#!/usr/bin/env bash
# =============================================================================
# update.sh — Build, push, and redeploy modernizer containers
#
# Usage:
#   ./scripts/update.sh [frontend|backend|all]
#
# Prerequisites:
#   - AWS CLI configured (profile or env vars)
#   - Docker running
#   - Infrastructure already deployed via CDK
# =============================================================================
set -euo pipefail

COMPONENT="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Resolve config from SSM (set by CDK) or environment overrides
# ---------------------------------------------------------------------------
AWS_REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_BASE="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

ssm_get() {
  aws ssm get-parameter --region "${AWS_REGION}" --name "$1" --query Parameter.Value --output text
}

echo "📦 Fetching deployment config from SSM..."
FRONTEND_REPO=$(ssm_get /modernizer/frontend-ecr-uri)
BACKEND_REPO=$(ssm_get /modernizer/backend-ecr-uri)
CLUSTER=$(ssm_get /modernizer/cluster-name)
FRONTEND_SERVICE=$(ssm_get /modernizer/frontend-service)
BACKEND_SERVICE=$(ssm_get /modernizer/backend-service)

echo "  Cluster:          ${CLUSTER}"
echo "  Frontend service: ${FRONTEND_SERVICE}"
echo "  Backend service:  ${BACKEND_SERVICE}"
echo "  Frontend ECR:     ${FRONTEND_REPO}"
echo "  Backend ECR:      ${BACKEND_REPO}"
echo ""

# ---------------------------------------------------------------------------
# ECR login
# ---------------------------------------------------------------------------
echo "🔐 Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${ECR_BASE}"

# ---------------------------------------------------------------------------
# Build + push + redeploy helpers
# ---------------------------------------------------------------------------
build_and_push() {
  local component="$1"
  local repo="$2"
  local context_dir="${PROJECT_ROOT}/${component}"

  echo ""
  echo "🔨 Building ${component}..."
  docker build --platform linux/amd64 -t "${repo}:latest" "${context_dir}"

  echo "⬆  Pushing ${component}..."
  docker push "${repo}:latest"
}

force_redeploy() {
  local service="$1"
  echo "🚀 Forcing new deployment for ${service}..."
  aws ecs update-service \
    --cluster "${CLUSTER}" \
    --service "${service}" \
    --force-new-deployment \
    --region "${AWS_REGION}" \
    --output text --query "service.serviceName"
}

wait_for_stable() {
  local service="$1"
  echo "⏳ Waiting for ${service} to stabilize..."
  aws ecs wait services-stable \
    --cluster "${CLUSTER}" \
    --services "${service}" \
    --region "${AWS_REGION}"
  echo "✅ ${service} is stable."
}

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
case "${COMPONENT}" in
  frontend)
    build_and_push frontend "${FRONTEND_REPO}"
    force_redeploy "${FRONTEND_SERVICE}"
    wait_for_stable "${FRONTEND_SERVICE}"
    ;;
  backend)
    build_and_push backend "${BACKEND_REPO}"
    force_redeploy "${BACKEND_SERVICE}"
    wait_for_stable "${BACKEND_SERVICE}"
    ;;
  all)
    build_and_push frontend "${FRONTEND_REPO}"
    build_and_push backend "${BACKEND_REPO}"
    force_redeploy "${FRONTEND_SERVICE}"
    force_redeploy "${BACKEND_SERVICE}"
    wait_for_stable "${FRONTEND_SERVICE}"
    wait_for_stable "${BACKEND_SERVICE}"
    ;;
  *)
    echo "Usage: $0 [frontend|backend|all]" >&2
    exit 1
    ;;
esac

echo ""
echo "🎉 Update complete!"
