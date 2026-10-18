#!/usr/bin/env bash
# Bootstrap: the chicken-and-egg part that Pulumi cannot create for itself.
#   project -> billing link -> bootstrap APIs -> Pulumi state bucket
# Idempotent: every step checks first and changes nothing if the state already matches.
# Needs: gcloud logged in (owner of the project / billing user on the billing account).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-contract-copilot-abc26}"
PROJECT_NAME="${PROJECT_NAME:-contract-copilot}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-0129D0-93C31A-5B2188}"
REGION="${REGION:-asia-southeast1}"
STATE_BUCKET="${STATE_BUCKET:-${PROJECT_ID}-pulumi-state}"

# Project labels: keys/values must be lowercase. managed-by=bootstrap marks who owns the project.
PROJECT_LABELS="app=contract-copilot,env=demo,owner=namtung,hackathon=aibuildercup2026,managed-by=bootstrap"
BUCKET_LABELS="app=contract-copilot,managed-by=bootstrap"

# Needed before Pulumi can run at all; foundation enables the full list again (idempotently).
BOOTSTRAP_APIS=(
  serviceusage.googleapis.com cloudresourcemanager.googleapis.com storage.googleapis.com
  cloudbilling.googleapis.com billingbudgets.googleapis.com iam.googleapis.com
  aiplatform.googleapis.com run.googleapis.com firestore.googleapis.com
  artifactregistry.googleapis.com cloudbuild.googleapis.com monitoring.googleapis.com
  logging.googleapis.com
)

CHANGES=0
step() { printf '\n[%s] %s\n' "$1" "$2"; }
ok()   { printf '    ok   %s\n' "$1"; }
did()  { printf '    DONE %s\n' "$1"; CHANGES=$((CHANGES + 1)); }

command -v gcloud >/dev/null || { echo "gcloud not found" >&2; exit 1; }

# --- 1. project --------------------------------------------------------------------------
step "1/5" "project ${PROJECT_ID}"
if gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
  ok "project exists"
else
  gcloud projects create "$PROJECT_ID" --name="$PROJECT_NAME" --labels="$PROJECT_LABELS" --quiet
  did "project created"
fi

# --- 2. project labels (compare as sorted key=value lines; only update on drift) -------------
step "2/5" "project labels"
want=$(printf '%s' "$PROJECT_LABELS" | tr ',' '\n' | sort)
have=$(gcloud projects describe "$PROJECT_ID" --format='json(labels)' |
  python3 -c "import json,sys; print('\n'.join(f'{k}={v}' for k,v in sorted((json.load(sys.stdin).get('labels') or {}).items())))")
if [ "$want" = "$have" ]; then
  ok "labels match"
else
  gcloud projects update "$PROJECT_ID" --update-labels="$PROJECT_LABELS" --quiet
  did "labels updated"
fi

# --- 3. billing link ---------------------------------------------------------------------
step "3/5" "billing link -> ${BILLING_ACCOUNT}"
current=$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null || true)
if [ "$current" = "billingAccounts/${BILLING_ACCOUNT}" ]; then
  ok "already linked"
else
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" --quiet
  did "billing linked"
fi

# --- 4. bootstrap APIs -------------------------------------------------------------------
step "4/5" "bootstrap APIs (${#BOOTSTRAP_APIS[@]})"
enabled=$(gcloud services list --enabled --project "$PROJECT_ID" --format='value(config.name)')
missing=()
for api in "${BOOTSTRAP_APIS[@]}"; do
  if grep -qx "$api" <<<"$enabled"; then ok "$api"; else missing+=("$api"); fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  gcloud services enable "${missing[@]}" --project "$PROJECT_ID" --quiet
  did "enabled: ${missing[*]}"
fi

# --- 5. Pulumi state bucket --------------------------------------------------------------
step "5/5" "state bucket gs://${STATE_BUCKET}"
if gcloud storage buckets describe "gs://${STATE_BUCKET}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  ok "bucket exists"
else
  gcloud storage buckets create "gs://${STATE_BUCKET}" --project "$PROJECT_ID" \
    --location="$REGION" --uniform-bucket-level-access --public-access-prevention \
    --update-labels="$BUCKET_LABELS" --quiet
  did "bucket created"
fi
b=$(gcloud storage buckets describe "gs://${STATE_BUCKET}" --project "$PROJECT_ID" \
  --format='value(versioning_enabled,uniform_bucket_level_access,public_access_prevention)')
# value() joins with tabs: versioning<TAB>uniform<TAB>pap. Only the first field can be empty/False.
IFS=$'\t' read -r ver uni pap <<<"$b"
if [ "$ver" = "True" ]; then ok "versioning on"; else
  gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning --quiet; did "versioning enabled"; fi
if [ "$uni" = "True" ]; then ok "uniform access on"; else
  gcloud storage buckets update "gs://${STATE_BUCKET}" --uniform-bucket-level-access --quiet; did "uniform access enabled"; fi
if [ "$pap" = "enforced" ]; then ok "public access prevention enforced"; else
  gcloud storage buckets update "gs://${STATE_BUCKET}" --public-access-prevention --quiet; did "PAP enforced"; fi

printf '\n'
if [ "$CHANGES" -eq 0 ]; then echo "bootstrap: no changes"; else echo "bootstrap: ${CHANGES} change(s) applied"; fi
echo "next: pulumi login gs://${STATE_BUCKET}   (see infrastructure/gcp/README.md)"
