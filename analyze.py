#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Dave Marshalonis
# See LICENSE file in the project root for full license text.

"""
Standalone CLI for the modernization analyzer.

Runs the same Strands agent used in the Docker backend, but directly from
the command line — no Docker, no FastAPI, no streaming overhead.

Usage examples:

  # Analyze with a PAT (token from env var)
  export GITLAB_PAT=glpat-xxxx
  python analyze.py https://gitlab.com/myorg/myrepo

  # Pass the token directly
  python analyze.py https://gitlab.com/myorg/myrepo --credential glpat-xxxx

  # Use SSH key
  python analyze.py git@gitlab.com:myorg/myrepo.git --auth-type ssh --credential ~/.ssh/id_rsa

  # Specify branch, model, region, and save output
  python analyze.py https://gitlab.com/myorg/myrepo \\
      --branch develop \\
      --model us.anthropic.claude-sonnet-4-5-20250929-v1:0 \\
      --region us-east-1 \\
      --output report.md

  # Ask a one-off question instead of a full analysis
  python analyze.py https://gitlab.com/myorg/myrepo \\
      --question "What authentication mechanism does this app use?"

Requirements (pip install):
  strands-agents boto3

AWS credentials must be configured (e.g., via AWS_PROFILE, IAM role, or
environment variables) with Bedrock InvokeModel permissions.
"""

import argparse
import os
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Inline tools (mirrors backend/tools.py — no backend package needed)
# ---------------------------------------------------------------------------

import json
import shutil
import stat
import subprocess
import tempfile
from typing import Optional

from strands import tool


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


def _run(cmd: list, env: Optional[dict] = None, cwd: Optional[str] = None):
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
    )
    return result.returncode, result.stdout, result.stderr


@tool
def clone_repository(url: str, auth_type: str, credential: str, branch: str = "main") -> str:
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
            if "://" in url:
                proto, rest = url.split("://", 1)
                authed_url = f"{proto}://oauth2:{credential}@{rest}"
            else:
                return json.dumps({"error": f"Cannot inject PAT into URL: {url}"})

            rc, out, err = _run(["git", "clone", "--depth", "1", "--branch", branch, authed_url, dest])
            if rc != 0:
                rc, out, err = _run(["git", "clone", "--depth", "1", authed_url, dest])

        elif auth_type == "ssh":
            # If credential is a file path, read the key from disk
            cred = credential.strip()
            if cred.startswith("~") or (cred.startswith("/") and Path(cred).exists()):
                key_content = Path(cred).expanduser().read_text()
            else:
                key_content = cred

            key_path = _write_ssh_key(key_content)
            try:
                ssh_cmd = f"ssh -i {key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
                rc, out, err = _run(
                    ["git", "clone", "--depth", "1", "--branch", branch, url, dest],
                    env={"GIT_SSH_COMMAND": ssh_cmd},
                )
                if rc != 0:
                    rc, out, err = _run(
                        ["git", "clone", "--depth", "1", url, dest],
                        env={"GIT_SSH_COMMAND": ssh_cmd},
                    )
            finally:
                os.unlink(key_path)
        else:
            return json.dumps({"error": f"Unknown auth_type: {auth_type}"})

        if rc != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return json.dumps({"error": err.strip()})

        return json.dumps({"repo_path": dest})

    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return json.dumps({"error": str(exc)})


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

    files: list = []
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
        target.resolve().relative_to(Path(repo_path).resolve())
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

    stack["manifest_files"] = [n for n in manifest_names if (root / n).exists()]

    ext_lang_map = {
        ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
        ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
        ".go": "Go", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".rs": "Rust",
    }
    found_exts: set = set()
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            found_exts.add(p.suffix.lower())
    for ext, lang in ext_lang_map.items():
        if ext in found_exts and lang not in stack["languages"]:
            stack["languages"].append(lang)

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for fw in ["react", "vue", "angular", "@angular/core", "svelte",
                       "next", "nuxt", "express", "koa", "fastify"]:
                if fw in deps:
                    label = fw.replace("@angular/core", "Angular").replace("@", "").title()
                    if label not in stack["frameworks"]:
                        stack["frameworks"].append(label)
        except Exception:
            pass

    for req_file in root.glob("requirements*.txt"):
        try:
            content = req_file.read_text().lower()
            for fw in ["django", "flask", "fastapi", "tornado", "pyramid", "falcon"]:
                if fw in content and fw.title() not in stack["frameworks"]:
                    stack["frameworks"].append(fw.title())
        except Exception:
            pass

    stack["ci_cd"] = [n for n in ci_names if (root / n).exists()]
    stack["containerization"] = [n for n in container_names if (root / n).exists()]

    build_signals = {
        "Maven": "pom.xml", "Gradle": "build.gradle",
        "Make": "Makefile", "npm": "package-lock.json",
        "yarn": "yarn.lock", "pnpm": "pnpm-lock.yaml",
        "Poetry": "pyproject.toml",
    }
    stack["build_tools"] = [t for t, f in build_signals.items() if (root / f).exists()]

    return json.dumps(stack)


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


