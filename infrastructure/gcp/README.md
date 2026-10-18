# infrastructure/gcp — Pulumi (Python)

GCP deployment for contract-copilot. State lives in a GCS bucket, so no Pulumi account is needed.
Project `contract-copilot-abc26`, region `asia-southeast1` (Gemini calls use `global`).

## Layers (apply in this order)

| # | Layer | What it owns | Tool |
|---|---|---|---|
| 0 | `bootstrap.sh` | project, billing link, bootstrap APIs, Pulumi state bucket | bash + gcloud, idempotent |
| 1 | `foundation/` | all 13 APIs, Artifact Registry repo, `cc-run` service account + IAM, Cloud Build push permission, email channel, RM 200 budget, 5xx and p95 latency alerts | Pulumi stack `demo` |
| 2 | `app/` | uploads bucket, Firestore `(default)` + 768-dim vector index on `chunks.embedding`, Cloud Run service, public invoker, `/healthz` uptime check + alert | Pulumi stack `demo` |

Shared constants (labels, region, API list) are in `common/labels.py`. Labels are applied through the
provider's `default_labels`: `app=contract-copilot env=demo owner=namtung hackathon=aibuildercup2026
managed-by=pulumi`. Resources without a label field (Firestore database, budget, IAM) carry none.

## Setup (once per machine)

```bash
bash infrastructure/gcp/bootstrap.sh          # safe to re-run; prints "no changes" when in sync
source infrastructure/gcp/env.sh              # sets PULUMI_CONFIG_PASSPHRASE_FILE, logs in to the bucket
```

`infrastructure/gcp/.passphrase` is gitignored and holds the Pulumi secrets passphrase. A fresh clone needs
the original file from the owner; a new one cannot decrypt the existing `Pulumi.demo.yaml` files.

The alert address is a Pulumi secret, stored encrypted in `foundation/Pulumi.demo.yaml`:

```bash
cd infrastructure/gcp/foundation && pulumi config set --secret alertEmail <address> --stack demo
```

## Commands

```bash
source infrastructure/gcp/env.sh
(cd infrastructure/gcp/foundation && pulumi preview --stack demo && pulumi up --stack demo)
scripts/deploy.sh      # Cloud Build -> pulumi up (app) -> curl /healthz
scripts/teardown.sh    # asks you to type the project id, destroys app then foundation
```

`app` reads foundation outputs through a StackReference, so `app` preview fails with
`KeyError: 'service_account_email'` until foundation has been applied. `scripts/deploy.sh` sets the
`image` config; for a preview use `pulumi config set image us-docker.pkg.dev/cloudrun/container/hello`.

## Cost cap and shutdown condition

- **Cap:** RM 200 (about USD 50) budget on the project, gross of free-trial credits. Emails at 25, 50, 75,
  90 and 100 % of actual spend and at 100 % forecast, to the alert channel and the billing admins.
  A budget only alerts; it does not stop spend.
- **Cloud Run:** min 0 / max 3 instances, request-based CPU billing. **Uploads:** deleted after 7 days.
- **Shutdown:** run `scripts/teardown.sh` after Demo Day, or at once if the 100 % alert fires.

## What is public

- The Cloud Run service `contract-copilot` allows `allUsers` to invoke it: **public by requirement**, the
  hackathon needs a public URL. There is no authentication in front of it; the only guards are the app's
  own caps (10 MB, 40 pages, per-IP rate limit) and `max_instance_count=3`.
- Everything else is private: the uploads and state buckets enforce public access prevention.

## Teardown date

Live through judging (19 Oct to 6 Nov) and Demo Day (4 Dec 2026). **Tear down after 4 Dec 2026**, and
before the free trial ends (about 17 Jan 2027). `scripts/teardown.sh` prints the final
`gcloud projects delete contract-copilot-abc26` command; it never runs it.
