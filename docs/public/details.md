# Contract Co-pilot: the detail behind the README

The README is the short version. This page keeps the full qualifications: what each check proves and
does not prove, how the daily allowance works, and how the evaluation numbers should be read.

> Contract Co-pilot gives general information to help you prepare. It is not legal advice.

## Using the app

- **Progress reported by the server.** Fresh reviews report reading, planning, research, drafting, verification
  and locating phases as they happen. Older servers show an honest waiting state; opening a cached result is
  labelled separately. Phase reports stream over the existing request; this is not a durable background-job system.
- **Files.** PDFs and JPG/PNG scans, in English or Bahasa Malaysia, up to 10 MB and 40 pages. The upload queue
  reviews files one at a time. Before adding them, choose the contract type, governing law, company policies and
  whether the review represents your business or you personally; automatic choices are available.
- **Findings.** Short summaries open into the explanation, the suggested change and the cited sources. The
  evidence view puts the contract and the company policy pages side by side.
- **Replies.** Draft an editable response to the employer, landlord, lender, vendor or other party, with asks tied
  to the findings.
- **Library.** Search clauses and their pages, track upcoming dates, and see activity and earlier runs of a contract.
- **Search.** Signed-in search combines meaning and keywords; public search uses keywords only.
- **Who can draft.** New drafts need sign-in. Visitors and guests can only open an already saved reply.
- **Refreshing.** A completed review is saved to the Library, but refreshing or closing an active review does not
  have a durable background-resume guarantee; a pending upload queue depends on its page.

## Accounts, roles and the demo company

Google sign-in creates a personal or company workspace, with owner, admin and member roles. Reviews, policies and
search are scoped to that workspace. Owners and admins manage people, rules and standard positions; review history
and an audit trail keep the recorded provenance. You can delete a review or the data you own.

The demo company contains synthetic contracts and invented people. Guests can browse its overview, people, rules
and activity, but cannot upload, edit, delete or start new paid work. A saved reply must already exist; an uncached
reply request requires sign-in. There is no guest bypass for a cold instance or a missing saved draft.

## The daily allowance

New paid work requires sign-in and has a **USD 1 allowance per account per UTC day**, including model attempts,
retries, fallback models, embeddings and policy processing. The service reserves a bounded amount before an attempt;
known usage releases unused allowance. A call that clearly returned nothing (timeout, overload) keeps only its input
cost, at most 20 times per account per day; any other uncertain failure keeps the whole reservation. This is
conservative token-price accounting, not an exact provider invoice. Cached reads do not start new AI calls.

Alongside the per-account allowance, fresh reviews have per-address and per-user rate limits and a service-wide
daily review-count cap. The count cap covers reviews; the account allowance also covers other paid work such as
drafts and embeddings. Cached results and copies do not start a new model review. Accounting failures refuse new
paid work rather than bypassing the allowance.

## Verification rules

The verify step keeps a claim only if its quote is on the stated page, its citation is in the library and supports
the claim, its law is the locked jurisdiction, and any date or amount it states appears in the contract.
If Gemini 3.8 Flash stalls, the same request is retried once on Gemini 3.5 Flash.

**Three rules decide what you see.** A finding is shown only if its quote is found on the page it names. A citation
is shown only if it comes from the knowledge library, with its text copied from the source and never written by the
model. And the law cited is that of the contract's governing jurisdiction: it is fixed once, early in the review,
and a citation from the other jurisdiction is removed. Unsupported claims are recorded with reasons when removed.
A high-risk rating whose consequence cannot be verified may instead be reduced to medium while the finding is kept;
the review distinguishes these adjustments from removals. These checks constrain the output, but do not establish
that every retained interpretation is correct.

Matching a quote is tolerant of how PDFs store text, not of what the contract says. Both sides are reduced to
letters and digits, ligatures and curly quotes are folded, hyphenated line breaks are rejoined and page furniture
(page numbers, running headers and footers) is ignored. A close-but-not-exact match is accepted only if it has the
same numbers and the same negations as the quote, so a changed figure or a dropped "not" is removed.

## Limits

Finding the quoted text and checking that a source exists do not prove the legal interpretation or risk rating is
correct. The prototype can miss issues or make unsupported claims; verify the cited words and consult a lawyer when
needed. Scans can take longer than text PDFs.

## Evaluation

The stored run **[18 October 2026, 12:42:53 UTC](../../eval/results/20261018T124253Z.md)** used `gemini-3.8-flash`
on 25 synthetic contracts with hand-written answer keys. These are results for that artifact, not a guarantee for
the current deployment or real contracts. [Raw results and outputs](../../eval/results/20261018T124253Z.json) are
available for inspection.

