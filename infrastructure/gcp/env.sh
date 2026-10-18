#!/usr/bin/env bash
# Source me:  source infrastructure/gcp/env.sh
# Points Pulumi at the GCS state bucket and the gitignored passphrase file, then logs in.
_gcp_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export PROJECT_ID="${PROJECT_ID:-contract-copilot-abc26}"
export REGION="${REGION:-asia-southeast1}"
export PULUMI_CONFIG_PASSPHRASE_FILE="${_gcp_dir}/.passphrase"
export PULUMI_BACKEND_URL="gs://${PROJECT_ID}-pulumi-state"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

if [ ! -s "$PULUMI_CONFIG_PASSPHRASE_FILE" ]; then
  echo "missing ${PULUMI_CONFIG_PASSPHRASE_FILE}" >&2
  echo "  fresh clone: restore it from the owner's password manager (a new random one cannot decrypt" >&2
  echo "  the existing stack config); first-ever setup: (umask 077; openssl rand -base64 32 > that file)" >&2
  return 1 2>/dev/null || exit 1
fi
pulumi login "$PULUMI_BACKEND_URL" >/dev/null
