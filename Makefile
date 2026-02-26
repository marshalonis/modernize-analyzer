# =============================================================================
# Modernization Analyzer — Makefile
# =============================================================================
.PHONY: help build build-frontend build-backend push push-frontend push-backend \
        local local-down bootstrap deploy deploy-ecr diff destroy update \
        update-frontend update-backend logs-frontend logs-backend

AWS_REGION   ?= us-east-1
AWS_PROFILE  ?=
PROFILE_ARG  := $(if $(AWS_PROFILE),--profile $(AWS_PROFILE),)

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

local:           ## Start local stack with docker compose
	docker compose up --build

local-down:      ## Stop local stack
	docker compose down

# ---------------------------------------------------------------------------
# Docker build
# ---------------------------------------------------------------------------

build: build-frontend build-backend  ## Build both images

build-frontend:  ## Build frontend image
	docker build --platform linux/amd64 -t modernizer-frontend:latest ./frontend

build-backend:   ## Build backend image
	docker build --platform linux/amd64 -t modernizer-backend:latest ./backend

# ---------------------------------------------------------------------------
# ECR push (requires images to be built)
# ---------------------------------------------------------------------------

_ecr-login:
	@ACCOUNT=$$(aws sts get-caller-identity $(PROFILE_ARG) --query Account --output text) && \
	aws ecr get-login-password --region $(AWS_REGION) $(PROFILE_ARG) | \
	  docker login --username AWS --password-stdin $${ACCOUNT}.dkr.ecr.$(AWS_REGION).amazonaws.com

_ecr-repos:
	$(eval FRONTEND_REPO := $(shell aws ssm get-parameter $(PROFILE_ARG) --region $(AWS_REGION) --name /modernizer/frontend-ecr-uri --query Parameter.Value --output text))
	$(eval BACKEND_REPO  := $(shell aws ssm get-parameter $(PROFILE_ARG) --region $(AWS_REGION) --name /modernizer/backend-ecr-uri  --query Parameter.Value --output text))

push: _ecr-login _ecr-repos push-frontend push-backend  ## Build and push both images to ECR

push-frontend: _ecr-login _ecr-repos build-frontend  ## Build and push frontend to ECR
	docker tag modernizer-frontend:latest $(FRONTEND_REPO):latest
	docker push $(FRONTEND_REPO):latest

push-backend: _ecr-login _ecr-repos build-backend  ## Build and push backend to ECR
	docker tag modernizer-backend:latest $(BACKEND_REPO):latest
	docker push $(BACKEND_REPO):latest

# ---------------------------------------------------------------------------
# CDK
# ---------------------------------------------------------------------------

bootstrap:       ## Bootstrap CDK in the target account/region
	cd cdk && pip install -r requirements.txt -q
	cd cdk && cdk bootstrap $(PROFILE_ARG)

deploy-ecr:      ## Deploy only ECR repositories (first-time only, before pushing images)
	cd cdk && cdk deploy ModernizerEcr $(PROFILE_ARG) --require-approval never

deploy:          ## Deploy all CDK stacks
	cd cdk && cdk deploy --all $(PROFILE_ARG) --require-approval never

diff:            ## Show CDK diff
	cd cdk && cdk diff --all $(PROFILE_ARG)

destroy:         ## DANGER: destroy all CDK stacks
	cd cdk && cdk destroy --all $(PROFILE_ARG)

# ---------------------------------------------------------------------------
# Post-deploy container updates
# ---------------------------------------------------------------------------

update:          ## Rebuild, push, and force-redeploy both services
	AWS_REGION=$(AWS_REGION) ./scripts/update.sh all

update-frontend: ## Rebuild, push, and force-redeploy frontend only
	AWS_REGION=$(AWS_REGION) ./scripts/update.sh frontend

update-backend:  ## Rebuild, push, and force-redeploy backend only
	AWS_REGION=$(AWS_REGION) ./scripts/update.sh backend

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

logs-frontend:   ## Tail frontend ECS logs (last 100 lines)
	aws logs tail /ecs/modernizer-frontend $(PROFILE_ARG) --region $(AWS_REGION) --follow

logs-backend:    ## Tail backend ECS logs (last 100 lines)
	aws logs tail /ecs/modernizer-backend $(PROFILE_ARG) --region $(AWS_REGION) --follow
