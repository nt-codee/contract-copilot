#!/usr/bin/env bash
# Destroy the app stack, then foundation. Never deletes the project: it prints that command instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GCP="${ROOT}/infrastructure/gcp"
# shellcheck source=../infrastructure/gcp/env.sh
source "${GCP}/env.sh"

echo "This DESTROYS the Cloud Run service, uploads bucket (with its contents), Firestore database,"
echo "registry (with its images), alerts, budget and service account in project ${PROJECT_ID}."
read -r -p "type the project id to continue: " answer
[ "$answer" = "$PROJECT_ID" ] || { echo "no match, aborting"; exit 1; }

echo; echo "== [1/3] destroy app"
(cd "${GCP}/app" && pulumi destroy --yes --stack demo --non-interactive)

echo; echo "== [2/3] destroy foundation"
(cd "${GCP}/foundation" && pulumi destroy --yes --stack demo --non-interactive)

echo; echo "== [3/3] project deletion is manual. NOT run by this script. To remove the whole project"
echo "   (state bucket included; 30-day recovery window), run it yourself:"
echo
echo "   gcloud projects delete ${PROJECT_ID}"
