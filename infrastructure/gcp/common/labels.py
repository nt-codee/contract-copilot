"""Shared constants for both Pulumi stacks (foundation, app).

Imported with a sys.path tweak in each stack's __main__.py. The labels are applied through the
GCP provider's `default_labels`, so every labellable resource gets them without repeating them.
"""

PROJECT_ID = "contract-copilot-abc26"
REGION = "asia-southeast1"
BILLING_ACCOUNT = "0129D0-93C31A-5B2188"
SERVICE_NAME = "contract-copilot"

COMMON_LABELS = {
    "app": "contract-copilot",
    "env": "demo",
    "owner": "namtung",
    "hackathon": "aibuildercup2026",
    "managed-by": "pulumi",
}

# Every API the stacks (and the app at runtime) rely on. The first block is also enabled by
# bootstrap.sh, because Pulumi itself needs serviceusage/cloudresourcemanager/storage before it can run.
APIS = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "storage.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
    "iam.googleapis.com",
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "identitytoolkit.googleapis.com",  # Google Cloud Identity Platform (Google sign-in)
    "apikeys.googleapis.com",  # the restricted browser API key for the Identity Platform client
]
