# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Dave Marshalonis
# See LICENSE file in the project root for full license text.

"""
Strands agent that orchestrates modernization analysis.
"""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from typing import AsyncIterator

from strands import Agent
from strands.models.bedrock import BedrockModel

from tools import (
    cleanup_repository,
    clone_repository,
    detect_tech_stack,
    list_repository_files,
    read_file_content,
)

# ---------------------------------------------------------------------------
# System prompt
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

# Shared thread pool for blocking agent calls
_executor = ThreadPoolExecutor(max_workers=4)

# Sentinel to signal the queue is done
_DONE = object()


# ---------------------------------------------------------------------------
# Callback handler — bridges Strands sync callbacks → async queue
# ---------------------------------------------------------------------------

class _StreamingHandler:
    """
    Passed to the Strands Agent as callback_handler.
    Strands calls this as a function with keyword arguments.
    Puts (event_type, data) tuples into a Queue so the async generator
    can forward them to the SSE stream.

    Strands kwargs of interest:
      data              – streaming text token
      complete          – True when the current message turn is complete
      current_tool_use  – dict with 'name' and 'input' when a tool is invoked
      tool_result_message – present when a tool has returned
    """

    def __init__(self, q: Queue) -> None:
        self._q = q

    def __call__(self, **kwargs) -> None:
        # Streaming text token
        data = kwargs.get("data")
        if data:
            self._q.put(("chunk", data))
            return

        # Tool invocation
        tool = kwargs.get("current_tool_use")
        if tool and isinstance(tool, dict) and not kwargs.get("complete"):
            tool_name = tool.get("name", "tool")
            self._q.put(("tool_use", f"Running tool: {tool_name}"))
            return

        # Tool result
        if kwargs.get("tool_result_message") is not None:
            self._q.put(("tool_result", "Tool completed"))


# ---------------------------------------------------------------------------
# Blocking agent runner (executed in thread pool)
# ---------------------------------------------------------------------------

def _run_agent_sync(
    model_id: str,
    aws_region: str,
    prompt: str,
    q: Queue,
    system_prompt: str = ANALYSIS_SYSTEM_PROMPT,
) -> str:
    """
    Builds and runs the Strands agent synchronously.
    Streaming tokens are pushed to `q` via the callback handler.
    Returns the full result text as a string.
    """
    model = BedrockModel(model_id=model_id, region_name=aws_region)
    handler = _StreamingHandler(q)

    agent = Agent(
        model=model,
        tools=[
            clone_repository,
            list_repository_files,
            read_file_content,
            detect_tech_stack,
            cleanup_repository,
        ],
        system_prompt=system_prompt,
        callback_handler=handler,
    )

    result = agent(prompt)
    return str(result)


# ---------------------------------------------------------------------------
# Public async generator
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
    Run the modernization analysis and yield SSE-formatted strings.
    Each yielded value is a complete 'data: ...\\n\\n' SSE event.
    """

    def sse(event: str, data: str) -> str:
        return f"data: {json.dumps({'event': event, 'data': data})}\n\n"

    yield sse("status", "Initializing analysis agent...")

    prompt = (
        f"Please analyze the GitLab repository at: {gitlab_url}\n"
        f"Authentication type: {auth_type}\n"
        f"Credential: {credential}\n"
        f"Branch: {branch}\n\n"
        "Begin by cloning the repository, then perform a thorough modernization analysis "
        "following the instructions in your system prompt. Clean up the repository when done."
    )

    q: Queue = Queue()
    loop = asyncio.get_event_loop()

    # Submit the blocking agent call to the thread pool
    future = loop.run_in_executor(
        _executor,
        _run_agent_sync,
        model_id,
        aws_region,
        prompt,
        q,
        ANALYSIS_SYSTEM_PROMPT,
    )

    yield sse("status", "Agent started — cloning repository...")

    # Drain the queue while the agent runs in the background thread
    collected: list[str] = []
    while not future.done():
        # Non-blocking drain: pull up to 20 items then yield control
        drained = 0
        while drained < 20:
            try:
                event_type, data = q.get_nowait()
                if event_type == "chunk":
                    collected.append(data)
                yield sse(event_type, data)
                drained += 1
            except Empty:
                break
        await asyncio.sleep(0.05)

    # Drain any remaining items after the future completes
    while not q.empty():
        try:
            event_type, data = q.get_nowait()
            if event_type == "chunk":
                collected.append(data)
            yield sse(event_type, data)
        except Empty:
            break

    # Get the final result from the future
    try:
        full_result = await future
    except Exception as exc:
        yield sse("error", f"Analysis failed: {exc}")
        return

    # If the model streamed tokens via on_llm_new_token, collected already has the
    # full text. If not (some Bedrock configs don't stream at token level),
    # full_result from the agent return value is the authoritative source.
    final_text = "".join(collected) if collected else full_result
    yield sse("done", final_text)


async def run_chat(
    gitlab_url: str,
    auth_type: str,
    credential: str,
    model_id: str,
    aws_region: str,
    question: str,
    branch: str = "main",
) -> AsyncIterator[str]:
    """
    Answer a free-form question about a repository and yield SSE-formatted strings.
    Each yielded value is a complete 'data: ...\\n\\n' SSE event.
    """

    def sse(event: str, data: str) -> str:
        return f"data: {json.dumps({'event': event, 'data': data})}\n\n"

    yield sse("status", "Initializing chat agent...")

    prompt = (
        f"The Git repository is at: {gitlab_url}\n"
        f"Authentication type: {auth_type}\n"
        f"Credential: {credential}\n"
        f"Branch: {branch}\n\n"
        f"Question: {question}\n\n"
        "Clone the repository, explore the relevant code needed to answer the question, "
        "provide a clear and specific answer, then clean up the repository."
    )

    q: Queue = Queue()
    loop = asyncio.get_event_loop()

    future = loop.run_in_executor(
        _executor,
        _run_agent_sync,
        model_id,
        aws_region,
        prompt,
        q,
        CHAT_SYSTEM_PROMPT,
    )

    yield sse("status", "Agent started — exploring repository...")

    collected: list[str] = []
    while not future.done():
        drained = 0
        while drained < 20:
            try:
                event_type, data = q.get_nowait()
                if event_type == "chunk":
                    collected.append(data)
                yield sse(event_type, data)
                drained += 1
            except Empty:
                break
        await asyncio.sleep(0.05)

    while not q.empty():
        try:
            event_type, data = q.get_nowait()
            if event_type == "chunk":
                collected.append(data)
            yield sse(event_type, data)
        except Empty:
            break

    try:
        full_result = await future
    except Exception as exc:
        yield sse("error", f"Chat failed: {exc}")
        return

    final_text = "".join(collected) if collected else full_result
    yield sse("done", final_text)
