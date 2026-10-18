"""Deep links to the exact provision of each law entry.

`app/data/deeplinks.json` maps entry id -> URL. It is written only by scripts/ingest_knowledge.py
(which fetches the official sources) and read at startup. This module has the pure functions; the
network part is a callable passed in by the script, so tests need no network.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable

log = logging.getLogger("cc.deeplinks")

DEEPLINKS_PATH = Path(__file__).parent / "data" / "deeplinks.json"
_SECTION = re.compile(r"\bs(?:ection)?\.?\s*(\d+[A-Z]{0,2})\b")
_SSO = re.compile(r"^https://sso\.agc\.gov\.sg/Act/([A-Za-z0-9]+)")


def load_deeplinks(path: Path = DEEPLINKS_PATH) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        log.warning("could not read %s; deep links off", path)
        return {}


def section_of(citation: str) -> str | None:
    """'Contracts Act 1950 (Act 136), s 24' -> '24'; 'PDPA 2012, s 26C' -> '26C'."""
    m = _SECTION.search(citation or "")
    return m.group(1) if m else None


def sso_link(source_url: str, citation: str) -> str | None:
    """https://sso.agc.gov.sg/Act/<CODE>?ProvIds=pr<section>- for a Singapore statute section."""
    m, sec = _SSO.match(source_url or ""), section_of(citation)
    return f"https://sso.agc.gov.sg/Act/{m.group(1)}?ProvIds=pr{sec}-" if m and sec else None


def lom_link(source_url: str, page: int) -> str:
    """The existing PDF URL plus #page=<n>."""
    return f"{source_url.split('#')[0]}#page={page}"


def build_deeplinks(entries: list[dict], find_page: Callable[[str, str], int | None]
                    ) -> tuple[dict[str, str], dict[str, str]]:
    """entries: {id, source_url, citation, text}. find_page(pdf_url, text) -> 1-based page or None.

    Returns (links, unresolved): unresolved maps id -> why no deep link was made (plain URL kept).
    """
    links: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    for e in entries:
        url = e["source_url"]
        if url.startswith("https://sso.agc.gov.sg/"):
            link = sso_link(url, e["citation"])
            if link:
                links[e["id"]] = link
            else:
                unresolved[e["id"]] = "no section number in the citation"
        elif ".pdf" in url.lower() and "lom.agc.gov.my" in url:
            try:
                page = find_page(url, e["text"])
            except Exception as ex:  # a download problem must not stop the other entries
                unresolved[e["id"]] = f"PDF not fetched: {type(ex).__name__}: {str(ex)[:80]}"
                continue
            if page:
                links[e["id"]] = lom_link(url, page)
            else:
                unresolved[e["id"]] = "quoted text not found in the PDF"
        else:
            unresolved[e["id"]] = "not a statute page (case or other source)"
    return links, unresolved


def pdf_page_of(pdf_bytes: bytes, text: str, min_score: int = 90) -> int | None:
    """1-based page of `pdf_bytes` on which `text` begins (matched on its first ~80 characters)."""
    import pymupdf
    from rapidfuzz import fuzz

    needle = _squash(text)[:80]
    if len(needle) < 20:
        return None
    best: tuple[float, int] | None = None
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            hay = _squash(page.get_text("text"))
            if needle in hay:
                return i
            score = fuzz.partial_ratio(needle, hay)
            if score >= min_score and (best is None or score > best[0]):
                best = (score, i)
    return best[1] if best else None


def _squash(text: str) -> str:
    text = text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                                         "\u2013": "-", "\u2014": "-", "\u00a0": " "}))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

