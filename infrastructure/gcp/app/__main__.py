"""App stack: uploads and knowledge buckets, Firestore + indexes (chunks, company chunks, knowledge, audit),
Identity Platform (Google sign-in) + a browser API key, Cloud Run service (public), uptime check + alert.

Reads the service account email and notification channel from the foundation stack.
"""
import sys
from pathlib import Path

import pulumi
import pulumi_gcp as gcp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.labels import COMMON_LABELS, REGION, SERVICE_NAME  # noqa: E402

gcp_cfg = pulumi.Config("gcp")
project = gcp_cfg.require("project")
cfg = pulumi.Config()
image = cfg.require("image")  # set by scripts/deploy.sh, or a placeholder for preview
app_version = cfg.get("appVersion") or image.rsplit(":", 1)[-1]  # the image tag; deploy.sh sets it
# Secret: comma-separated platform-admin emails. Never in a committed file (Pulumi stores it encrypted).
admin_emails = cfg.get_secret("adminEmails") or pulumi.Output.secret("")
# The project owner creates the Google OAuth client and sets both; until then Google sign-in is not configured.
google_client_id = cfg.get("googleClientId")
google_client_secret = cfg.get_secret("googleClientSecret")

foundation = pulumi.StackReference("organization/cc-foundation/demo")
run_sa_email = foundation.require_output("service_account_email")
channel_id = foundation.require_output("channel_id")

provider = gcp.Provider(
    "gcp",
    project=project,
    region=REGION,
    default_labels=COMMON_LABELS,
    user_project_override=True,
    billing_project=project,
)
base = pulumi.ResourceOptions(provider=provider)


def opts(*deps) -> pulumi.ResourceOptions:
    return pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(depends_on=list(deps)))


# --- Uploads bucket ----------------------------------------------------------------------------
bucket_name = f"{project}-uploads"
bucket = gcp.storage.Bucket(
    "uploads",
    project=project,
    name=bucket_name,
    location=REGION,
    uniform_bucket_level_access=True,
    public_access_prevention="enforced",
    force_destroy=True,  # teardown must work even with uploads still inside (they are demo data)
    lifecycle_rules=[
        gcp.storage.BucketLifecycleRuleArgs(
            action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
            condition=gcp.storage.BucketLifecycleRuleConditionArgs(age=7),
        )
    ],
    opts=base,
)
gcp.storage.BucketIAMMember(
    "uploads-cc-run-object-admin",
    bucket=bucket.name,
    role="roles/storage.objectAdmin",
    member=pulumi.Output.concat("serviceAccount:", run_sa_email),
    opts=base,
)

# --- Knowledge bucket ---------------------------------------------------------------------------
# The ingested law and playbook markdown (kb/<kb_version>/<id>.md), written by scripts/ingest_knowledge.py
# with the owner's own credentials; the Cloud Run service account only reads it.
knowledge_bucket_name = f"{project}-knowledge"
knowledge_bucket = gcp.storage.Bucket(
    "knowledge",
    project=project,
    name=knowledge_bucket_name,
    location=REGION,
    uniform_bucket_level_access=True,
    public_access_prevention="enforced",
    versioning=gcp.storage.BucketVersioningArgs(enabled=True),
    force_destroy=True,  # teardown must work; the content is re-created by the ingest script
    opts=base,
)
gcp.storage.BucketIAMMember(
    "knowledge-cc-run-object-viewer",
    bucket=knowledge_bucket.name,
    role="roles/storage.objectViewer",
    member=pulumi.Output.concat("serviceAccount:", run_sa_email),
    opts=base,
)

# --- Firestore (NATIVE) ---------------------------------------------------------------------------
# Checked 2026-10-18 (`gcloud firestore databases list`): none exists in this project, so create.
# If one appears later, import it:  pulumi import gcp:firestore/database:Database firestore \
#   projects/<project>/databases/(default)
database = gcp.firestore.Database(
    "firestore",
    project=project,
    name="(default)",
    location_id=REGION,
    type="FIRESTORE_NATIVE",
    deletion_policy="DELETE",  # Pulumi/Terraform may delete it on destroy
    delete_protection_state="DELETE_PROTECTION_DISABLED",  # the API-level guard, off so teardown works
    opts=base,
)

gcp.firestore.Index(
    "chunks-embedding-vector",
    project=project,
    database=database.name,
    collection="chunks",
    query_scope="COLLECTION",
    fields=[
        gcp.firestore.IndexFieldArgs(
            field_path="embedding",
            vector_config=gcp.firestore.IndexFieldVectorConfigArgs(
                dimension=768,
                flat=gcp.firestore.IndexFieldVectorConfigFlatArgs(),
            ),
        )
    ],
    # Firestore appends an implicit __name__ field server-side, so `fields` always diffs and forces a
    # replace that fails with 409. Indexes are immutable; to change one, rename this resource.
    opts=pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(ignore_changes=["fields"])),
)

