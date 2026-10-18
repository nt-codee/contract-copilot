<div align="center">

<img src="docs/public/banner.png" alt="Contract Co-pilot: a contract page with the risky clauses boxed in red and amber, and the explanation beside it" width="100%">

# Contract Co-pilot

**Know what you're signing, before you sign it.**

Contract Co-pilot reviews the vendor contracts that small businesses in Malaysia and Singapore sign without a lawyer.
Upload a PDF and read the contract's own pages with every risky clause boxed on the exact words, what each one
means in plain English, the law that applies, and a reply to the vendor that is ready to send.

[![Google Cloud](https://img.shields.io/badge/Google_Cloud-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com)
[![Gemini](https://img.shields.io/badge/Gemini_3.8_Flash-8E75B2?logo=googlegemini&logoColor=white)](https://cloud.google.com/vertex-ai)
[![Cloud Run](https://img.shields.io/badge/Cloud_Run-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/run)
[![Firestore](https://img.shields.io/badge/Firestore-FFA000?logo=googlecloud&logoColor=white)](https://cloud.google.com/firestore)
[![Pulumi](https://img.shields.io/badge/Pulumi-8A3391?logo=pulumi&logoColor=white)](infrastructure/gcp)
[![Python](https://img.shields.io/badge/Python_3.12-3776AB?logo=python&logoColor=white)](services/api)
[![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)](web)

**[Open the live demo](https://contract-copilot-ordg3tjh7a-as.a.run.app)** · built for the Google Cloud **AI Builder Cup 2026**, theme *Future of Work & Enterprise Productivity*

</div>

> Contract Co-pilot gives general information to help you prepare. It is not legal advice.

## See it work

Watch the 3-minute demo: [watch on Google Drive](https://drive.google.com/file/d/1KztL52YcRw1_XKTOd6A3fr5pkWpoL8yx/view).

A software subscription agreement, reviewed. Each coloured box sits on the words in the vendor's PDF; the panel explains the clause,
shows what a balanced term looks like, and quotes the company rule it breaks.

<p align="center"><img src="docs/public/demo-review.gif" alt="Opening a review: pages on the left, boxed clauses on the contract, and the explanation of each finding on the right" width="100%"></p>

One click turns the findings into a reply to the vendor, with one ask per issue, each tied to its clause and page.

<p align="center"><img src="docs/public/demo-reply.gif" alt="The drafted reply to the vendor, with a numbered list of asks that match the flags on the contract" width="100%"></p>

## What you get

| | |
|---|---|
| **A reader, not a report.** Boxes are drawn on the real PDF, on the exact words, so you can check every claim yourself. | <img src="docs/public/screens/reader.png" alt="Contract page with highlighted clauses and the finding panel" width="520"> |
| **Citations that are real.** Each finding links Malaysian or Singapore law, or a case, taken from a library where every entry keeps its source URL and the quoted text. | <img src="docs/public/screens/citation.png" alt="A Contracts Act 1950 entry with its text and a link to the official source" width="520"> |
| **Your own rules, applied.** Upload your company's contracting policy once (owners and admins do this). Reviews flag the clauses that break it and quote the rule. | <img src="docs/public/screens/rules.png" alt="The company rules page: each rule with the contracts that break it" width="520"> |
| **Dates that matter.** The last day to cancel before an auto-renewal, and other deadlines, collected across your contracts. | <img src="docs/public/screens/dates.png" alt="Home page with the number of contracts reviewed and the coming deadlines" width="520"> |
| **Search every contract your company has reviewed.** Describe the situation in your own words and get the clause and its page. Results blend meaning (vector) with exact words and phrases (keyword). | <img src="docs/public/screens/library.png" alt="Library search results for the phrase cancel early" width="520"> |
| **A reply ready to send.** One ask per issue, numbered to match the flags on the contract. Edit it, copy it, or open it in your email. | <img src="docs/public/screens/reply.png" alt="The drafted reply to the vendor" width="520"> |

Also: it reads scanned, image-only PDFs and contracts written in Bahasa Malaysia, and explains them in English.

<!-- screens: round 4 -->

### Teams, accounts and trust

The public demo needs no login: anyone can open the sample reviews, draft a reply, search and browse the law library. Uploading
a contract or a policy, and deleting anything, needs sign-in.

| | |
|---|---|
| **Google sign-in.** Google Cloud Identity Platform; the API checks every ID token against the project. | `GET /api/me`, `GET /api/auth/config` |
| **A workspace per company.** The first person from a Google Workspace domain creates the company workspace; later colleagues from that domain join it. A Gmail user gets a personal workspace. Reviews, policies and search never cross workspaces. | `/api/workspaces` |
| **Roles and invites.** Owner, admin and member. Owners and admins invite (a code valid for 7 days), change roles, remove people and manage company policies and standard positions; members review, search, draft and upload contracts. | `/api/workspaces/{id}/invites`, `/api/invites/{code}/accept` |
| **Your standard positions.** Edit what a balanced clause means for your company, per contract type and clause. The review checks against your version and says so. | `/api/playbook` |
| **An audit trail.** Every upload, review, re-review, reply, view, export and delete is recorded (who, what, when, which model and knowledge-base version). It holds metadata, never contract text. Owners and admins see the whole workspace; a member sees their own. | `/api/activity` |
| **Review provenance and history.** Each review records the model, the knowledge-base version, the prompt version and the app version, and a hash of the PDF. Reviewing the same document again links to the earlier run. | `/api/reviews/{id}/history` |
| **Your data, deletable.** Delete a review, or everything you own. | `DELETE /api/reviews/{id}`, `POST /api/me/delete` |
| **Dashboards, aggregates only.** Owners and admins see their workspace's usage, risk mix, citations, cost and deadlines. Platform administrators see the same across all workspaces, plus the audit metadata, and never any contract text. | `/api/workspaces/{id}/overview`, `/api/admin/*` |

## How it works

```mermaid
flowchart TD
  A[Read the contract<br/>PyMuPDF text; Gemini transcribes scans] --> B[Set the contract type and lock the<br/>governing-law jurisdiction]
  B --> C{Plan the lookups,<br/>run them in parallel}
  C --> L[Law and cases<br/>Malaysia and Singapore]
  C --> P[Playbook<br/>standard terms, or your company's own]
  C --> R[Company rules<br/>your workspace's policy]
  L --> D[Draft findings<br/>one structured Gemini call]
  P --> D
  R --> D
  D --> V{Verify}
  V -->|quote is on the stated page<br/>citation is in the library<br/>law is of the locked jurisdiction| K[Keep]
  V -->|cannot be verified| X[Remove it, and record why]
  K --> E[Locate the words on the PDF<br/>and draw the evidence boxes]
  E --> M[Draft the reply to the vendor]
```

The loop is bounded: two model turns and at most 16 lookups per review. The first turn plans every lookup at once, so they run
side by side instead of one after another.

**Three rules decide what you see.** A finding is shown only if its quote is found on the page it names. A citation is shown only
if it comes from the knowledge library, with its text copied from the source and never written by the model. And the law cited
is that of the contract's governing jurisdiction: it is fixed once, early in the review, and a citation from the other
jurisdiction is removed. Anything that fails a check is removed from the result and recorded with the review as a "removed
claim" with the reason, so you can see what the model said and why it was not shown.

## Measured results

The newest evaluation run (`eval/results/20261018T045101Z`, model `gemini-3.8-flash`) reviewed **8 synthetic contracts**
(4 NDAs and 4 service agreements, one in Bahasa Malaysia and one a scan) against a hand-written answer key.
The contracts are invented, so treat this as a regression check on a small set, not as a claim about real-world accuracy.

| Measure | Result |
|---|---|
| Expected issues found | **20 of 20** (12 of 12 high risk, 8 of 8 medium) |
| Issues the review reported | 28 findings in total |
| False highs (a high flag on a clause the key says is not high) | 3, on 2 of the 8 contracts |
| Time per review, median / slowest | 17.9 s / 62.6 s |
| LLM cost per review, mean | $0.0176 (embeddings excluded, about $0.001 more) |
| Claims removed by verification | 1 across the 8 reviews |
| Reviews that errored | 0 of 8 |

<p align="center"><img src="docs/public/eval-latency.svg" alt="Bar chart of review time for each of the 8 synthetic contracts, with the median at 17.9 seconds" width="100%"></p>

Timing depends on how busy the model endpoint is. An earlier run on the same day, with the same 8 contracts and the same recall,
had a median of 90.6 s and a slowest review of 167.1 s. All three runs are kept in [`eval/results`](eval/results).
Reproduce a run with `scripts/eval.py` (see below).

Not measured yet: the employment, tenancy and hire purchase packs have sample contracts and answer keys but are not part of this scored run.

## Coverage

| Contract type | Standard-terms notes | Sample contracts | Of which Bahasa Malaysia |
|---|---|---|---|
| NDA | 10 | 6 | 1 |
| Service agreement | 12 | 10 | 1 |
| Employment and freelance | 11 | 3 | 1 |
| Tenancy | 10 | 3 | 1 |
| Hire purchase | 10 | 3 | 0 |
| **Total** | **53** | **25** | **4** |

**Jurisdictions:** Malaysia and Singapore. The review picks one from the governing-law clause.

**Law library:** 96 entries, each with its source URL.

| | Statutes | Cases | Total |
|---|---|---|---|
| Malaysia | 40 | 2 | 42 |
| Singapore | 38 | 16 | 54 |

Contract types are packs: a set of standard terms, the relevant law, and samples with answer keys. The reader, the verification
and the reply are shared, so adding a type is mostly content. See [`docs/public/roadmap.md`](docs/public/roadmap.md).

## Architecture

```mermaid
flowchart LR
  U[Browser] -->|Google sign-in| I[Identity Platform]
  U -->|PDF + ID token| R[Cloud Run: FastAPI + React]
  R --> X[Extract pages<br/>PyMuPDF; Gemini OCR for scans]
  X --> A[Gemini 3.8 Flash review loop<br/>Vertex AI]
  A -->|tools| L[(Law: MY + SG<br/>Firestore, vector + keyword)]
  A -->|tools| P[(Playbook<br/>+ your overrides)]
  A -->|tools| C[(Company rules<br/>Firestore vector search)]
  A --> V[Verify quotes + citations<br/>locate boxes]
  V --> F[(Firestore: reviews, chunks,<br/>workspaces, audit events)]
  R --> S[(Cloud Storage: uploads,<br/>7-day lifecycle)]
```

Cloud services sit behind adapters chosen by one environment variable, so the review logic imports no cloud SDK and the whole
pipeline runs offline in tests.

| Layer | Google Cloud |
|---|---|
| Model | Gemini 3.8 Flash on Vertex AI (global endpoint); `gemini-embedding-2` for search |
| App | Cloud Run: scales to zero, at most 3 instances |
| Data | Firestore with vector search (reviews, search chunks, company policies, workspaces and members, invites, users, audit events, the knowledge library, standard-position overrides); Cloud Storage with a 7-day lifecycle on uploads |
| Sign-in | Google Cloud Identity Platform (Google as the only provider); the API verifies each ID token and serves the web client config at `/api/auth/config` |
| Operations | Cloud Monitoring alerts (5xx rate, latency, uptime check), a Cloud Billing budget, labels on every resource |
| Infrastructure as code | Pulumi (Python) in [`infrastructure/gcp`](infrastructure/gcp): bootstrap script, `foundation` stack, `app` stack |

Limits on the public URL: PDFs up to 10 MB and 40 pages, 10 reviews per hour per address.

## Repository layout

```
services/api/        Python backend: pipeline, review loop, adapters (GCP and local), tests
web/                 React + TypeScript frontend
shared/contracts/    JSON Schema for the API, the single source of truth for api and web
knowledge/law/       Malaysian and Singapore statutes and cases, each with its source URL
knowledge/playbook/  standard terms per clause type, for each contract type
samples/             synthetic contracts with answer keys, plus a sample company policy
eval/results/        measured evaluation runs
infrastructure/gcp/  Pulumi stacks
scripts/             review, eval, seed, deploy, teardown
docs/public/         images for this README, and the roadmap
```

## Run it locally

```bash
cd services/api && uv sync
CLOUD=local LLM=fake uv run pytest -q             # offline tests
CLOUD=local LLM=vertex uv run python ../../scripts/review.py ../../samples/contracts/msa-02-autorenew.pdf
CLOUD=local LLM=vertex uv run python ../../scripts/eval.py   # score the samples; writes eval/results
cd ../../web && npm ci && npm run dev             # http://localhost:5173, proxies /api
```

Real reviews need Google Cloud application-default credentials with access to Vertex AI. With `CLOUD=local` the API accepts
test tokens (`Authorization: Bearer test:<uid>:<email>`, optionally `:hd=<domain>`) instead of Identity Platform ID tokens; they
are rejected on a deployed service.

## Deploy

```bash
bash infrastructure/gcp/bootstrap.sh              # project, billing link, state bucket
source infrastructure/gcp/env.sh
(cd infrastructure/gcp/foundation && pulumi up)   # APIs, IAM, registry, budget, alerts
bash scripts/deploy.sh                            # build, pulumi up app, health check
```

Google sign-in switches on once the Google OAuth client id and secret are set in the `app` stack config (`googleClientId`,
`googleClientSecret`); platform administrators are listed in the secret config `adminEmails`. `scripts/ingest_knowledge.py
--target gcp` indexes the law and playbook into Firestore. `scripts/teardown.sh` removes what the deploy created.

## Data and licences

- Every sample contract and the sample company policy is synthetic: invented companies, no real people.
- The law library quotes official sources (Laws of Malaysia, Singapore Statutes Online, eLitigation). The source and reuse terms
  of each entry are listed in [`knowledge/law/README.md`](knowledge/law/README.md); playbook sources are in
  [`knowledge/playbook/SOURCES.md`](knowledge/playbook/SOURCES.md).
- This repository does not yet include a software licence file. Until one is added, all rights in the code are reserved.

## Disclaimer

Contract Co-pilot gives general information to help you prepare for a conversation with the other side. It is not legal advice,
and it does not replace a lawyer for a contract that matters.
