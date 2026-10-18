# samples/contracts — SYNTHETIC NDAs and service agreements with expected flags. Never real contracts (those go in eval/private/, gitignored).

- `src/<id>.md` — the contract text (all parties, addresses and numbers are invented).
- `<id>.pdf` — rendered by `python3 scripts/make_samples.py` (deterministic; also writes `index.json`).
  `msa-04-scanned.pdf` is image-only, with no text layer, to exercise the scanned path.
- `expected/<id>.json` — the answer key: planted risks (`must_flag`, each with a page and a
  `quote_contains` substring on that page) and clause types that must not be rated high.
  For msa-04 the quotes refer to the page images; the checker verifies them against a text twin
  rendered in memory from the same source.
- `index.json` — the "try a sample" list read by the app.

Check everything with `python3 scripts/check_samples.py` (exit 1 on any failure).
