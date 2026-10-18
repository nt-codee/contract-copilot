"""App stack: uploads bucket, Firestore + vector index, Cloud Run service (public), uptime check + alert.

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

# --- Cloud Run service -------------------------------------------------------------------------------
env = {
    "CLOUD": "gcp",
    "LLM": "vertex",
    "GCP_PROJECT": project,
    "GEMINI_LOCATION": "global",
    "GEMINI_MODEL": "gemini-3.8-flash",
    "EMBED_MODEL": "gemini-embedding-2",
    "UPLOAD_BUCKET": bucket_name,
    "FIRESTORE_DB": "(default)",
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
                envs=[gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name=k, value=v) for k, v in env.items()],
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"cpu": "1", "memory": "1Gi"},
                    cpu_idle=True,  # CPU only while serving a request: cheapest for a demo
                    startup_cpu_boost=True,
                ),
            )
        ],
    ),
    opts=opts(database, bucket),
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
