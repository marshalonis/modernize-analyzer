# Modernization Analyzer

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

AI-powered GitLab repository modernization consultant.
Analyzes codebases and produces detailed modernization reports across:
- Code quality & patterns
- Architecture & infrastructure
- UI/UX modernization

## Architecture

```
Internet → Public ALB → Streamlit (ECS Fargate)
                              │ HTTP (internal)
                         Internal ALB → FastAPI + Strands Agent (ECS Fargate)
                                              │
                                         Amazon Bedrock (Claude)
                                              │
                                         GitLab repo (SSH or PAT)
```

## Quick Start — Standalone CLI (no Docker required)

Run `analyze.py` directly against any GitLab repo from your terminal.

**Prerequisites:** Python 3.10+, AWS credentials with Bedrock access, Claude model enabled in Bedrock.

### 1. Set up a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install strands-agents boto3
```

### 3. Run the analyzer

```bash
# Full modernization analysis using a Personal Access Token
export GITLAB_PAT=glpat-xxxx
python analyze.py https://gitlab.com/myorg/myrepo

# Pass the token directly
python analyze.py https://gitlab.com/myorg/myrepo --credential glpat-xxxx

# Analyze a specific branch and save the report
python analyze.py https://gitlab.com/myorg/myrepo --branch develop --output report.md

# Use SSH key authentication
python analyze.py git@gitlab.com:myorg/myrepo.git --auth-type ssh --credential ~/.ssh/id_rsa

# Ask a one-off question instead of a full analysis
python analyze.py https://gitlab.com/myorg/myrepo --question "What auth mechanism does this app use?"

# Override the model or AWS region
python analyze.py https://gitlab.com/myorg/myrepo \
    --model us.anthropic.claude-sonnet-4-5-20250929-v1:0 \
    --region us-east-1
```

**Credential resolution order** (when `--credential` is omitted):
- PAT mode: reads `GITLAB_PAT` or `GL_TOKEN` env var
- SSH mode: reads `GITLAB_SSH_KEY` or `GL_SSH_KEY` env var, then falls back to `~/.ssh/id_rsa`

Progress and tool activity stream to stderr; the report streams to stdout. Use `--output` to save a clean markdown file.

---

## Quick Start — Local Development (Docker)

**Prerequisites:** Docker, AWS credentials with Bedrock access, Claude model enabled in Bedrock.

```bash
# Start local stack
make local
# Open http://localhost:8501
```

To override the model:
```bash
DEFAULT_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0 make local
```

## Deployment to AWS

### 1. First-time setup

```bash
# Install CDK dependencies
cd cdk && pip install -r requirements.txt && cd ..

# Bootstrap CDK in your account
make bootstrap

# Deploy ECR repos (must exist before pushing images)
make deploy-ecr
```

### 2. Build and push container images

```bash
make push
```

### 3. Deploy all infrastructure

```bash
make deploy
```

The frontend URL is printed as a CDK output (`ModernizerEcs.FrontendUrl`).

### 4. Updating containers after code changes

```bash
# Update both
make update

# Update only backend
make update-backend

# Update only frontend
make update-frontend
```

This rebuilds the Docker image, pushes to ECR, and forces a new ECS deployment.

## Configuration

| Variable | Where | Default | Description |
|---|---|---|---|
| `DEFAULT_MODEL_ID` | Backend env / CDK context | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Bedrock model to use |
| `AWS_REGION` | CDK env / shell | `us-east-1` | AWS region |
| `BACKEND_URL` | Frontend env | `http://localhost:8000` | Internal URL of backend |

Override `defaultModelId` at deploy time:
```bash
cd cdk && cdk deploy --all --context defaultModelId=anthropic.claude-3-opus-20240229-v1:0
```

## IAM Requirements

The backend ECS task role is granted:
- `bedrock:InvokeModel`
- `bedrock:InvokeModelWithResponseStream`

No other permissions are required. The task runs in a private subnet with NAT Gateway egress for GitLab and Bedrock API access.

## Useful Commands

```bash
make logs-backend   # Tail backend CloudWatch logs
make logs-frontend  # Tail frontend CloudWatch logs
make diff           # Preview CDK changes before deploying
make destroy        # Tear down all infrastructure
```

## License

This project is released under the [MIT License](LICENSE).
© 2026 Dave Marshalonis. Provided as-is, without warranty of any kind.