| Measure | That run |
|---|---|
| Expected issues found | 70 of 76 (92%); 49/54 high, 21/22 medium |
| False highs against the answer key’s excluded high-risk clause types | 0 |
| Completed / errored reviews | 25 / 0 |
| Median / slowest review | 36.0 s / 160.2 s |
| Mean model cost per review | USD 0.02657, excluding embeddings |

A later **[fixed eight-case validation](../../eval/results/20261018T152353Z.md)** on backend `88801f2` found 28 of 29
expected issues, with no answer-key false highs. Its median / slowest review was 37.4 s / 131.0 s; the mean
successful model-call estimate was USD 0.02873, excluding embeddings. This is a smaller, separate scope, combined
from two sequential runs; [the raw artifact](../../eval/results/20261018T152353Z.json) preserves the initial
budget-blocked attempts and provenance. It does not replace the 25-case benchmark or establish real-world accuracy.
The product’s USD 1 daily allowance is unchanged.

**Recall is not overall quality.** The completed assessment of these same eight fresh outputs is **FAIL**:
citation support averaged **6.875/10** and restraint **6.375/10**, both below the required 7. Other scored averages
met that threshold, but they do not offset these failures or establish that individual findings are correct.
Email-draft quality is **not assessed (N/A): zero drafts were tested**. Zero false highs against a limited answer
key does not establish that every finding or severity is correct. Both dated runs include a scanned contract taking
longer than two minutes; no universal speed or near-zero-cost promise is made.

After that assessment, general fixes went in (dates computed in code and marked stated or inferred; legal claims
hedged unless a cited source supports them; summaries that contradict a finding removed). The same eight contracts
were run again on backend `c05f822`: **[27 of 29 expected issues](../../eval/results/20261018T164053Z.md)**, no
answer-key false highs, 8 of 8 completed, median / slowest 26.9 s / 119.9 s, USD 0.02885 per review. One contract
got better (`hp-02-used-car`, 5 of 5) and one worse (`msa-09-bm-cleaning`, 3 of 5: a one-sided 7-day termination is
now rated medium where the key says high, and the customer's breach-only exit was not flagged). That run has **not
yet been independently assessed**, so the FAIL above still stands.

[Earlier runs](../../eval/results) remain available. Running `scripts/eval.py` makes paid model calls; reproduce it
only with configured access and an explicit budget.

## Architecture notes

Cloud services sit behind adapters selected by configuration. The review logic imports no cloud SDK, and local
adapters let tests run without model or cloud calls.

Firestore holds reviews, search chunks, company policies, workspaces and members, invites, users, audit events, the
knowledge library and standard-position overrides, with vector search. Cloud Storage keeps uploads with a 7-day
lifecycle. Identity Platform uses Google as the only provider; the API verifies each ID token and serves the web
client config at `/api/auth/config`.

## Repository layout

```
services/api/        Python backend: pipeline, review loop, adapters (GCP and local), tests
web/                 React + TypeScript frontend
shared/contracts/    JSON Schema for the API, the single source of truth for api and web
knowledge/law/       Malaysian and Singapore statutes and cases, each with its source URL
knowledge/playbook/  standard terms per clause type, for each contract type
samples/             synthetic contracts with answer keys, plus a sample company policy
samples/showcase/    30 synthetic contracts for the demo company
eval/results/        measured evaluation runs
infrastructure/gcp/  Pulumi stacks
scripts/             review, eval, seed, deploy, teardown
docs/public/         README images, this page and the roadmap
```

## Local development

`CLOUD=local` accepts test tokens (`Authorization: Bearer test:<uid>:<email>`, optionally `:hd=<domain>`); deployed
services reject them. The normal Google sign-in flow needs Identity Platform configured, so a fresh local browser is
not signed in automatically. The fake model is for exercising the interface and tests, not evaluating review quality.

## Images in the README

Every screenshot and GIF is captured from the live service, read-only, on synthetic contracts and the invented
Kopi Kita company. Opening a saved sample does not run a new review, and GIF frames are held for reading, so they
are not a speed benchmark. The method is in [`media.md`](media.md) and the provenance of each file (source commit,
service revision, capture time, frame hashes, crops) is in [`media-evidence.json`](media-evidence.json).
The [recorded product video](https://drive.google.com/file/d/1KztL52YcRw1_XKTOd6A3fr5pkWpoL8yx/view) shows an earlier
interface; the current app may look different.
