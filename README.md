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

## Quick Start — Local Development

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