# Same 768-d COSINE index for the company knowledge base (uploaded policy documents).
gcp.firestore.Index(
    "company-chunks-embedding-vector",
    project=project,
    database=database.name,
    collection="company_chunks",
    query_scope="COLLECTION",
    fields=[
        gcp.firestore.IndexFieldArgs(
            field_path="embedding",
            vector_config=gcp.firestore.IndexFieldVectorConfigArgs(
                dimension=768,
                flat=gcp.firestore.IndexFieldVectorConfigFlatArgs(),
            ),
        )
    ],
    opts=pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(ignore_changes=["fields"])),
)

# The knowledge store: one document per law, case and playbook entry, with a 768-d COSINE vector.
# Queries filter on `kind` first, so the composite index is (kind, embedding). Playbook overrides
# (`playbook_overrides`) are read by document id and by `owner ==`, which need no extra index.
gcp.firestore.Index(
    "knowledge-kind-embedding-vector",
    project=project,
    database=database.name,
    collection="knowledge",
    query_scope="COLLECTION",
    fields=[
        gcp.firestore.IndexFieldArgs(field_path="kind", order="ASCENDING"),
        gcp.firestore.IndexFieldArgs(
            field_path="embedding",
            vector_config=gcp.firestore.IndexFieldVectorConfigArgs(
                dimension=768,
                flat=gcp.firestore.IndexFieldVectorConfigFlatArgs(),
            ),
        ),
    ],
    opts=pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(ignore_changes=["fields"])),
)

# Workspace-scoped vector search: find_nearest with a prefilter where("owner", "==", workspace_id) needs the
# composite index (owner ASC, embedding vector). These supersede the two single-field vector indexes above,
# which stay only so a rollout never has a moment without a usable index; drop them in a later cleanup.
for _name, _collection in (("chunks", "chunks"), ("company-chunks", "company_chunks")):
    gcp.firestore.Index(
        f"{_name}-owner-embedding-vector",
        project=project,
        database=database.name,
        collection=_collection,
        query_scope="COLLECTION",
        fields=[
            gcp.firestore.IndexFieldArgs(field_path="owner", order="ASCENDING"),
            gcp.firestore.IndexFieldArgs(
                field_path="embedding",
                vector_config=gcp.firestore.IndexFieldVectorConfigArgs(
                    dimension=768,
                    flat=gcp.firestore.IndexFieldVectorConfigFlatArgs(),
                ),
            ),
        ],
        opts=pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(ignore_changes=["fields"])),
    )

# The audit trail, read per workspace (and per actor within it), newest first.
for _name, _fields in (
    ("audit-workspace-at", [("workspace_id", "ASCENDING"), ("at", "DESCENDING")]),
    ("audit-workspace-actor-at", [("workspace_id", "ASCENDING"), ("actor", "ASCENDING"), ("at", "DESCENDING")]),
):
    gcp.firestore.Index(
        _name,
        project=project,
        database=database.name,
        collection="audit_events",
        query_scope="COLLECTION",
        fields=[gcp.firestore.IndexFieldArgs(field_path=f, order=o) for f, o in _fields],
        opts=pulumi.ResourceOptions.merge(base, pulumi.ResourceOptions(ignore_changes=["fields"])),
    )

# The public Cloud Run host. Cloud Run's URL is https://<service>-<project number>.<region>.run.app, so it is known
# before the service exists; that lets the service's env carry the browser key without a dependency cycle.
_proj = gcp.organizations.get_project_output(project_id=project, opts=pulumi.InvokeOutputOptions(provider=provider))
run_host = _proj.number.apply(lambda n: f"{SERVICE_NAME}-{n}.{REGION}.run.app")

# Google Cloud Identity Platform (the identitytoolkit API is enabled by the foundation stack). The Config resource
# initialises Identity Platform on the project through its own API. Sign-in is Google only: email/password is off.
# Authorized domains for the sign-in redirect: the Cloud Run host, localhost for development, and
# <project>.firebaseapp.com, the auth handler domain Identity Platform provisions for the project.
identity = gcp.identityplatform.Config(
    "identity",
    project=project,
    authorized_domains=[run_host, "localhost", f"{project}.firebaseapp.com"],
    sign_in=gcp.identityplatform.ConfigSignInArgs(
        email=gcp.identityplatform.ConfigSignInEmailArgs(enabled=False),
    ),
    opts=base,
)

# Browser API key for the Identity Platform client SDK. It ships to every browser by design, so it is locked to
# the two Identity Platform APIs and to this site's origins (the Cloud Run URL, and the local dev server).
web_key = gcp.projects.ApiKey(
    "cc-web-auth",
    project=project,
    name="cc-web-auth",
    display_name="contract-copilot web sign-in",
    restrictions=gcp.projects.ApiKeyRestrictionsArgs(
        api_targets=[
            gcp.projects.ApiKeyRestrictionsApiTargetArgs(service="identitytoolkit.googleapis.com"),
            gcp.projects.ApiKeyRestrictionsApiTargetArgs(service="securetoken.googleapis.com"),
        ],
        browser_key_restrictions=gcp.projects.ApiKeyRestrictionsBrowserKeyRestrictionsArgs(
            allowed_referrers=[f"https://{run_host}/*", "http://localhost:5173/*"],
        ),
    ),
    opts=base,
)

