#!/usr/bin/env python3
"""Render the extra Kopi Kita company policies in samples/company/src/*.md to PDFs, with the same renderer as the samples.

Usage:  python3 samples/company/make_policies.py
        (re-runs itself under `uv run` with the pinned fpdf2 and pymupdf if they are not importable)

Writes samples/company/<name>.pdf and samples/company/policies.json, which lists every company policy the
seed script uploads: the original contracting policy (rendered by scripts/make_samples.py) first, then these.
Deterministic: re-running gives byte-identical PDFs. All policies are synthetic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
import make_samples as ms  # noqa: E402  (re-execs under uv if fpdf2/pymupdf are missing)
import pymupdf  # noqa: E402

MIN_PAGES, MAX_PAGES = 2, 4

# name (file stem), title shown in lists, one-line scope
POLICIES = [
    ("kopi-kita-people-and-hiring-policy", "People and hiring policy",
     "employment, internship and freelance agreements"),
    ("kopi-kita-premises-and-leasing-policy", "Premises and leasing policy",
     "tenancies: shop lots, offices, warehouses and rooms"),
    ("kopi-kita-equipment-and-financing-policy", "Equipment and financing policy",
     "hire purchase, leases, equipment finance and loans"),
]
ORIGINAL = ("kopi-kita-contracting-policy", "Contracting policy", "all vendor contracts and NDAs")


def main() -> int:
    bad = []
    for name, _title, _scope in POLICIES:
        pdf = ms.render_markdown((HERE / "src" / f"{name}.md").read_text(encoding="utf-8"))
        (HERE / f"{name}.pdf").write_bytes(pdf)
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            pages = doc.page_count
        if not MIN_PAGES <= pages <= MAX_PAGES:
            bad.append(f"{name}: {pages} pages")
        print(f"wrote {name}.pdf  pages={pages}  bytes={len(pdf)}", flush=True)
    index = [{"id": n, "title": t, "scope": s, "filename": f"{n}.pdf"} for n, t, s in [ORIGINAL, *POLICIES]]
    for e in index:
        if not (HERE / e["filename"]).is_file():
            bad.append(f"{e['filename']}: missing")
    (HERE / "policies.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote policies.json  policies={len(index)}")
    if bad:
        print("problems:", "; ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