# ---------------------------------------------------------------------------
# System prompts (same as backend/analyzer.py)
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """You are an expert software modernization consultant with deep knowledge of:
- Modern software architecture patterns (microservices, serverless, event-driven)
- Frontend frameworks and UI/UX best practices
- Backend technologies, APIs, and database patterns
- Cloud-native development and containerization
- CI/CD, DevOps, and developer experience
- Security best practices
- Performance optimization
- Dependency management and technical debt

You have been given tools to clone and inspect a GitLab repository.

Your analysis MUST cover these categories (only those applicable to the codebase):

## 1. Code Quality & Modernization
- Outdated language features or deprecated APIs
- Anti-patterns, code smells, and refactoring opportunities
- Test coverage signals (presence/absence of tests, testing frameworks)
- Documentation quality

## 2. Architecture & Infrastructure
- Monolith vs service decomposition opportunities
- Containerization readiness (Docker, Kubernetes)
- CI/CD maturity and pipeline gaps
- Infrastructure-as-code adoption
- Configuration management (hardcoded values, env vars, secrets management)

## 3. UI/UX Modernization
- Frontend framework age and upgrade paths
- Accessibility signals
- Responsive design
- State management patterns
- Build toolchain modernization (webpack → vite, etc.)

## Analysis approach:
1. Start by detecting the tech stack
2. List repository files to understand structure
3. Read key files: entry points, configuration, package manifests, CI config, a sample of source files
4. Synthesize your findings into actionable recommendations

## Output format:
Structure your final output as a clear markdown report with:
- **Executive Summary** (3-5 sentences)
- **Tech Stack Detected** (table)
- **Findings by Category** (each finding: severity [High/Medium/Low], description, recommended action)
- **Modernization Roadmap** (Quick wins vs Strategic changes)
- **Estimated Effort** (rough T-shirt sizing per recommendation)

Be specific. Reference actual file names and code patterns you observed. Avoid generic advice.
"""

CHAT_SYSTEM_PROMPT = """You are an expert software engineer helping users understand a codebase.
You have tools to clone and inspect a Git repository.

When answering questions:
1. Clone the repository first using the provided credentials
2. Use the available tools to explore the relevant parts of the codebase needed to answer the question
3. Give a clear, specific answer based on what you actually find in the code
4. Reference actual file names and line numbers when relevant
5. Be concise but thorough — answer the question directly, don't produce a full analysis report
6. Clean up the repository when done

Only explore the parts of the codebase relevant to the question asked.
"""

AGENT_TOOLS = [
    clone_repository,
    list_repository_files,
    read_file_content,
    detect_tech_stack,
    cleanup_repository,
]


# ---------------------------------------------------------------------------
# Printing callback — streams agent output to the terminal in real time
# ---------------------------------------------------------------------------

