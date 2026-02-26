"""
FastAPI backend for the modernization analyzer.
Exposes a streaming SSE endpoint consumed by the Streamlit frontend.
"""
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analyzer import run_analysis

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ID = os.getenv(
    "DEFAULT_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Modernization Analyzer API",
    description="Analyzes GitLab repositories for modernization opportunities using AI.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production via ALLOWED_ORIGINS env var
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    gitlab_url: str = Field(..., description="Full GitLab repository URL")
    auth_type: str = Field(..., description="'pat' or 'ssh'")
    credential: str = Field(..., description="PAT string or SSH private key PEM")
    branch: str = Field("main", description="Branch to analyze")
    model_id: str = Field(
        default="",
        description="Bedrock model ID (empty = use server default)",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "default_model": DEFAULT_MODEL_ID, "region": AWS_REGION}


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """
    Stream a modernization analysis as Server-Sent Events.

    Each event is a JSON object:
      { "event": "status"|"chunk"|"tool_use"|"tool_result"|"done"|"error",
        "data": "<string>" }
    """
    if not request.gitlab_url.strip():
        raise HTTPException(status_code=400, detail="gitlab_url is required")
    if request.auth_type not in ("pat", "ssh"):
        raise HTTPException(status_code=400, detail="auth_type must be 'pat' or 'ssh'")
    if not request.credential.strip():
        raise HTTPException(status_code=400, detail="credential is required")

    effective_model = request.model_id.strip() or DEFAULT_MODEL_ID

    return StreamingResponse(
        run_analysis(
            gitlab_url=request.gitlab_url,
            auth_type=request.auth_type,
            credential=request.credential,
            model_id=effective_model,
            aws_region=AWS_REGION,
            branch=request.branch,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if behind proxy
        },
    )


@app.get("/models")
def list_models():
    """Return the available Bedrock Claude model IDs."""
    return {
        "default": DEFAULT_MODEL_ID,
        "available": [
            {
                "id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "label": "Claude 3.5 Sonnet (recommended)",
            },
            {
                "id": "anthropic.claude-3-5-haiku-20241022-v1:0",
                "label": "Claude 3.5 Haiku (faster / cheaper)",
            },
            {
                "id": "anthropic.claude-3-opus-20240229-v1:0",
                "label": "Claude 3 Opus (most capable)",
            },
        ],
    }
