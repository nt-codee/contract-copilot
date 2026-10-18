"""Deterministic test PDFs built with PyMuPDF. Also used as a stand-in contract."""

from __future__ import annotations

import pymupdf

PAGES = [
    "SERVICES AGREEMENT\n\n"
    "This Services Agreement is made between Kedai Contoh Sdn Bhd (\"Customer\") and\n"
    "Awan Biru Pte Ltd (\"Vendor\").\n\n"
    "1. Services\n"
    "1.1 The Vendor shall provide cloud bookkeeping services described in Schedule A.\n"
    "1.2 The Vendor shall perform the services with reasonable skill and care.\n",

    "5. Liability\n"
    "5.1 The Customer shall be liable without limit for any loss suffered by the Vendor.\n"
    "5.2 The Vendor's total liability is limited to the fees paid in the one month\n"
    "before the claim.\n\n"
    "6. Renewal\n"
    "6.1 This Agreement renews automatically for successive 12 month terms unless the\n"
    "Customer gives 90 days written notice.\n",

    "9. Governing law\n"
    "9.1 This Agreement is governed by the laws of the State of Delaware.\n\n"
    "10. General\n"
    "10.1 Notices must be sent by registered post.\n",
]


def make_pdf(pages: list[str] = PAGES) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(pymupdf.Rect(56, 56, 539, 786), text, fontsize=11, fontname="helv")
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def make_scanned_pdf(pages: list[str] = PAGES) -> bytes:
    """Image-only PDF: each page rendered to a bitmap, no text layer."""
    src = pymupdf.open(stream=make_pdf(pages), filetype="pdf")
    out = pymupdf.open()
    for p in src:
        pix = p.get_pixmap(dpi=100)
        page = out.new_page(width=p.rect.width, height=p.rect.height)
        page.insert_image(page.rect, pixmap=pix)
    data = out.tobytes(deflate=True)
    out.close()
    src.close()
    return data


def make_blank_pdf(n_pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(n_pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} of a long contract with plenty of text on it.")
    data = doc.tobytes()
    doc.close()
    return data