class _PrintingHandler:
    """
    Strands callback_handler that prints tokens as they arrive and logs
    tool usage to stderr so the report stays clean on stdout.
    """

    def __call__(self, **kwargs) -> None:
        data = kwargs.get("data")
        if data:
            print(data, end="", flush=True)
            return

        tool = kwargs.get("current_tool_use")
        if tool and isinstance(tool, dict) and not kwargs.get("complete"):
            tool_name = tool.get("name", "tool")
            print(f"\n[tool: {tool_name}]", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(
    gitlab_url: str,
    auth_type: str,
    credential: str,
    branch: str,
    model_id: str,
    aws_region: str,
    question: Optional[str],
    output_file: Optional[str],
) -> None:
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    model = BedrockModel(model_id=model_id, region_name=aws_region)
    handler = _PrintingHandler()

    system_prompt = CHAT_SYSTEM_PROMPT if question else ANALYSIS_SYSTEM_PROMPT

    agent = Agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        callback_handler=handler,
    )

    if question:
        prompt = (
            f"The Git repository is at: {gitlab_url}\n"
            f"Authentication type: {auth_type}\n"
            f"Credential: {credential}\n"
            f"Branch: {branch}\n\n"
            f"Question: {question}\n\n"
            "Clone the repository, explore the relevant code needed to answer the question, "
            "provide a clear and specific answer, then clean up the repository."
        )
    else:
        prompt = (
            f"Please analyze the GitLab repository at: {gitlab_url}\n"
            f"Authentication type: {auth_type}\n"
            f"Credential: {credential}\n"
            f"Branch: {branch}\n\n"
            "Begin by cloning the repository, then perform a thorough modernization analysis "
            "following the instructions in your system prompt. Clean up the repository when done."
        )

    print(f"Analyzing {gitlab_url} (branch: {branch}) ...\n", file=sys.stderr)
    result = agent(prompt)
    report = str(result)

    # Newline after streamed content
    print()

    if output_file:
        Path(output_file).write_text(report)
        print(f"\nReport saved to: {output_file}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_credential(args) -> str:
    """Return credential string, reading from file or env var as needed."""
    if args.credential:
        cred = args.credential.strip()
        # If it looks like a file path, pass as-is (clone_repository handles it for SSH)
        return cred

    if args.auth_type == "pat":
        token = os.environ.get("GITLAB_PAT") or os.environ.get("GL_TOKEN")
        if token:
            return token.strip()
        sys.exit(
            "Error: no credential provided. Pass --credential or set GITLAB_PAT env var."
        )
    else:  # ssh
        key_path = os.environ.get("GITLAB_SSH_KEY") or os.environ.get("GL_SSH_KEY")
        if key_path:
            return key_path.strip()
        # Fall back to default SSH key
        default = Path("~/.ssh/id_rsa").expanduser()
        if default.exists():
            return str(default)
        sys.exit(
            "Error: no SSH key provided. Pass --credential <path> or set GITLAB_SSH_KEY env var."
        )


def main():
    parser = argparse.ArgumentParser(
        prog="analyze.py",
        description="AI-powered GitLab repository modernization analyzer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            environment variables:
              GITLAB_PAT        Personal Access Token (used when --auth-type pat and no --credential)
              GITLAB_SSH_KEY    Path to SSH private key (used when --auth-type ssh and no --credential)
              AWS_PROFILE       AWS credentials profile
              AWS_REGION        AWS region (overridden by --region)
        """),
    )

    parser.add_argument("url", help="GitLab repository URL (HTTPS or SSH)")
    parser.add_argument(
        "--auth-type",
        choices=["pat", "ssh"],
        default="pat",
        help="Authentication type (default: pat)",
    )
    parser.add_argument(
        "--credential",
        metavar="TOKEN_OR_KEY_PATH",
        help="PAT token string, SSH key PEM content, or path to SSH key file. "
             "Falls back to GITLAB_PAT / GITLAB_SSH_KEY env vars.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Branch to analyze (default: main)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "DEFAULT_MODEL_ID",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        ),
        metavar="MODEL_ID",
        help="Bedrock model ID (default: claude-sonnet-4-5)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region for Bedrock (default: us-east-1)",
    )
    parser.add_argument(
        "--question", "-q",
        metavar="QUESTION",
        help="Ask a specific question instead of running a full analysis",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save the report/answer to a file (markdown)",
    )

    args = parser.parse_args()
    credential = _resolve_credential(args)

    run(
        gitlab_url=args.url,
        auth_type=args.auth_type,
        credential=credential,
        branch=args.branch,
        model_id=args.model,
        aws_region=args.region,
        question=args.question,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
