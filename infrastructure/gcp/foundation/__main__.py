"""Foundation stack: APIs, Artifact Registry, Cloud Run service account + IAM, notification channel,
billing budget, and the alert policies that do not need the Cloud Run URL.

The app stack owns the uptime check (it needs the URL) and everything that is the workload.
"""
import sys
from pathlib import Path

import pulumi
import pulumi_gcp as gcp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.labels import APIS, BILLING_ACCOUNT, COMMON_LABELS, REGION, SERVICE_NAME  # noqa: E402

gcp_cfg = pulumi.Config("gcp")
project = gcp_cfg.require("project")
cfg = pulumi.Config()
alert_email = cfg.require_secret("alertEmail")  # pulumi config set --secret alertEmail ...

# One provider so default_labels reach every labellable resource. user_project_override +
# billing_project are required for the Billing Budgets API when running on user (ADC) credentials.
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


proj = gcp.organizations.get_project(project_id=project, opts=pulumi.InvokeOptions(provider=provider))

# --- APIs ---------------------------------------------------------------------------------
services = {
    api: gcp.projects.Service(
        f"api-{api.split('.')[0]}",
        project=project,
        service=api,
        disable_on_destroy=False,
        opts=base,
    )
    for api in APIS
}
svc = services.__getitem__

# --- Artifact Registry ---------------------------------------------------------------------
repo = gcp.artifactregistry.Repository(
    "registry",
    project=project,
    location=REGION,
    repository_id="contract-copilot",
    format="DOCKER",
    description="contract-copilot container images",
    cleanup_policies=[
        gcp.artifactregistry.RepositoryCleanupPolicyArgs(
            id="keep-5-versions",
            action="KEEP",
            most_recent_versions=gcp.artifactregistry.RepositoryCleanupPolicyMostRecentVersionsArgs(keep_count=5),
        ),
        # A KEEP-only policy deletes nothing. This catch-all DELETE removes everything not kept.
        gcp.artifactregistry.RepositoryCleanupPolicyArgs(
            id="delete-the-rest",
            action="DELETE",
            condition=gcp.artifactregistry.RepositoryCleanupPolicyConditionArgs(tag_state="ANY"),
        ),
    ],
    opts=opts(svc("artifactregistry.googleapis.com")),
)
registry_path = pulumi.Output.concat(REGION, "-docker.pkg.dev/", project, "/", repo.repository_id)

# --- Cloud Run service account (least privilege) --------------------------------------------
run_sa = gcp.serviceaccount.Account(
    "cc-run",
    project=project,
    account_id="cc-run",
    display_name="contract-copilot Cloud Run runtime",
    opts=opts(svc("iam.googleapis.com")),
)
for role in [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
]:
    gcp.projects.IAMMember(
        f"cc-run-{role.split('/')[1].replace('.', '-')}",
        project=project,
        role=role,
        member=pulumi.Output.concat("serviceAccount:", run_sa.email),
        opts=base,
    )

# --- Cloud Build -> Artifact Registry ---------------------------------------------------------
# Checked 2026-10-18 with `gcloud builds get-default-service-account`: this project's default build
# identity is the Compute Engine default SA, not the legacy <number>@cloudbuild SA. Grant it writer
# on this one repo (the legacy SA already holds cloudbuild.builds.builder, which can push).
build_sa = f"serviceAccount:{proj.number}-compute@developer.gserviceaccount.com"
gcp.artifactregistry.RepositoryIamMember(
    "build-sa-registry-writer",
    project=project,
    location=REGION,
    repository=repo.repository_id,
    role="roles/artifactregistry.writer",
    member=build_sa,
    opts=base,
)

# --- Notification channel (address is a secret; never in a committed file) -------------------
channel = gcp.monitoring.NotificationChannel(
    "alert-email",
    project=project,
    display_name="contract-copilot alerts",
    type="email",
    labels={"email_address": alert_email},
    opts=opts(svc("monitoring.googleapis.com")),
)

# --- Billing budget: MYR 200, scoped to this project -------------------------------------------
# credit_types_treatment=EXCLUDE_ALL_CREDITS: the account is on a free trial, so with the default
# (INCLUDE_ALL_CREDITS) net spend stays 0 and no threshold would ever fire. Gross spend is the cap.
thresholds = [
    gcp.billing.BudgetThresholdRuleArgs(threshold_percent=p, spend_basis="CURRENT_SPEND")
    for p in (0.25, 0.5, 0.75, 0.9, 1.0)
] + [gcp.billing.BudgetThresholdRuleArgs(threshold_percent=1.0, spend_basis="FORECASTED_SPEND")]

budget = gcp.billing.Budget(
    "budget",
    billing_account=BILLING_ACCOUNT,
    display_name="contract-copilot RM 200",
    amount=gcp.billing.BudgetAmountArgs(
        specified_amount=gcp.billing.BudgetAmountSpecifiedAmountArgs(currency_code="MYR", units="200")
    ),
    budget_filter=gcp.billing.BudgetBudgetFilterArgs(
        projects=[f"projects/{proj.number}"],
        credit_types_treatment="EXCLUDE_ALL_CREDITS",
    ),
    threshold_rules=thresholds,
    all_updates_rule=gcp.billing.BudgetAllUpdatesRuleArgs(
        monitoring_notification_channels=[channel.id],
        disable_default_iam_recipients=False,  # billing admins keep getting the mail too
    ),
    opts=opts(svc("billingbudgets.googleapis.com")),
)

# --- Alert policies (the uptime alert lives in the app stack) ---------------------------------
run_filter = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE_NAME}"'

gcp.monitoring.AlertPolicy(
    "alert-5xx",
    project=project,
    display_name="contract-copilot: Cloud Run 5xx > 5 in 5 min",
    combiner="OR",
    notification_channels=[channel.id],
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="5xx request count",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter=(
                    'metric.type="run.googleapis.com/request_count" AND '
                    'metric.labels.response_code_class="5xx" AND ' + run_filter
                ),
                comparison="COMPARISON_GT",
                threshold_value=5,
                duration="0s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="300s",
                        per_series_aligner="ALIGN_DELTA",
                        cross_series_reducer="REDUCE_SUM",
                    )
                ],
            ),
        )
    ],
    opts=opts(svc("monitoring.googleapis.com")),
)

gcp.monitoring.AlertPolicy(
    "alert-latency-p95",
    project=project,
    display_name="contract-copilot: Cloud Run p95 latency > 90 s for 5 min",
    combiner="OR",
    notification_channels=[channel.id],
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="p95 request latency",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter='metric.type="run.googleapis.com/request_latencies" AND ' + run_filter,
                comparison="COMPARISON_GT",
                threshold_value=90000,  # the metric is in milliseconds
                duration="300s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="300s",
                        per_series_aligner="ALIGN_PERCENTILE_95",
                        cross_series_reducer="REDUCE_MAX",
                    )
                ],
            ),
        )
    ],
    opts=opts(svc("monitoring.googleapis.com")),
)

pulumi.export("service_account_email", run_sa.email)
pulumi.export("registry_path", registry_path)
pulumi.export("channel_id", channel.id)
pulumi.export("budget_name", budget.name)
