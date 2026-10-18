#!/usr/bin/env python3
"""Render the showcase contracts in samples/showcase/src/*.md to PDFs, using the same renderer as the eval samples.

Usage:  python3 samples/showcase/make_showcase.py
        (re-runs itself under `uv run` with the pinned fpdf2 and pymupdf if they are not importable)

Reads samples/showcase/manifest.json, writes samples/showcase/<id>.pdf and samples/showcase/index.json
(id, title, type, language, jurisdiction, filename). Deterministic: re-running gives byte-identical PDFs.
Every contract here is synthetic. This folder is separate from samples/contracts/, which holds the eval corpus.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
import make_samples as ms  # noqa: E402  (re-execs under uv if fpdf2/pymupdf are missing)
import pymupdf  # noqa: E402

MIN_PAGES, MAX_PAGES = 2, 6


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    index, bad = [], []
    for m in manifest:
        pdf = ms.render_markdown((HERE / "src" / f"{m['id']}.md").read_text(encoding="utf-8"))
        (HERE / f"{m['id']}.pdf").write_bytes(pdf)
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            pages = doc.page_count
        if not MIN_PAGES <= pages <= MAX_PAGES:
            bad.append(f"{m['id']}: {pages} pages")
        print(f"wrote {m['id']}.pdf  pages={pages}  bytes={len(pdf)}", flush=True)
        index.append({"id": m["id"], "title": m["title"], "type": m["contract_type"],
                      "language": m["language"], "jurisdiction": m["jurisdiction"],
                      "filename": f"{m['id']}.pdf"})
    (HERE / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote index.json  contracts={len(index)}")
    if bad:
        print("page count outside 2-6:", "; ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
