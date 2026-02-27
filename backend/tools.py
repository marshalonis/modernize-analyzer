# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Dave Marshalonis
# See LICENSE file in the project root for full license text.

"""
Strands agent tools for GitLab repo cloning and code analysis.
"""
import os
import stat
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Optional
from strands import tool


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _write_ssh_key(private_key: str) -> str:
    """Write SSH private key to a temp file; return path."""
    fd, path = tempfile.mkstemp(prefix="gl_ssh_", suffix=".pem")
    with os.fdopen(fd, "w") as f:
        key = private_key.strip()
        if not key.endswith("\n"):
            key += "\n"
        f.write(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def _run(cmd: list[str], env: Optional[dict] = None, cwd: Optional[str] = None) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Tool: clone
# ---------------------------------------------------------------------------

@tool
def clone_repository(
    url: str,
    auth_type: str,
    credential: str,
    branch: str = "main",
) -> str:
    """
    Clone a GitLab repository to a temporary directory.

    Args:
        url: GitLab repository URL (HTTPS or SSH).
        auth_type: 'pat' for Personal Access Token, 'ssh' for SSH private key.
        credential: The PAT string or SSH private key PEM content.
        branch: Branch to clone (default: main).

    Returns:
        JSON string with 'repo_path' on success or 'error' on failure.
    """
    dest = tempfile.mkdtemp(prefix="modernizer_repo_")

    try:
        if auth_type == "pat":
            # Inject PAT into HTTPS URL: https://oauth2:<token>@gitlab.com/...
            if "://" in url:
                proto, rest = url.split("://", 1)
                authed_url = f"{proto}://oauth2:{credential}@{rest}"
            else:
                return json.dumps({"error": f"Cannot inject PAT into URL: {url}"})

            rc, out, err = _run(
                ["git", "clone", "--depth", "1", "--branch", branch, authed_url, dest]
            )
        elif auth_type == "ssh":
            key_path = _write_ssh_key(credential)
            try:
                ssh_cmd = (
                    f"ssh -i {key_path} -o StrictHostKeyChecking=no "
                    f"-o UserKnownHostsFile=/dev/null"
                )
                rc, out, err = _run(
                    ["git", "clone", "--depth", "1", "--branch", branch, url, dest],
                    env={"GIT_SSH_COMMAND": ssh_cmd},
                )
            finally:
                os.unlink(key_path)
        else:
            return json.dumps({"error": f"Unknown auth_type: {auth_type}"})

        if rc != 0:
            # Try without branch in case default branch differs
            if auth_type == "pat":
                rc, out, err = _run(["git", "clone", "--depth", "1", authed_url, dest])
            else:
                rc, out, err = _run(
                    ["git", "clone", "--depth", "1", url, dest],
                    env={"GIT_SSH_COMMAND": ssh_cmd},
                )

        if rc != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return json.dumps({"error": err.strip()})

        return json.dumps({"repo_path": dest})

    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: list files
# ---------------------------------------------------------------------------

@tool
def list_repository_files(repo_path: str, max_files: int = 300) -> str:
    """
    Return a recursive listing of all files in the repository, excluding
    .git and common binary/generated directories.

    Args:
        repo_path: Absolute path to the cloned repo.
        max_files: Maximum number of files to return.

    Returns:
        JSON list of relative file paths.
    """
    skip_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".nuxt", "vendor", "target",
        "bin", "obj", ".gradle", ".mvn",
    }
    skip_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
        ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".tar",
        ".gz", ".lock",
    }

    files: list[str] = []
    root = Path(repo_path)

    for p in root.rglob("*"):
        if p.is_file():
            parts = set(p.relative_to(root).parts)
            if parts & skip_dirs:
                continue
            if p.suffix.lower() in skip_exts:
                continue
            files.append(str(p.relative_to(root)))
            if len(files) >= max_files:
                break

    return json.dumps({"files": files, "total": len(files)})


# ---------------------------------------------------------------------------
# Tool: read file
# ---------------------------------------------------------------------------

