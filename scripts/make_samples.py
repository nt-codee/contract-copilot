#!/usr/bin/env python3
"""Render the synthetic sample contracts in samples/contracts/src/*.md to PDFs.

Usage:  python3 scripts/make_samples.py
        (re-runs itself under `uv run` with pinned fpdf2 and pymupdf if they are not importable)

Deterministic: fixed creation dates, no random IDs, so re-running gives byte-identical PDFs.
Also writes samples/contracts/index.json from the SAMPLES manifest below, and renders the company
policies in samples/company/ (POLICIES).

Markdown subset understood by render_markdown():
  # Title            centred bold title (first line)
  ## Heading         clause heading
  ### Subheading     smaller bold heading
  (a) ... / (i) ...  indented paragraph
  **bold**           inline bold (fpdf2 markdown)
  ::pagebreak::      force a new page
  blank line         paragraph break; consecutive lines are joined
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PINS = ["fpdf2==2.8.4", "pymupdf==1.26.5"]

try:
    from fpdf import FPDF
    import pymupdf
except ImportError:
    if os.environ.get("MAKE_SAMPLES_REEXEC"):
        raise
    cmd = ["uv", "run", "--no-project"]
    for p in PINS:
        cmd += ["--with", p]
    cmd += ["python", os.path.abspath(sys.argv[0]), *sys.argv[1:]]  # the calling script, not this module
    os.execvpe("uv", cmd, {**os.environ, "MAKE_SAMPLES_REEXEC": "1"})

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "contracts"
SRC = SAMPLES / "src"
FIXED_DATE = datetime(2026, 10, 18, 0, 0, 0, tzinfo=timezone.utc)
FOOTER_NOTE = "Sample document for testing. All parties are fictional."

# id, title shown in the app, contract_type, language (ISO 639-1), scanned (image-only PDF)
_CORE_MANIFEST = [
    ("nda-01-fair", "Mutual NDA: software integration talks (KL / Singapore)", "nda", "en", False),
    ("nda-02-oneway", "One-way NDA: supplier talks with a food platform", "nda", "en", False),
    ("nda-03-predatory", "NDA: distribution talks with an overseas group", "nda", "en", False),
    ("nda-04-bm", "Perjanjian Kerahsiaan (NDA in Bahasa Malaysia)", "nda", "ms", False),
    ("msa-01-fair", "Software services agreement: inventory platform", "service_agreement", "en", False),
    ("msa-02-autorenew", "Fleet tracking subscription agreement", "service_agreement", "en", False),
    ("msa-03-liability", "App development and support agreement", "service_agreement", "en", False),
    ("msa-04-scanned", "Facilities services agreement (scanned copy)", "service_agreement", "en", True),
    ("msa-05-saas", "POS software subscription agreement", "service_agreement", "en", False),
    ("msa-06-cloud-hosting", "Hosting and managed services agreement", "service_agreement", "en", False),
    ("msa-07-marketing-agency", "Digital marketing services agreement", "service_agreement", "en", False),
    ("msa-08-logistics", "Last-mile delivery services agreement", "service_agreement", "en", False),
    ("msa-09-bm-cleaning", "Perjanjian Perkhidmatan Pembersihan Pejabat (cleaning, in Bahasa Malaysia)", "service_agreement", "ms", False),
    ("msa-10-long-fair", "IT services agreement with schedules (long, balanced)", "service_agreement", "en", False),
    ("nda-05-investor", "Confidentiality agreement: investor discussions", "nda", "en", False),
    ("nda-06-employee-style", "Confidentiality, non-compete and invention assignment agreement", "nda", "en", False),
]


def _pack_manifests() -> list[tuple]:
    """Extra contract-type packs register their samples in samples/contracts/packs/<type>.json:
    [[id, title, contract_type, language, scanned], ...]. Each pack owns its own file, so packs never collide."""
    out = []
    for f in sorted((SAMPLES / "packs").glob("*.json")):
        out += [tuple(row) for row in json.loads(f.read_text(encoding="utf-8"))]
    return out


MANIFEST = _CORE_MANIFEST + _pack_manifests()

INDENT_RE = re.compile(r"^\(([a-z]{1,4}|[ivx]+)\)\s")

# Company policies for the "breaks your policy" demo: samples/company/<name>.md -> .pdf
COMPANY = ROOT / "samples" / "company"
POLICIES = ["kopi-kita-contracting-policy"]


class ContractPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 4, f"Page {self.page_no()} of {{nb}}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4, FOOTER_NOTE, align="C")
        self.set_text_color(0, 0, 0)


def _paragraphs(text: str) -> list[str]:
    blocks, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        is_block_start = s.startswith("#") or s == "::pagebreak::" or bool(INDENT_RE.match(s))
        if not s or is_block_start:
            if cur:
                blocks.append(" ".join(cur))
                cur = []
            if s and (s.startswith("#") or s == "::pagebreak::"):
                blocks.append(s)
                continue
            if not s:
                continue
        cur.append(s)
    if cur:
        blocks.append(" ".join(cur))
    return blocks


def render_markdown(md: str) -> bytes:
    """Render the Markdown subset to PDF bytes with a text layer."""
    try:
        md.encode("latin-1")
    except UnicodeEncodeError as e:  # core fonts are latin-1 only
        raise SystemExit(f"non latin-1 character in source: {md[e.start:e.end]!r}") from e
    pdf = ContractPDF(format="A4")
    pdf.set_creation_date(FIXED_DATE)
    pdf.set_author("contract-copilot sample generator")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_page()
    for block in _paragraphs(md):
        if block == "::pagebreak::":
            pdf.add_page()
        elif block.startswith("### "):
            pdf.ln(1.5)
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.multi_cell(0, 5.5, block[4:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.5)
        elif block.startswith("## "):
            if pdf.get_y() > pdf.h - 45:  # keep a heading with its first lines
                pdf.add_page()
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 6, block[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif block.startswith("# "):
            pdf.set_font("Helvetica", "B", 15)
            pdf.multi_cell(0, 8, block[2:], align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        else:
            pdf.set_font("Helvetica", "", 10.5)
            indent = 8 if INDENT_RE.match(block) else 0
            pdf.set_x(pdf.l_margin + indent)
            pdf.multi_cell(0, 5.4, block, markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
    return bytes(pdf.output())


def rasterise(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """Turn a text PDF into an image-only PDF (no text layer), like a scanned copy."""
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("png"))
    stamp = "D:20261018000000Z"
    out.set_metadata({"producer": "contract-copilot sample generator (scan)", "creator": "",
                      "creationDate": stamp, "modDate": stamp})
    data = out.tobytes(garbage=4, deflate=True, no_new_id=True)
    out.close()
    src.close()
    return data


def render_sample(sample_id: str, scanned: bool) -> tuple[bytes, bytes]:
    """Return (pdf_as_shipped, text_twin). For text PDFs both are the same bytes."""
    text_pdf = render_markdown((SRC / f"{sample_id}.md").read_text(encoding="utf-8"))
    return (rasterise(text_pdf) if scanned else text_pdf), text_pdf


def render_policy(name: str) -> bytes:
    return render_markdown((COMPANY / f"{name}.md").read_text(encoding="utf-8"))


def main() -> None:
    index = []
    for sid, title, ctype, lang, scanned in MANIFEST:
        pdf, _ = render_sample(sid, scanned)
        out = SAMPLES / f"{sid}.pdf"
        out.write_bytes(pdf)
        pages = pymupdf.open(stream=pdf, filetype="pdf").page_count
        print(f"wrote {out.relative_to(ROOT)}  pages={pages}  bytes={len(pdf)}  scanned={scanned}",
              flush=True)
        index.append({"id": sid, "title": title, "contract_type": ctype, "language": lang,
                      "file": f"{sid}.pdf"})
    (SAMPLES / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    print(f"wrote {(SAMPLES / 'index.json').relative_to(ROOT)}  samples={len(index)}")
    for name in POLICIES:
        pdf = render_policy(name)
        out = COMPANY / f"{name}.pdf"
        out.write_bytes(pdf)
        pages = pymupdf.open(stream=pdf, filetype="pdf").page_count
        print(f"wrote {out.relative_to(ROOT)}  pages={pages}  bytes={len(pdf)}", flush=True)


if __name__ == "__main__":
    main()
