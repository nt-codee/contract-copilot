#!/usr/bin/env bash
# Build the repo-root Dockerfile with Cloud Build, then roll it out with the app stack.
# Prerequisite: foundation is applied (registry + service account + channel exist).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GCP="${ROOT}/infrastructure/gcp"
# shellcheck source=../infrastructure/gcp/env.sh
source "${GCP}/env.sh"

stage() { printf '\n== [%s] %s\n' "$1" "$2"; }

stage 1/5 "resolve image tag"
[ -f "${ROOT}/Dockerfile" ] || { echo "no Dockerfile at ${ROOT}/Dockerfile" >&2; exit 1; }
if git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1 && [ -z "$(git -C "$ROOT" status --porcelain)" ]; then
  TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
else
  TAG="dirty-$(date +%Y%m%d-%H%M%S)"
fi
REGISTRY="$(cd "${GCP}/foundation" && pulumi stack output registry_path --stack demo)"
IMAGE="${REGISTRY}/contract-copilot:${TAG}"
echo "image: ${IMAGE}"

stage 2/5 "cloud build"
gcloud builds submit "$ROOT" --project "$PROJECT_ID" --region "$REGION" --tag "$IMAGE"

stage 3/5 "pulumi up (app)"
cd "${GCP}/app"
pulumi config set image "$IMAGE" --stack demo
pulumi config set appVersion "$TAG" --stack demo   # becomes APP_VERSION on the service (review provenance)
pulumi up --yes --stack demo --non-interactive

stage 4/5 "service url"
URL="$(pulumi stack output service_url --stack demo)"
echo "url: ${URL}"

stage 5/5 "health check ${URL}/api/health"
for i in $(seq 1 12); do
  if body="$(curl -fsS --max-time 10 "${URL}/api/health" 2>/dev/null)"; then
    echo "healthy: ${body}"; exit 0
  fi
  echo "attempt ${i}/12 not ready, retrying in 5s"; sleep 5
done
echo "healthz did not return 200 within 60 s" >&2
exit 1
