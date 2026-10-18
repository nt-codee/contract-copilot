#!/usr/bin/env python3
"""Check the synthetic sample corpus and the baseline playbook.

Usage:  python3 scripts/check_samples.py
        (re-runs itself under `uv run` with pinned fpdf2 and pymupdf if needed; exit 1 on any failure)

Checks:
  1. Text layer: the 7 text PDFs have text on every page; msa-04-scanned has no text at all.
  2. Every must_flag quote_contains is on the stated page (case-insensitive, whitespace-normalised).
     For the scanned PDF the quote is checked against its text twin, re-rendered in memory from the
     same source, after confirming the page count matches.
  3. index.json and expected/*.json parse, and every enum value is in shared/contracts/review.schema.json.
  4. Planted risks sit on page 2 or later; no clause type is both must_flag high and must_not_flag_high.
  5. Baseline: index.json lists existing files for nda and service_agreement; SOURCES.md records CC BY 4.0.
  6. Determinism: re-rendering every sample in memory gives the same bytes as the PDF on disk.
  7. Company policy: PDF is 2-3 pages with text, byte-identical on re-render; company/expected.json
     rows point at real rules and real must_flag findings; >= 4 rules hit msa-02/msa-03/nda-03.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_samples as ms  # noqa: E402  (re-execs under uv if fpdf2/pymupdf are missing)
import pymupdf  # noqa: E402

ROOT = ms.ROOT
SAMPLES = ROOT / "samples" / "contracts"
BASELINE = ROOT / "knowledge" / "playbook"
SCHEMA = json.loads((ROOT / "shared" / "contracts" / "review.schema.json").read_text())["$defs"]
CLAUSE_TYPES = set(SCHEMA["ClauseType"]["enum"])
CONTRACT_TYPES = set(SCHEMA["ContractType"]["enum"])
RISKS = set(SCHEMA["Risk"]["enum"])

failures: list[str] = []
passes = 0


def check(ok: bool, msg: str) -> bool:
    global passes
    if ok:
        passes += 1
    else:
        failures.append(msg)
        print(f"FAIL {msg}", flush=True)
    return ok


def norm(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def page_texts(pdf_bytes: bytes) -> list[str]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [p.get_text() for p in doc]


def main() -> None:
    manifest = {m[0]: m for m in ms.MANIFEST}

    # --- index.json -------------------------------------------------------------------------
    index = json.loads((SAMPLES / "index.json").read_text(encoding="utf-8"))
    check(isinstance(index, list) and len(index) >= 16, f"index.json is a list of 16+ samples (got {len(index)})")
    for entry in index:
        sid = entry.get("id", "?")
        check(set(entry) == {"id", "title", "contract_type", "language", "file"},
              f"index[{sid}] has exactly id/title/contract_type/language/file")
        check(entry.get("contract_type") in CONTRACT_TYPES, f"index[{sid}] contract_type in enum")
        check((SAMPLES / entry.get("file", "")).is_file(), f"index[{sid}] file exists")
        check(sid in manifest, f"index[{sid}] is in make_samples.MANIFEST")

    # --- per sample ---------------------------------------------------------------------------
    for entry in index:
        sid = entry["id"]
        scanned = manifest[sid][4]
        shipped = (SAMPLES / entry["file"]).read_bytes()
        rendered, twin = ms.render_sample(sid, scanned)
        check(hashlib.sha256(rendered).digest() == hashlib.sha256(shipped).digest(),
              f"{sid}: re-render is byte-identical to the PDF on disk")

        shipped_text = page_texts(shipped)
        n_pages = len(shipped_text)
        lo, hi = (14, 20) if sid == "msa-10-long-fair" else (3, 8)  # msa-10 is the long-document control by design
        check(lo <= n_pages <= hi, f"{sid}: {lo}-{hi} pages (got {n_pages})")
        if scanned:
            check(all(not t.strip() for t in shipped_text), f"{sid}: scanned PDF has an empty text layer")
            with pymupdf.open(stream=shipped, filetype="pdf") as d:
                check(all(len(p.get_images()) == 1 for p in d), f"{sid}: one page image per page")
            quote_pages = page_texts(twin)
            check(len(quote_pages) == n_pages, f"{sid}: text twin has the same page count")
        else:
            check(all(len(t.strip()) > 200 for t in shipped_text), f"{sid}: text layer on every page")
            quote_pages = shipped_text
        quote_pages = [norm(t) for t in quote_pages]

        exp_path = SAMPLES / "expected" / f"{sid}.json"
        if not check(exp_path.is_file(), f"{sid}: expected/{sid}.json exists"):
            continue
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        check(set(exp) == {"id", "contract_type", "language", "expected_overall_risk", "must_flag",
                           "must_not_flag_high"}, f"{sid}: expected has exactly the agreed keys")
        check(exp.get("id") == sid, f"{sid}: expected.id matches")
        check(exp.get("contract_type") == entry["contract_type"], f"{sid}: contract_type matches index")
        check(exp.get("language") == entry["language"], f"{sid}: language matches index")
        check(exp.get("expected_overall_risk") in RISKS, f"{sid}: expected_overall_risk in enum")
        high_flagged = set()
        for i, f in enumerate(exp.get("must_flag", [])):
            tag = f"{sid}: must_flag[{i}]"
            check(set(f) == {"clause_type", "risk", "page", "quote_contains"}, f"{tag} keys")
            check(f.get("clause_type") in CLAUSE_TYPES, f"{tag} clause_type {f.get('clause_type')!r} in enum")
            check(f.get("risk") in RISKS, f"{tag} risk in enum")
            if f.get("risk") == "high":
                high_flagged.add(f.get("clause_type"))
            page, q = f.get("page"), norm(f.get("quote_contains", ""))
            check(isinstance(page, int) and page >= 2, f"{tag} page {page} is not page 1")
            check(len(q) >= 12, f"{tag} quote_contains is distinctive (>= 12 chars)")
            on_page = isinstance(page, int) and 1 <= page <= n_pages and q in quote_pages[page - 1]
            found = [n + 1 for n, t in enumerate(quote_pages) if q in t]
            check(on_page, f"{tag} quote {f.get('quote_contains')!r} on page {page} (found on {found})")
            check(len(found) == 1, f"{tag} quote appears on exactly one page (found on {found})")
        for ct in exp.get("must_not_flag_high", []):
            check(ct in CLAUSE_TYPES, f"{sid}: must_not_flag_high {ct!r} in enum")
        check(not (high_flagged & set(exp.get("must_not_flag_high", []))),
              f"{sid}: no clause type is both must_flag high and must_not_flag_high")
        print(f"--   {sid}: pages={n_pages} scanned={scanned} must_flag={len(exp.get('must_flag', []))}",
              flush=True)

    # --- baseline -----------------------------------------------------------------------------
    bidx = json.loads((BASELINE / "index.json").read_text(encoding="utf-8"))
    for ctype in ("nda", "service_agreement"):
        files = bidx.get(ctype, [])
        check(8 <= len(files) <= 12, f"baseline {ctype}: 8-12 files (got {len(files)})")
        for rel in files:
            p = BASELINE / rel
            check(p.is_file(), f"baseline {rel} exists")
            check(p.parent.name == ctype and p.stem in CLAUSE_TYPES, f"baseline {rel} named <type>/<clause_type>.md")
            if p.is_file():
                body = p.read_text(encoding="utf-8")
                for sec in ("## In plain words", "## What balanced looks like", "## Red flags", "## Suggested ask"):
                    check(sec in body, f"baseline {rel} has section {sec!r}")
    for ctype in bidx:
        check(ctype in CONTRACT_TYPES, f"baseline index key {ctype!r} in ContractType enum")
    sources = BASELINE / "SOURCES.md"
    check(sources.is_file() and "CC BY 4.0" in sources.read_text(encoding="utf-8"),
          "baseline SOURCES.md exists and records CC BY 4.0")

    # --- company policy -----------------------------------------------------------------------
    must_flag_types = {e["id"]: {f["clause_type"] for f in json.loads(
        (SAMPLES / "expected" / f"{e['id']}.json").read_text(encoding="utf-8"))["must_flag"]} for e in index}
    rules: set[int] = set()
    for name in ms.POLICIES:
        pdf_path = ms.COMPANY / f"{name}.pdf"
        if not check(pdf_path.is_file(), f"policy {name}.pdf exists"):
            continue
        shipped = pdf_path.read_bytes()
        check(ms.render_policy(name) == shipped, f"policy {name}: re-render is byte-identical")
        texts = page_texts(shipped)
        check(2 <= len(texts) <= 3, f"policy {name}: 2-3 pages (got {len(texts)})")
        check(all(len(t.strip()) > 200 for t in texts), f"policy {name}: text layer on every page")
        rules |= {int(n) for n in re.findall(r"^Rule (\d+)\.", "\n".join(texts), re.M)}
        print(f"--   policy {name}: pages={len(texts)} rules={sorted(rules)}", flush=True)
    conflicts = json.loads((ms.COMPANY / "expected.json").read_text(encoding="utf-8"))
    check(isinstance(conflicts, list) and conflicts, "company/expected.json is a non-empty list")
    for i, c in enumerate(conflicts):
        tag = f"company/expected[{i}]"
        check(set(c) == {"sample_id", "clause_type", "policy_rule_no"}, f"{tag} keys")
        check(c.get("sample_id") in must_flag_types, f"{tag} sample_id {c.get('sample_id')!r} in index")
        check(c.get("clause_type") in CLAUSE_TYPES, f"{tag} clause_type in enum")
        check(c.get("policy_rule_no") in rules, f"{tag} rule {c.get('policy_rule_no')} exists in the policy")
        check(c.get("clause_type") in must_flag_types.get(c.get("sample_id"), set()),
              f"{tag} {c.get('sample_id')}/{c.get('clause_type')} is a must_flag finding")
    targeted = [c for c in conflicts if c.get("sample_id") in ("msa-02-autorenew", "msa-03-liability",
                                                               "nda-03-predatory")]
    check(len({c["policy_rule_no"] for c in targeted}) >= 4,
          "at least 4 distinct rules conflict with msa-02, msa-03 and nda-03")
    check(not any(c.get("sample_id", "").endswith("-fair") for c in conflicts),
          "the fair controls break no policy rule")

    # --- every expected clause type has a baseline file (warning only: e.g. non_solicit in a MSA)
    for entry in index:
        exp = json.loads((SAMPLES / "expected" / f"{entry['id']}.json").read_text(encoding="utf-8"))
        for f in exp["must_flag"]:
            rel = f"{entry['contract_type']}/{f['clause_type']}.md"
            if rel not in bidx.get(entry["contract_type"], []):
                print(f"warn {entry['id']}: no baseline file {rel}", flush=True)

    print(f"\n{passes} checks passed, {len(failures)} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