@tool
def read_file_content(repo_path: str, relative_path: str, max_lines: int = 300) -> str:
    """
    Read the content of a specific file within the repository.

    Args:
        repo_path: Absolute path to the cloned repo.
        relative_path: Relative path of the file within the repo.
        max_lines: Maximum lines to return (truncates long files).

    Returns:
        JSON with 'content' field or 'error'.
    """
    target = Path(repo_path) / relative_path
    try:
        target.resolve().relative_to(Path(repo_path).resolve())  # path traversal guard
    except ValueError:
        return json.dumps({"error": "Path traversal detected"})

    if not target.exists():
        return json.dumps({"error": f"File not found: {relative_path}"})
    if not target.is_file():
        return json.dumps({"error": f"Not a file: {relative_path}"})

    try:
        lines = target.read_text(errors="replace").splitlines()
        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])
        return json.dumps({
            "content": content,
            "lines_shown": min(len(lines), max_lines),
            "total_lines": len(lines),
            "truncated": truncated,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: detect tech stack
# ---------------------------------------------------------------------------

@tool
def detect_tech_stack(repo_path: str) -> str:
    """
    Detect languages, frameworks, and tooling used in the repository by
    inspecting well-known manifest and config files.

    Args:
        repo_path: Absolute path to the cloned repo.

    Returns:
        JSON dict with detected stack information.
    """
    root = Path(repo_path)
    stack: dict = {
        "languages": [],
        "frameworks": [],
        "build_tools": [],
        "ci_cd": [],
        "containerization": [],
        "package_managers": [],
        "manifest_files": [],
    }

    checks = {
        # Language signals
        "languages": {
            "Python": ["*.py", "requirements*.txt", "setup.py", "pyproject.toml", "Pipfile"],
            "JavaScript": ["*.js", "*.mjs", "package.json"],
            "TypeScript": ["*.ts", "*.tsx", "tsconfig.json"],
            "Java": ["*.java", "pom.xml", "build.gradle"],
            "Go": ["*.go", "go.mod"],
            "Ruby": ["*.rb", "Gemfile"],
            "PHP": ["*.php", "composer.json"],
            "C#": ["*.cs", "*.csproj"],
            "Rust": ["*.rs", "Cargo.toml"],
        },
        "frameworks": {
            "React": ["package.json"],
            "Vue": ["package.json"],
            "Angular": ["angular.json"],
            "Django": ["manage.py", "django"],
            "Flask": ["app.py", "wsgi.py"],
            "FastAPI": ["requirements*.txt"],
            "Spring Boot": ["pom.xml", "build.gradle"],
            "Next.js": ["next.config.js", "next.config.ts"],
            "Express": ["package.json"],
        },
    }

    manifest_names = [
        "package.json", "requirements.txt", "requirements-dev.txt",
        "pyproject.toml", "Pipfile", "pom.xml", "build.gradle",
        "go.mod", "Gemfile", "composer.json", "Cargo.toml",
        "setup.py", "setup.cfg",
    ]
    ci_names = [
        ".gitlab-ci.yml", ".github/workflows", "Jenkinsfile",
        ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml",
    ]
    container_names = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                       "kubernetes", "k8s", "helm", ".helm"]

    found_manifests = []
    for name in manifest_names:
        if (root / name).exists():
            found_manifests.append(name)
    stack["manifest_files"] = found_manifests

    # Language detection by file extension presence
    ext_lang_map = {
        ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
        ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
        ".go": "Go", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".rs": "Rust",
    }
    found_exts: set[str] = set()
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            found_exts.add(p.suffix.lower())
    for ext, lang in ext_lang_map.items():
        if ext in found_exts and lang not in stack["languages"]:
            stack["languages"].append(lang)

    # Framework signals from package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for fw in ["react", "vue", "angular", "@angular/core", "svelte",
                       "next", "nuxt", "express", "koa", "fastify"]:
                if fw in deps or f"@{fw}" in deps:
                    label = fw.replace("@angular/core", "Angular").replace("@", "").title()
                    if label not in stack["frameworks"]:
                        stack["frameworks"].append(label)
        except Exception:
            pass

    # Python framework signals
    for req_file in root.glob("requirements*.txt"):
        try:
            content = req_file.read_text().lower()
            for fw in ["django", "flask", "fastapi", "tornado", "pyramid", "falcon"]:
                if fw in content and fw.title() not in stack["frameworks"]:
                    stack["frameworks"].append(fw.title())
        except Exception:
            pass

    # CI/CD
    for name in ci_names:
        if (root / name).exists():
            stack["ci_cd"].append(name)

    # Containerization
    for name in container_names:
        if (root / name).exists():
            stack["containerization"].append(name)

    # Build tools
    build_signals = {
        "Maven": "pom.xml", "Gradle": "build.gradle",
        "Make": "Makefile", "npm": "package-lock.json",
        "yarn": "yarn.lock", "pnpm": "pnpm-lock.yaml",
        "Poetry": "pyproject.toml",
    }
    for tool_name, fname in build_signals.items():
        if (root / fname).exists():
            stack["build_tools"].append(tool_name)

    return json.dumps(stack)


# ---------------------------------------------------------------------------
# Tool: cleanup
# ---------------------------------------------------------------------------

@tool
def cleanup_repository(repo_path: str) -> str:
    """
    Delete a cloned repository from disk after analysis is complete.

    Args:
        repo_path: Absolute path to the cloned repo.

    Returns:
        JSON with 'status'.
    """
    try:
        shutil.rmtree(repo_path, ignore_errors=True)
        return json.dumps({"status": "deleted", "path": repo_path})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})
