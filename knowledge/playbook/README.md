# knowledge/playbook — standard terms per contract type and clause type, the yardstick for 'risky or unusual'.

Layout: `<contract_type>/<clause_type>.md`, where both names come from the enums in
`shared/contracts/review.schema.json`. `index.json` lists the files per contract type (paths are
relative to this folder). Sources and licences: `SOURCES.md`.

Every file has the same sections, so the reviewer prompt can rely on them:

- `## In plain words` — what the clause does, for a non-lawyer.
- `## What balanced looks like` — the market-standard position (Malaysia and Singapore SMEs).
- `## Red flags` — bullets tagged **High:**, **Medium:** or **Low:**, the risk to assign.
- `## Suggested ask` — one model sentence to put to the other side.

The playbook describes general market practice. It is not legal advice.