# What the browser needs to start the Identity Platform client SDK. Public by design (it ships to every browser);
# the API serves it at GET /api/auth/config so the web reads it at runtime, with no rebuild per environment.
identity_web_config = pulumi.Output.json_dumps({
    "apiKey": web_key.key_string,
    "authDomain": f"{project}.firebaseapp.com",
    "projectId": project,
})

# --- Cloud Run service -------------------------------------------------------------------------------
env = {
    "CLOUD": "gcp",
    "LLM": "vertex",
    "GCP_PROJECT": project,
    "GEMINI_LOCATION": "global",
    "GEMINI_MODEL": "gemini-3.8-flash",
    "EMBED_MODEL": "gemini-embedding-2",
    "UPLOAD_BUCKET": bucket_name,
    "KNOWLEDGE_BUCKET": knowledge_bucket_name,
    "FIRESTORE_DB": "(default)",
    "APP_VERSION": app_version,
}
env_outputs = {
    "IDENTITY_WEB_CONFIG": identity_web_config,
    "ADMIN_EMAILS": admin_emails,  # secret
}

service = gcp.cloudrunv2.Service(
    "service",
    project=project,
    name=SERVICE_NAME,
    location=REGION,
    ingress="INGRESS_TRAFFIC_ALL",
    deletion_protection=False,  # v2 default is true, which would block teardown
    template=gcp.cloudrunv2.ServiceTemplateArgs(
        service_account=run_sa_email,
        timeout="300s",
        max_instance_request_concurrency=10,
        scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(min_instance_count=0, max_instance_count=3),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=image,
                envs=[gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name=k, value=v)
                      for k, v in {**env, **env_outputs}.items()],
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"cpu": "1", "memory": "1Gi"},
                    cpu_idle=True,  # CPU only while serving a request: cheapest for a demo
                    startup_cpu_boost=True,
                ),
            )
        ],
    ),
    opts=opts(database, bucket, knowledge_bucket),
)

# Google as a sign-in provider. Skipped until the OAuth client exists (`pulumi config set googleClientId ...`
# and `pulumi config set --secret googleClientSecret ...`), so a preview passes before it is created.
if google_client_id and google_client_secret is not None:
    gcp.identityplatform.DefaultSupportedIdpConfig(
        "google-idp",
        project=project,
        idp_id="google.com",
        enabled=True,
        client_id=google_client_id,
        client_secret=google_client_secret,
        opts=opts(identity),
    )

# PUBLIC BY REQUIREMENT: the hackathon needs a public URL, so allUsers may invoke the service.
gcp.cloudrunv2.ServiceIamMember(
    "public-invoker",
    project=project,
    location=REGION,
    name=service.name,
    role="roles/run.invoker",
    member="allUsers",
    opts=base,
)

# --- Uptime check on /healthz + alert -----------------------------------------------------------------
host = service.uri.apply(lambda u: u.removeprefix("https://").rstrip("/"))
uptime = gcp.monitoring.UptimeCheckConfig(
    "healthz",
    project=project,
    display_name="contract-copilot /healthz",
    timeout="10s",
    period="300s",
    http_check=gcp.monitoring.UptimeCheckConfigHttpCheckArgs(
        path="/api/health", port=443, use_ssl=True, validate_ssl=True, request_method="GET"
    ),
    monitored_resource=gcp.monitoring.UptimeCheckConfigMonitoredResourceArgs(
        type="uptime_url", labels={"project_id": project, "host": host}
    ),
    opts=base,
)

gcp.monitoring.AlertPolicy(
    "alert-uptime",
    project=project,
    display_name="contract-copilot: /healthz uptime check failing",
    combiner="OR",
    notification_channels=[channel_id],
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="uptime check failed",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter=pulumi.Output.concat(
                    'metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND '
                    'resource.type="uptime_url" AND metric.labels.check_id="',
                    uptime.uptime_check_id,
                    '"',
                ),
                comparison="COMPARISON_GT",
                threshold_value=1,
                duration="300s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="1200s",
                        per_series_aligner="ALIGN_NEXT_OLDER",
                        cross_series_reducer="REDUCE_COUNT_FALSE",
                        group_by_fields=["resource.*"],
                    )
                ],
            ),
        )
    ],
    opts=base,
)

pulumi.export("service_url", service.uri)
pulumi.export("uploads_bucket", bucket.name)
pulumi.export("knowledge_bucket", knowledge_bucket.name)
pulumi.export("identity_web_config", identity_web_config)  # public values: the browser receives them anyway
pulumi.export("google_sign_in_configured", bool(google_client_id and google_client_secret is not None))
