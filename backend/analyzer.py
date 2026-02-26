"""
Strands agent that orchestrates modernization analysis.
"""
import os
import json
from typing import AsyncIterator

from strands import Agent
from strands.models.bedrock import BedrockModel

from tools import (
    clone_repository,
    list_repository_files,
    read_file_content,
    detect_tech_stack,
    cleanup_repository,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert software modernization consultant with deep knowledge of:
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

# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------

def build_model(model_id: str, region: str) -> BedrockModel:
    return BedrockModel(
        model_id=model_id,
        region_name=region,
        # streaming is enabled by default in Strands BedrockModel
    )


# ---------------------------------------------------------------------------
# Analysis runner
# ---------------------------------------------------------------------------

async def run_analysis(
    gitlab_url: str,
    auth_type: str,
    credential: str,
    model_id: str,
    aws_region: str,
    branch: str = "main",
) -> AsyncIterator[str]:
    """
    Run the modernization analysis and yield SSE-formatted chunks.
    Each yielded string is a complete 'data: ...\n\n' SSE event.
    """

    def sse(event: str, data: str) -> str:
        payload = json.dumps({"event": event, "data": data})
        return f"data: {payload}\n\n"

    yield sse("status", "Initializing analysis agent...")

    try:
        model = build_model(model_id, aws_region)
    except Exception as exc:
        yield sse("error", f"Failed to initialize model: {exc}")
        return

    agent = Agent(
        model=model,
        tools=[
            clone_repository,
            list_repository_files,
            read_file_content,
            detect_tech_stack,
            cleanup_repository,
        ],
        system_prompt=SYSTEM_PROMPT,
    )

    prompt = (
        f"Please analyze the GitLab repository at: {gitlab_url}\n"
        f"Authentication type: {auth_type}\n"
        f"Credential: {credential}\n"
        f"Branch: {branch}\n\n"
        "Begin by cloning the repository, then perform a thorough modernization analysis "
        "following the instructions in your system prompt. Clean up the repository when done."
    )

    yield sse("status", "Agent started — cloning repository...")

    # Strands Agent supports streaming via async iteration
    try:
        collected_text = []
        async for chunk in agent.stream_async(prompt):
            # Strands yields different event types; we care about text deltas
            if hasattr(chunk, "text") and chunk.text:
                collected_text.append(chunk.text)
                yield sse("chunk", chunk.text)
            elif hasattr(chunk, "tool_use"):
                tool_name = getattr(chunk.tool_use, "name", "tool")
                yield sse("tool_use", f"Using tool: {tool_name}")
            elif hasattr(chunk, "tool_result"):
                yield sse("tool_result", "Tool completed")

        yield sse("done", "".join(collected_text))

    except Exception as exc:
        yield sse("error", f"Analysis failed: {exc}")
