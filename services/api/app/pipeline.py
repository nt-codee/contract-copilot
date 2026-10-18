"""Review pipeline: validate -> extract -> agent (tools, then findings) -> verify quotes and citations
-> index -> persist.

Imports only ports, models, prompts and config. No cloud SDK.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pymupdf

from app import calibration, grounding, prompts, provenance, quotes
from app.agent import Toolbox, Trace, fill_cited, run_agent
from app.audit import Audit
from app.config import DEMO_OWNER, DISCLAIMER, Settings, estimate_cost
from app.knowledge import _STOP as STOPWORDS
from app.knowledge import Knowledge, LawEntry
from app.playbook_overrides import PlaybookOverrideStore
from app.models import (
    RISK_ORDER, Citation, CompanyDoc, Finding, KeyDate, LLMBoxes, LLMCitation, LLMFinding, LLMKeyDate,
    LLMNegotiation, LLMTranscription, Metrics, NegotiationAsk, NegotiationDraft, RemovedClaim, ReviewOptions, ReviewResult, SearchHit, Verdict, Verification,
)
from app.ports import (LLM, AuthVerifier, BlobStore, Chunk, CompanyChunk, CompanyStore, Embedder, LLMUsage,
                       PdfPart, ReviewStore, SearchIndex, SpendCounter, UserStore, WorkspaceStore)

log = logging.getLogger("cc.pipeline")

MIN_CHARS_PER_PAGE = 50
CHUNK_CHARS = 800


class UserError(Exception):
    """An error whose message is safe and useful to show to the user."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Services:
    settings: Settings
    llm: LLM
    embedder: Embedder
    blobs: BlobStore
    index: SearchIndex
    store: ReviewStore
    company: CompanyStore
    kb: Knowledge  # a HybridKnowledge in practice: same search interface, backed by the knowledge store
    auth: AuthVerifier
    users: UserStore
    audit: Audit
    workspaces: WorkspaceStore
    kb_version: str  # content hash of knowledge/, computed once at startup
    prompt_version: str
    overrides: "PlaybookOverrideStore | None" = None  # workspace playbook overrides (None: defaults only)
    spend: "SpendCounter | None" = None  # daily cap on fresh model reviews (None: no cap)


# ---- 1. validate and extract -----------------------------------------------------------------


def validate_pdf(pdf_bytes: bytes, settings: Settings) -> pymupdf.Document:
    if len(pdf_bytes) > settings.max_bytes:
        raise UserError(413, "file_too_large",
                        f"That file is larger than {settings.max_bytes // (1024 * 1024)} MB. "
                        "Please upload a smaller PDF.")
    if not pdf_bytes.lstrip()[:5].startswith(b"%PDF-"):
        raise UserError(415, "not_a_pdf", "That file is not a PDF. Please upload a PDF contract.")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        raise UserError(422, "unreadable_pdf",
                        "We could not open that PDF. It may be damaged; please try another copy.") from None
    if doc.needs_pass:
        raise UserError(422, "encrypted_pdf",
                        "That PDF is password-protected. Please remove the password and upload it again.")
    if doc.page_count == 0:
        raise UserError(422, "empty_pdf", "That PDF has no pages.")
    if doc.page_count > settings.max_pages:
        raise UserError(422, "too_many_pages",
                        f"That PDF has {doc.page_count} pages. The limit is {settings.max_pages} pages.")
    return doc


# Contract photos and scans: one image is one page. Recognised by magic bytes, never by the file name.
IMAGE_TYPES = {"jpeg": "image/jpeg", "png": "image/png"}  # what PyMuPDF 1.28 can open and convert
MAX_IMAGE_PIXELS = 50_000_000  # about a 50 MP photo; refuses decompression bombs before decoding
NOT_SUPPORTED = ("That file is not a PDF, JPEG or PNG. Please upload the contract as a PDF, "
                 "or a photo of one page as a JPEG or PNG.")
UNSUPPORTED_IMAGE = ("We cannot read {name} images yet. Please save the photo as a JPEG or PNG, "
                     "or upload the contract as a PDF.")


def sniff(data: bytes) -> str | None:
    """'pdf', 'jpeg', 'png', 'webp', 'heic' from the first bytes; None for anything else."""
    if data.lstrip()[:5].startswith(b"%PDF-"):
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
                                                  b"mif1", b"msf1"):
        return "heic"
    return None


def image_to_pdf(data: bytes, settings: Settings) -> bytes:
    """One JPEG or PNG -> a one-page PDF at the image's own size (PyMuPDF; EXIF rotation applied), so it
    takes the scanned path: the model transcribes it and boxes come back in page coordinates."""
    if len(data) > settings.max_bytes:
        raise UserError(413, "file_too_large",
                        f"That file is larger than {settings.max_bytes // (1024 * 1024)} MB. "
                        "Please upload a smaller image.")
    try:
        img = pymupdf.open(stream=data)
    except Exception:
        raise UserError(422, "unreadable_image",
                        "We could not open that image. It may be damaged; please try another copy.") from None
    try:
        r = img[0].rect  # points: the pixels at the image's own resolution, read without decoding
        w, h = _pixel_size(data)
        if (w * h if w and h else r.width * r.height * (96 / 72) ** 2) > MAX_IMAGE_PIXELS:
            raise UserError(422, "image_too_large",
                            "That image has too many pixels. Please upload a smaller photo of the page.")
        return img.convert_to_pdf()
    except UserError:
        raise
    except Exception:
        raise UserError(422, "unreadable_image",
                        "We could not open that image. It may be damaged; please try another copy.") from None
    finally:
        img.close()


def _pixel_size(data: bytes) -> tuple[int, int]:
    """Width and height in pixels from a PNG IHDR or a JPEG SOF header; (0, 0) if not found."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    i = 2
    while data[:2] == b"\xff\xd8" and i + 9 < len(data):
        if data[i] != 0xFF:
            break
        marker, size = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + size
    return 0, 0


def contract_pdf(data: bytes, settings: Settings) -> tuple[bytes, str]:
    """The upload as PDF bytes, and what it was ('pdf', 'jpeg', 'png'). Other types are refused."""
    kind = sniff(data)
    if kind == "pdf":
        return data, kind
    if kind in IMAGE_TYPES:
        return image_to_pdf(data, settings), kind
    if kind in ("webp", "heic"):
        raise UserError(415, "not_a_pdf", UNSUPPORTED_IMAGE.format(name=kind.upper()))
    raise UserError(415, "not_a_pdf", NOT_SUPPORTED)


def extract_pages(doc: pymupdf.Document) -> list[str]:
    return [page.get_text("text") for page in doc]


def needs_ocr(pages: list[str]) -> bool:
    total = sum(len(p.strip()) for p in pages)
    return total < MIN_CHARS_PER_PAGE * max(len(pages), 1)


def transcribe(llm: LLM, pdf_bytes: bytes, page_count: int) -> tuple[list[str], LLMUsage]:
    out, usage = llm.generate(system=prompts.TRANSCRIBE_SYSTEM,
                              parts=[PdfPart(pdf_bytes), prompts.TRANSCRIBE_USER],
                              schema=LLMTranscription, purpose="transcribe")
    pages = [""] * page_count
    for p in out.pages:
        if 1 <= p.page <= page_count:
            pages[p.page - 1] = p.text
    return pages, usage


# ---- 3. quote verification -------------------------------------------------------------------

_PUNCT_MAP = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                            "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u00ad": ""})


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Lower-case, unify quotes/dashes and collapse whitespace; keep a map to original indices."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        ch = ch.translate(_PUNCT_MAP)
        if not ch:
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        out.append(ch.lower())
        idx.append(i)
        prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalise(text: str) -> str:
    return _norm_map(text)[0]


def _clean_span(text: str) -> str:
    return " ".join(text.split())


def match_on_page_detail(quote: str, page_text: str,
                         repeated: set[str] | None = None) -> tuple[str, str, float] | None:
    """(matching original span of `page_text`, how it matched, score 0-100), or None.

    How: "exact" = the quote is on the page character for character; "normalised" = the same letters
    and digits once Unicode forms, ligatures, quotes, dashes, spacing, hyphenation and page furniture are
    evened out (app/quotes.py); "fuzzy" = a window of the page scores >= quotes.FUZZY_THRESHOLD.
    """
    hit = quotes.match_text(quote, page_text, repeated)
    return (hit.span, hit.how, hit.score, hit.start) if hit else None


def match_on_page(quote: str, page_text: str) -> str | None:
    """Return the matching original span of `page_text`, or None."""
    hit = match_on_page_detail(quote, page_text)
    return hit[0] if hit else None


@dataclass
class QuoteHit:
    page: int
    span: str
    how: str
    score: float
    corrected_from: int | None  # the page the model claimed, when the quote was on a neighbouring page
    start: int = 0  # offset of the quote in its page's text


def page_furniture(pages: list[str]) -> set[str]:
    """Running headers and footers: short lines repeated on most pages (see quotes.furniture)."""
    return quotes.furniture([quotes.tokenize(p) for p in pages])


def verify_quote_detail(quote: str, page: int, pages: list[str],
                        repeated: set[str] | None = None) -> QuoteHit | None:
    """Find `quote` on the claimed page, else on page-1 / page+1, else running from one of those pages
    onto the next (exact only; the hit's page is the one the quote starts on)."""
    repeated = page_furniture(pages) if repeated is None else repeated
    for candidate in (page, page - 1, page + 1):
        if 1 <= candidate <= len(pages):
            hit = match_on_page_detail(quote, pages[candidate - 1], repeated)
            if hit is not None:
                return QuoteHit(candidate, hit[0], hit[1], hit[2], page if candidate != page else None, hit[3])
    for start in (page, page - 1):
        if 1 <= start < len(pages):
            hit = quotes.match_across(quote, pages[start - 1], pages[start], repeated)
            if hit is not None:
                return QuoteHit(start, hit.span, hit.how, hit.score, page if start != page else None, hit.start)
    return None


def verify_quote(quote: str, page: int, pages: list[str]) -> tuple[int, str] | None:
    """Find `quote` on the claimed page, else on page-1 / page+1. Returns (page, exact_span)."""
    hit = verify_quote_detail(quote, page, pages)
    return (hit.page, hit.span) if hit else None


JURISDICTION_NAME = {"my": "Malaysia", "sg": "Singapore"}
JURISDICTION_ADJECTIVE = {"my": "Malaysian", "sg": "Singapore"}


def fallback_prefix(home: str) -> str:
    return f"If disputed in {JURISDICTION_NAME.get(home, 'Malaysia')}: "


# the reader is a freelancer or contractor, not an employee, by the contract's own words
CONTRACTOR = re.compile(r"independent contractor|kontraktor bebas|\bfreelancer\b|not an employee|bukan (?:seorang )?"
                        r"pekerja|contract for services|kontrak untuk perkhidmatan|tiada hubungan (?:majikan|pekerjaan)",
                        re.I)
# a finding that states when an employee-only statute applies
EMPLOYEE_CONDITION = re.compile(r"\bif (?:you are|you're|the \w+ is|they are)\b[^.]{0,30}\bemployee\b|"
                                r"\bonly (?:applies to|protects|covers) employees\b|\bemployee status\b|"
                                r"\bwhether you (?:are|count as) an employee\b|\bif (?:the )?employment act[^.]{0,20}"
                                r"(?:applies|covers)\b|\bif (?:this|the agreement) is (?:in substance |really )?a "
                                r"contract of service\b", re.I)


def is_contractor(pages: list[str]) -> bool:
    return any(CONTRACTOR.search(p) for p in pages)


def policy_quoted(raw: list[LLMCitation], known: dict[str, list[Citation]]) -> bool:
    """True if the model quoted the words of a retrieved company rule (its excerpt is in that rule's text)."""
    for c in raw:
        if c.kind != "company" or len(c.excerpt.strip()) < 12:
            continue
        for rec in known.get(c.ref.strip(), []):
            if rec.kind == "company" and quotes.match_text(c.excerpt, rec.excerpt) is not None:
                return True
    return False


def verify_citations(raw: list[LLMCitation], known: dict[str, list[Citation]], *,
                     removed: list[RemovedClaim] | None = None,
                     finding_title: str | None = None, jurisdiction: str | None = None,
                     home: str = "my", clause_type: str | None = None, quote: str = "",
                     contractor: bool = False, why: str = "",
                     law_by_id: Callable[[str], LawEntry | None] | None = None) -> tuple[list[Citation], int]:
    """Keep a citation only if a tool returned its ref in this run, and take every displayed field
    (kind, title, excerpt, url, jurisdiction, page) from that KB record, never from the model.

    With `jurisdiction` (the review's), a law or case is also dropped unless it is that country's law:
    a Malaysian contract is not judged on Singapore law, nor the reverse. Under a foreign law
    ("other") the home country's law is allowed as a fallback and its title says so.

    With `law_by_id` and `clause_type`, a law or case is also dropped unless its entry's `supports`
    scope covers that clause type and the finding's `quote` (reason citation_scope): being in the
    library is not enough, the entry must back this kind of claim."""
    kept: list[Citation] = []
    dropped = 0
    for c in raw:
        records = known.get(c.ref.strip())
        if not records:
            dropped += 1
            log.info("dropped unverified citation: %r", c.ref)
            if removed is not None:
                removed.append(RemovedClaim(kind="citation", reason="source_not_retrieved", text=c.ref,
                                            finding_title=finding_title))
            continue
        rec = records[0]
        if rec.kind == "company":  # one document can come back as several passages; keep the first
            rec = next((r for r in records if r.excerpt and c.excerpt.strip()[:40] in r.excerpt), rec)
        if jurisdiction and rec.kind in ("law", "case"):
            allowed = home if jurisdiction == "other" else jurisdiction
            if rec.jurisdiction != allowed:  # judged on the KB record, never on the model's word
                dropped += 1
                log.info("dropped citation %r: %s law on a %s contract", c.ref, rec.jurisdiction, jurisdiction)
                if removed is not None:
                    removed.append(RemovedClaim(kind="citation", reason="wrong_jurisdiction", text=c.ref,
                                                finding_title=finding_title))
                continue
        entry = law_by_id(rec.ref) if law_by_id and clause_type and rec.kind in ("law", "case") else None
        # an employee-only statute on a freelance or contractor agreement: kept only if the finding says so
        not_for_them = (entry is not None and contractor and entry.applies_to == "employee"
                        and not EMPLOYEE_CONDITION.search(why))
        if entry is not None and (not entry.supports_claim(clause_type, quote) or not_for_them):
            dropped += 1
            log.info("dropped citation %r: does not support a %s claim", c.ref, clause_type)
            if removed is not None:
                removed.append(RemovedClaim(kind="citation", reason="citation_scope", text=c.ref,
                                            finding_title=finding_title))
            continue
        if any(k.ref == rec.ref and k.page == rec.page for k in kept):
            continue  # the same source cited twice for one finding
        rec = rec.model_copy()
        if jurisdiction == "other" and rec.kind in ("law", "case"):
            rec.title = fallback_prefix(home) + rec.title
        kept.append(rec)
    return kept, dropped


def add_caveats(findings: list[Finding], law_by_id: Callable[[str], LawEntry | None]) -> None:
    """Append each cited entry's caveat (for example the Hire-Purchase Act version warning) to the
    finding's explanation, once. Runs after grounding: the caveat is the library's words, not a claim
    about the contract."""
    for f in findings:
        notes: list[str] = []
        for c in f.citations:
            e = law_by_id(c.ref) if c.kind in ("law", "case") else None
            if e is not None and e.caveat and e.caveat not in notes and e.caveat not in f.why_risky:
                notes.append(e.caveat)
        if notes:
            f.why_risky = " ".join([f.why_risky.strip(), *notes])


def verify_findings(raw: list[LLMFinding], pages: list[str],
                    known: dict[str, list[Citation]] | None = None, *, kb_version: str | None = None,
                    removed: list[RemovedClaim] | None = None, jurisdiction: str | None = None,
                    home: str = "my", law_by_id: Callable[[str], LawEntry | None] | None = None,
                    consequences: dict[str, str] | None = None) -> tuple[list[Finding], int, int]:
    """Quote verification (drop a finding with no real quote) and citation verification.

    Returns (findings, dropped_findings, dropped_citations). Every claim that is removed is appended to
    `removed` (when given) so the user can see what the model said and verification refused.
    """
    known = known or {}
    removed = removed if removed is not None else []
    kept: list[tuple[LLMFinding, QuoteHit]] = []
    dropped = 0
    repeated = page_furniture(pages)
    contractor = is_contractor(pages)
    for f in raw:
        hit = verify_quote_detail(f.quote, f.page, pages, repeated)
        if hit is None:
            dropped += 1
            log.info("dropped unverified finding on p%s (%s)", f.page, f.clause_type)
            removed.append(RemovedClaim(kind="finding", reason="quote_not_found",
                                        text=f"{f.title}: {f.quote}", page=f.page, finding_title=f.title))
            continue
        kept.append((f, hit))
    kept.sort(key=lambda t: (RISK_ORDER[t[0].risk], t[1].page))
    findings: list[Finding] = []
    dropped_citations = 0
    for i, (f, hit) in enumerate(kept, start=1):
        cites, n = verify_citations(f.citations, known, removed=removed, finding_title=f.title,
                                    jurisdiction=jurisdiction, home=home, clause_type=f.clause_type,
                                    quote=hit.span, law_by_id=law_by_id, contractor=contractor,
                                    why=f.why_risky)
        dropped_citations += n
        # a policy breach is a claim about the user's own rules: keep it only if one is cited
        # a policy breach is asserted only when both are quoted: the clause (verified above) and the rule
        breach = f.policy_breach.strip()
        if breach and not (any(c.kind == "company" for c in cites) and policy_quoted(f.citations, known)):
            removed.append(RemovedClaim(kind="policy_breach", reason="no_company_citation", text=breach,
                                        page=hit.page, finding_title=f.title))
            breach = ""
        if consequences is not None:
            consequences[f"f{i}"] = f.consequence
        findings.append(Finding(
            id=f"f{i}", clause_type=f.clause_type, title=f.title.strip(), risk=f.risk, quote=hit.span, page=hit.page,
            clause_ref=pick_clause_ref(f.clause_ref, pages[hit.page - 1], hit.start, pages),
            why_risky=f.why_risky, standard=f.standard, suggested_ask=f.suggested_ask,
            baseline_ref=f.baseline_ref or None, citations=cites, policy_breach=breach or None,
            verification=Verification(quote_match=hit.how, score=hit.score,  # type: ignore[arg-type]
                                      page_corrected_from=hit.corrected_from,
                                      citations_checked=len(f.citations), kb_version=kb_version)))
    return findings, dropped, dropped_citations


# a clause number at the start of a line, followed by a capital, a quote or a bracket: "5.1 The ...",
# "7. TERMINATION", "Fasal 3 (a)". A wrapped line that starts "12 months after ..." is not a clause.
_HEAD_NO = re.compile(r"(?m)^[ \t]*(?:(?i:clause|fasal|klausa|section|seksyen)[ \t]+)?"
                      r"(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?[ \t]+[\"\u201c(A-Z]")
_REF_NO = re.compile(r"\d{1,2}(?:\.\d{1,2}){0,3}(?:\s*\([a-z0-9]{1,4}\))*", re.I)


def clause_number(page_text: str, offset: int) -> str | None:
    """The number of the clause the text at `offset` belongs to: the last line-start number ('5.1',
    '7.', '12)') at or before it on the page. None when the page numbers no lines before it."""
    best = None
    for m in _HEAD_NO.finditer(page_text):
        if m.start(1) > offset:
            break
        best = m.group(1)
    return best


def pick_clause_ref(model_ref: str, page_text: str, offset: int, pages: list[str]) -> str | None:
    """The finding's clause number. The one read off the page wins; the model's is kept only when it
    refines it ('5.1(b)' for '5.1') or, if the page gave none, when that number occurs in the contract.
    Never a number the contract does not contain."""
    found = clause_number(page_text, offset)
    m = _REF_NO.search(model_ref or "")
    given = re.sub(r"\s+", "", m.group(0)) if m else ""
    if found:
        return given if given and given != found and given.startswith(found + "(") else found
    if given and any(re.search(rf"(?<![\d.]){re.escape(given)}(?![\d])", p) for p in pages):
        return given
    return None


def locate_company_boxes(svc: Services, findings: list[Finding], owner: str) -> None:
    """Company citations: where the excerpt sits on its page of the stored policy PDF (same locator as
    for contracts). Best effort: a missing PDF, a scan with no text layer or an unmatched excerpt leaves []."""
    wanted = [c for f in findings for c in f.citations if c.kind == "company" and c.doc_id and c.page]
    docs: dict[str, pymupdf.Document | None] = {}
    try:
        for c in wanted:
            if c.doc_id not in docs:
                mine = svc.company.get(c.doc_id, owner=owner) is not None  # never read another workspace's PDF
                data = svc.blobs.get(f"company/{c.doc_id}.pdf") if mine else None
                docs[c.doc_id] = pymupdf.open(stream=data, filetype="pdf") if data else None
            doc = docs[c.doc_id]
            if doc is not None and 1 <= (c.page or 0) <= doc.page_count:
                c.boxes = locate_boxes(doc[c.page - 1], c.excerpt.removesuffix("...").rstrip())
    except Exception:  # boxes are a nicety; never fail the review over them
        log.exception("locating company citation boxes failed")
    finally:
        for d in docs.values():
            if d is not None:
                d.close()


# ---- 3a. key dates ---------------------------------------------------------------------------


def add_months(d: date, months: int) -> date:
    """d plus whole months; the day is clamped to the end of a shorter month (31 Jan + 1 month = 28/29 Feb)."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def compute_date(base_date: str, months: int, days: int) -> date | None:
    """base_date + months + days, or None when the contract gave no usable base date.

    The model supplies the pieces (date stated in the contract, term in months, day offset); the
    arithmetic is done here, never by the model. The last day of a 12-month term starting 1 Jan is
    1 Jan + 12 months - 1 day, and a notice deadline 90 days before that adds -90 more.
    """
    try:
        base = date.fromisoformat((base_date or "").strip())
        return add_months(base, int(months)) + timedelta(days=int(days))
    except (ValueError, OverflowError):
        return None


def build_key_dates(raw: list[LLMKeyDate], findings: list[Finding], page_count: int) -> list[KeyDate]:
    """Dated first (soonest first), then undated. Drops an entry whose page is outside the contract."""
    by_title = {f.title.strip().lower(): f.id for f in findings}
    out: list[KeyDate] = []
    for k in raw:
        if not 1 <= k.page <= page_count or not k.label.strip():
            log.info("dropped key date on page %s (%s)", k.page, k.kind)
            continue
        out.append(KeyDate(label=k.label.strip(), kind=k.kind, date=compute_date(k.base_date, k.months, k.days),
                           rule=k.rule.strip() or None, page=k.page,
                           finding_id=by_title.get(k.finding_title.strip().lower()) if k.finding_title else None))
    out.sort(key=lambda k: (k.date is None, k.date or date.max, k.page))
    return out


# ---- 3b. highlight boxes ---------------------------------------------------------------------

EDGE_CHARS = 60
MAX_BOX_PAGES = 10  # scanned path: at most this many extra LLM calls per review


def _same_line(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap > 0.5 * min(a.height, b.height)


def _merge_lines(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Union fragments that sit on the same text line; return lines in reading order."""
    lines: list[pymupdf.Rect] = []
    for r in sorted(rects, key=lambda r: (r.y0, r.x0)):
        for line in lines:
            if _same_line(line, r):
                line.include_rect(r)
                break
        else:
            lines.append(pymupdf.Rect(r))
    return sorted(lines, key=lambda r: (r.y0, r.x0))


def _edge(quote: str, head: bool) -> str:
    """First/last ~EDGE_CHARS of the quote, cut at a word boundary."""
    if len(quote) <= EDGE_CHARS:
        return quote
    if head:
        cut = quote.rfind(" ", 0, EDGE_CHARS)
        return quote[: cut if cut > EDGE_CHARS // 2 else EDGE_CHARS]
    start = len(quote) - EDGE_CHARS
    cut = quote.find(" ", start)
    return quote[cut + 1 if 0 <= cut < start + EDGE_CHARS // 2 else start:]


def _span_lines(page: pymupdf.Page, start: pymupdf.Rect, end: pymupdf.Rect) -> list[pymupdf.Rect]:
    """Line rects of the words from the start rect to the end rect, in reading order."""
    words = []
    for x0, y0, x1, y1, *_ in page.get_text("words"):
        w = pymupdf.Rect(x0, y0, x1, y1)
        cy = (w.y0 + w.y1) / 2
        if cy < start.y0 or cy > end.y1:
            continue
        if _same_line(w, start) and w.x1 < start.x0 + 1:
            continue
        if _same_line(w, end) and w.x0 > end.x1 - 1:
            continue
        words.append(w)
    return _merge_lines(words)


def _normalised(lines: list[pymupdf.Rect], page_rect: pymupdf.Rect) -> list[list[float]]:
    w, h = page_rect.width or 1.0, page_rect.height or 1.0
    out = []
    for r in lines:
        box = [min(max((r.x0 - page_rect.x0) / w, 0.0), 1.0), min(max((r.y0 - page_rect.y0) / h, 0.0), 1.0),
               min(max((r.x1 - page_rect.x0) / w, 0.0), 1.0), min(max((r.y1 - page_rect.y0) / h, 0.0), 1.0)]
        if box[2] > box[0] and box[3] > box[1]:
            out.append([round(v, 4) for v in box])
    return out


def _word_lines(words: list[tuple], idx: list[int]) -> list[pymupdf.Rect]:
    """Union the chosen words per text line (PyMuPDF block, line), in reading order. Per line, not by
    height alone, so a quote in one column never takes in words from the column beside it."""
    lines: dict[tuple[int, int], pymupdf.Rect] = {}
    for i in idx:
        x0, y0, x1, y1, _, block, line, *_ = words[i]
        r = pymupdf.Rect(x0, y0, x1, y1)
        if (block, line) in lines:
            lines[(block, line)].include_rect(r)
        else:
            lines[(block, line)] = r
    return list(lines.values())


def locate_boxes(page: pymupdf.Page, quote: str, repeated: set[str] | None = None) -> list[list[float]]:
    """Where `quote` sits on `page` as normalised line rects; [] when it cannot be found.

    First the token matcher over the page's words (the same normalisation as verification, so
    ligatures, hyphenation, footers and columns do not break it; a quote that runs onto the next page is
    boxed by the part on this page), then PyMuPDF's own search as a fallback."""
    quote = _clean_span(quote)
    if not quote:
        return []
    try:
        words = page.get_text("words")
        idx = quotes.match_words(quote, words, repeated=repeated, prefix_ok=True)
        if idx:
            return _normalised(_word_lines(words, idx), page.rect)
        hits = page.search_for(quote)
        if hits:
            return _normalised(_merge_lines(hits), page.rect)
        head = _merge_lines(page.search_for(_edge(quote, head=True)))
        tail = _merge_lines(page.search_for(_edge(quote, head=False)))
        if not head or not tail:
            return []
        start = head[0]
        after = [t for t in tail if t.y0 >= start.y0 - 1 and not (_same_line(t, start) and t.x1 < start.x0)]
        if not after:
            return []
        end = after[0]
        for t in after[1:]:  # the tail fragment may itself wrap: extend through its contiguous lines
            if t.y0 - end.y1 < end.height:
                end = t
            else:
                break
        lines = _span_lines(page, start, end)
        return _normalised(lines, page.rect) if lines else []
    except Exception:  # boxes are a nicety; never fail the review over them
        log.exception("locating boxes failed")
        return []


def box_from_2d(box_2d: list[int]) -> list[float] | None:
    """Gemini [ymin, xmin, ymax, xmax] on 0-1000 -> [x0, y0, x1, y1] on 0-1; None if implausible."""
    if len(box_2d) != 4:
        return None
    ymin, xmin, ymax, xmax = (min(max(v, 0), 1000) / 1000 for v in box_2d)
    if xmax <= xmin or ymax <= ymin:
        return None
    return [round(xmin, 4), round(ymin, 4), round(xmax, 4), round(ymax, 4)]


def boxes_from_llm(llm: LLM, doc: pymupdf.Document, findings: list[Finding]) -> LLMUsage:
    """Scanned PDFs: one bounded call per page that has findings, on a rendered image of that page."""
    usage = LLMUsage(model=llm.model)
    by_page: dict[int, list[Finding]] = {}
    for f in findings:
        by_page.setdefault(f.page, []).append(f)
    page_nos = sorted(by_page)[:MAX_BOX_PAGES]
    # pages are independent: run them in parallel so latency is the slowest page, not the sum.
    # Rendering is done up front because a PyMuPDF document is not thread-safe.
    pngs = {p: doc[p - 1].get_pixmap(dpi=110).tobytes("png") for p in page_nos}

    def call(pno: int):
        try:
            return pno, llm.generate(
                system=prompts.BOXES_SYSTEM,
                parts=[PdfPart(pngs[pno], "image/png"), prompts.boxes_user_text([f.quote for f in by_page[pno]])],
                schema=LLMBoxes, purpose="boxes")
        except Exception:
            log.exception("box lookup failed for page %d", pno)
            return pno, None

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(page_nos)))) as pool:
        results = list(pool.map(call, page_nos))
    for pno, res in results:
        if res is None:
            continue
        out, u = res
        usage.add(u)
        group = by_page[pno]
        for b in out.boxes:
            box = box_from_2d(b.box_2d)
            if box is not None and 0 <= b.index < len(group) and not group[b.index].boxes:
                group[b.index].boxes = [box]
    return usage


# ---- 4. chunking and indexing ----------------------------------------------------------------


def chunk_page(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paras = [_clean_span(p) for p in re.split(r"\n\s*\n|\n(?=\s*\d+(?:\.\d+)*[.)]?\s)", text)]
    paras = [p for p in paras if p]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if buf and len(buf) + 1 + len(p) > size:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf} {p}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def build_chunks(review: ReviewResult, pages: list[str]) -> list[Chunk]:
    owner = review.owner or DEMO_OWNER
    chunks: list[Chunk] = []
    for pno, text in enumerate(pages, start=1):
        for j, c in enumerate(chunk_page(text)):
            chunks.append(Chunk(id=f"{review.id}-p{pno}-{j}", review_id=review.id,
                                filename=review.filename, page=pno, text=c, owner=owner))
    for f in review.findings:
        chunks.append(Chunk(id=f"{review.id}-{f.id}", review_id=review.id, filename=review.filename,
                            page=f.page, text=f"{f.title}: {f.quote}", clause_type=f.clause_type,
                            finding_id=f.id, owner=owner))
    return chunks


def index_review(svc: Services, review: ReviewResult, pages: list[str]) -> int:
    chunks = build_chunks(review, pages)
    if not chunks:
        return 0
    vectors, tokens = svc.embedder.embed([c.text for c in chunks])
    for c, v in zip(chunks, vectors, strict=True):
        if len(v) != svc.embedder.dim:
            raise ValueError(f"embedding has {len(v)} dims, expected {svc.embedder.dim}")
        c.embedding = v
    svc.index.upsert(chunks)
    return tokens


# ---- 2 + 5. the review -----------------------------------------------------------------------


def review(svc: Services, pdf_bytes: bytes, filename: str, sample_id: str | None = None,
           owner: str = DEMO_OWNER, created_by: str | None = None,
           options: ReviewOptions | None = None) -> ReviewResult:
    """Review a PDF into workspace `owner`, run by member `created_by`, with the reader's `options`."""
    t0 = time.monotonic()
    doc = validate_pdf(pdf_bytes, svc.settings)
    try:
        return _review(svc, doc, pdf_bytes, filename, sample_id, t0, owner, created_by, options or ReviewOptions())
    finally:
        doc.close()


def _review(svc: Services, doc: pymupdf.Document, pdf_bytes: bytes, filename: str,
            sample_id: str | None, t0: float, owner: str, created_by: str | None,
            options: ReviewOptions) -> ReviewResult:
    page_count = doc.page_count
    trace = Trace(origin=t0)
    t_read = time.monotonic()
    pages = extract_pages(doc)

    usage = LLMUsage(model=svc.llm.model)
    scanned = needs_ocr(pages)
    if scanned:
        log.info("text layer thin (%d chars over %d pages); transcribing with the LLM",
                 sum(len(p.strip()) for p in pages), page_count)
        pages, u = transcribe(svc.llm, pdf_bytes, page_count)
        usage.add(u)
        if needs_ocr(pages):
            raise UserError(422, "no_text",
                            "We could not read any text in that PDF. Please upload a clearer copy.")
    trace.add("read_contract", f"Read the contract ({page_count} page{'s' if page_count != 1 else ''})",
              "scanned copy: read with AI text recognition" if scanned else None,
              (time.monotonic() - t_read) * 1000, start=t_read)

    home = svc.settings.home_jurisdiction
    tools = Toolbox(svc.kb, svc.company, svc.embedder, pages, owner=owner, overrides=svc.overrides, home=home)
    agent = run_agent(svc.llm, tools, pages, trace, options=options)
    out = agent.review
    usage.add(agent.usage)
    # the jurisdiction fixed during research wins over the model's final word; both default to "my"
    jurisdiction = agent.jurisdiction or out.jurisdiction.strip().lower()
    jurisdiction = jurisdiction if jurisdiction in ("my", "sg", "other") else "my"
    chosen = [f"{k}={len(v) if k == 'company_doc_ids' else v}" for k, v in options.model_dump().items()
              if v != ReviewOptions.model_fields[k].default]
    if chosen:  # what the reader set, shown with the other facts about how the contract was read
        trace.steps[0].detail = " | ".join(x for x in (trace.steps[0].detail, "set by the reader: " + ", ".join(chosen)) if x)
    if agent.outlined:
        trace.steps[0].detail = " | ".join(x for x in (trace.steps[0].detail, "long contract: planned from an outline, "
                                                       "reviewed in full") if x)
    if agent.governing_law:
        trace.steps[0].detail = " | ".join(
            x for x in (trace.steps[0].detail, f"governing law: {agent.governing_law} -> {jurisdiction}") if x)

    t_verify = time.monotonic()
    removed: list[RemovedClaim] = []
    consequences: dict[str, str] = {}
    kb_version = svc.kb_version  # the same content hash the provenance records
    findings, dropped, dropped_cites = verify_findings(out.findings, pages, agent.known,
                                                       kb_version=kb_version, removed=removed,
                                                       jurisdiction=jurisdiction, home=home,
                                                       law_by_id=svc.kb.law_by_id, consequences=consequences)
    missing = [m.model_copy() for m in out.missing_clauses]
    model_summary = [s.strip() for s in out.verdict.summary if s.strip()][:3]
    n_before = len(removed)
    kept_summary = grounding.ground_review(findings, missing, model_summary, pages, jurisdiction, removed)
    contract_text, numbers = "\n".join(pages), grounding.contract_numbers(pages)
    overall, headline, kept_summary = calibration.calibrate_review(
        findings, consequences, kept_summary, out.verdict.headline.strip(), pages, removed,
        lambda s: not grounding.ungrounded_numbers(s, numbers) and not grounding.unsupported_terms(s, contract_text))
    findings.sort(key=lambda f: (RISK_ORDER[f.risk], f.page))  # demoted findings move down
    for i, f in enumerate(findings, start=1):
        f.id = f"f{i}"
    n_grounding = len(removed) - n_before
    if overall != out.verdict.overall_risk:
        trace.steps[0].detail = " | ".join(x for x in (trace.steps[0].detail, (
            f"overall risk {overall}, from the findings (the model said {out.verdict.overall_risk})")) if x)
    add_caveats(findings, svc.kb.law_by_id)
    locate_company_boxes(svc, findings, owner)
    fill_cited(trace.steps, {c.ref for f in findings for c in f.citations})
    if scanned:
        usage.add(boxes_from_llm(svc.llm, doc, findings))
    else:
        repeated = page_furniture(pages)
        for f in findings:
            f.boxes = locate_boxes(doc[f.page - 1], f.quote, repeated)
    n_cites = sum(len(f.citations) for f in findings)
    trace.add("verify", "Checked every quote against the contract and every source against the library",
              f"kept {len(findings)} findings and {n_cites} sources; dropped {dropped} unsupported findings "
              f"and {dropped_cites} unverified sources; removed {n_grounding} statements not found in the contract",
              (time.monotonic() - t_verify) * 1000, start=t_verify)
    key_dates = build_key_dates(out.key_dates, findings, page_count)
    if len(headline) > 160:
        headline = headline[:157].rstrip() + "..."
    summary = kept_summary or [headline]
    if jurisdiction == "other":  # the reader must know the law checked is only a fallback
        law = (agent.governing_law or "a foreign law").rstrip(". ")
        summary = summary[:2] + [f"This contract is governed by {law}; we check it against "
                                 f"{JURISDICTION_ADJECTIVE.get(home, 'Malaysian')} law only as a fallback."]

    # the model that wrote the findings: the fallback when it answered the review call (the primary stalled)
    answered_by = usage.fallback_model if usage.fallback_model and "review" in usage.fallback_purposes else svc.llm.model
    if usage.fallback_purposes:
        trace.steps[0].detail = " | ".join(x for x in (trace.steps[0].detail, (
            f"answered by fallback model {usage.fallback_model} ({', '.join(dict.fromkeys(usage.fallback_purposes))}): "
            f"{svc.llm.model} did not respond")) if x)

    review_id = uuid.uuid4().hex[:12]
    chash = provenance.content_hash(pdf_bytes)
    earlier = svc.store.runs_of(chash, owner=owner)  # newest first
    result = ReviewResult(
        id=review_id, created_at=datetime.now(timezone.utc), filename=filename, sample_id=sample_id,
        contract_type=out.contract_type if options.contract_type == "auto" else options.contract_type, language=out.language.strip().lower()[:5] or "en",
        pages=page_count, parties=out.parties,
        verdict=Verdict(overall_risk=overall, headline=headline, summary=summary),
        findings=findings, missing_clauses=missing,
        metrics=Metrics(latency_ms=0, model=answered_by, dropped_unverified=dropped,
                        dropped_unverified_citations=dropped_cites),
        disclaimer=DISCLAIMER, jurisdiction=jurisdiction, trace=trace.steps, key_dates=key_dates,
        removed=removed,
        owner=owner, created_by=created_by, content_hash=chash, previous_review_id=earlier[0].id if earlier else None,
        provenance=provenance.build(answered_by, svc.kb_version, svc.prompt_version,
                                    svc.settings.app_version, options),
    )

    svc.blobs.put(f"uploads/{review_id}.pdf", pdf_bytes)

    embed_tokens = 0
    try:
        embed_tokens = index_review(svc, result, pages)
    except Exception:  # search is secondary; the review itself must still be returned
        log.exception("indexing failed for review %s", review_id)

    cost = estimate_cost(usage.model, usage.input_tokens, usage.output_tokens)
    if usage.fallback_model and cost is not None:
        fb = estimate_cost(usage.fallback_model, usage.fallback_input_tokens, usage.fallback_output_tokens)
        cost = cost + fb if fb is not None else None
    embed_cost = estimate_cost(svc.embedder.model, embed_tokens, 0)
    if cost is not None and embed_cost is not None:
        cost += embed_cost
    result.metrics = Metrics(
        latency_ms=int((time.monotonic() - t0) * 1000), model=answered_by,
        input_tokens=usage.total_input, output_tokens=usage.total_output,
        cost_usd_est=round(cost, 6) if cost is not None else None, dropped_unverified=dropped,
        dropped_unverified_citations=dropped_cites,
    )
    svc.store.save(result)
    return result


def copy_review(svc: Services, src: ReviewResult, pdf_bytes: bytes, owner: str,
                created_by: str | None = None) -> ReviewResult:
    """Put a copy of an already-reviewed bundled sample into `owner`'s workspace. No model call: the
    findings and their provenance are the original run's; the copy itself cost nothing."""
    t0 = time.monotonic()
    chash = provenance.content_hash(pdf_bytes)
    earlier = svc.store.runs_of(chash, owner=owner)
    result = src.model_copy(deep=True, update={
        "id": uuid.uuid4().hex[:12], "created_at": datetime.now(timezone.utc), "owner": owner,
        "created_by": created_by, "content_hash": chash, "previous_review_id": earlier[0].id if earlier else None})
    svc.blobs.put(f"uploads/{result.id}.pdf", pdf_bytes)
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            pages = extract_pages(doc)
        if not needs_ocr(pages):  # a scanned sample would need a model call to read; skip search for it
            index_review(svc, result, pages)
    except Exception:
        log.exception("indexing the copied sample failed for review %s", result.id)
    result.metrics = result.metrics.model_copy(update={
        "latency_ms": int((time.monotonic() - t0) * 1000), "input_tokens": 0, "output_tokens": 0,
        "cost_usd_est": 0.0})
    svc.store.save(result)
    return result


def audit_review(svc: Services, actor: str, result: ReviewResult, **extra) -> None:
    """'reviewed', or 're_reviewed' when the same content was reviewed before in this workspace."""
    p = result.provenance
    svc.audit.record(
        actor, "re_reviewed" if result.previous_review_id else "reviewed", result.id, result.filename,
        {"model": p.model if p else None, "kb_version": p.kb_version if p else None,
         "prompt_version": p.prompt_version if p else None, "findings": len(result.findings),
         "options": p.options.model_dump() if p and p.options else None,
         "latency_ms": result.metrics.latency_ms, "previous_review_id": result.previous_review_id, **extra},
        workspace_id=result.owner)


def history(svc: Services, review: ReviewResult) -> list[ReviewResult]:
    """Every run of this content in this workspace, newest first. A review without a hash is its own history."""
    if not review.content_hash or review.owner is None:
        return [review]
    runs = svc.store.runs_of(review.content_hash, owner=review.owner)
    return runs if any(r.id == review.id for r in runs) else [review, *runs]


def delete_review(svc: Services, review_id: str, owner: str) -> ReviewResult:
    """Remove a review, its PDF and its search chunks. Returns what was removed (for the audit line)."""
    r = svc.store.get(review_id, owner=owner)
    if r is None:
        raise UserError(404, "review_not_found", "We could not find that review.")
    svc.store.delete(review_id, owner=owner)
    svc.index.delete_review(review_id, owner=owner)
    svc.blobs.delete(f"uploads/{review_id}.pdf")
    return r


def _drop_workspace(svc: Services, workspace_id: str) -> tuple[int, int, int]:
    """Remove everything a workspace holds. Returns (reviews, company docs, audit events) removed."""
    review_ids = svc.store.delete_owner(workspace_id)
    svc.index.delete_owner(workspace_id)
    for rid in review_ids:
        svc.blobs.delete(f"uploads/{rid}.pdf")
    doc_ids = svc.company.delete_owner(workspace_id)
    for did in doc_ids:
        svc.blobs.delete(f"company/{did}.pdf")
    events = svc.audit.forget_workspace(workspace_id)
    svc.workspaces.delete(workspace_id)
    return len(review_ids), len(doc_ids), events


def delete_my_data(svc: Services, uid: str) -> tuple[dict[str, int], list[str]]:
    """Everything a user owns: the reviews they ran, their audit events, memberships and profile.

    A workspace where they are the only member goes entirely (reviews, PDFs, chunks, policies, events).
    A workspace with other people keeps its policies and everyone else's reviews. Refuses (409) while
    they are the only owner of a workspace that has other members. Returns counts and removed review ids."""
    mine = svc.workspaces.memberships(uid)
    for ws, m in mine:
        others = [x for x in svc.workspaces.members(ws.id) if x.uid != uid]
        if m.role == "owner" and others and not any(x.role == "owner" for x in others):
            raise UserError(409, "last_owner",
                            f"You are the only owner of {ws.name}. Make someone else an owner, or remove "
                            "the other members, before deleting your data.")
    counts = {"reviews": 0, "company_docs": 0, "workspaces": 0, "audit_events": 0}
    removed: list[str] = []
    for ws, _m in mine:
        if not [x for x in svc.workspaces.members(ws.id) if x.uid != uid]:
            removed += [r.id for r in svc.store.list(owner=ws.id, limit=1_000_000)]
            n_rev, n_docs, n_events = _drop_workspace(svc, ws.id)
            counts["audit_events"] += n_events
            counts["reviews"] += n_rev
            counts["company_docs"] += n_docs
            counts["workspaces"] += 1
        else:
            for r in [r for r in svc.store.list(owner=ws.id, limit=1_000_000) if r.created_by == uid]:
                delete_review(svc, r.id, ws.id)
                removed.append(r.id)
                counts["reviews"] += 1
            svc.workspaces.delete_member(ws.id, uid)
    counts["audit_events"] += svc.audit.forget(uid)
    svc.users.delete(uid)
    return counts, removed


# ---- negotiation and search ------------------------------------------------------------------


_CLAUSE_NO = re.compile(r"(?m)^[ \t]*(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?[ \t]+\S")
_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]")
MAX_EMAIL_WORDS = 220


def clause_label(page_text: str, quote: str, page: int) -> str:
    """'Clause 5.1 (page 2)' when a clause number precedes the quote on its page, else 'page 2'."""
    nq = normalise(quote)[:40]
    np_, idx = _norm_map(page_text)
    pos = np_.find(nq) if nq else -1
    if pos >= 0:
        nums = _CLAUSE_NO.findall(page_text[: idx[pos] + 1])
        if nums:
            return f"Clause {nums[-1]} (page {page})"
    return f"Page {page}"


def _email_problems(body: str) -> list[str]:
    problems = []
    if _PLACEHOLDER.search(body):
        problems.append("it contains a bracketed placeholder; write the real party names instead")
    if len(body.split()) > MAX_EMAIL_WORDS:
        problems.append(f"it is {len(body.split())} words; it must be under {MAX_EMAIL_WORDS}")
    return problems


def _page_texts(svc: Services, review: ReviewResult) -> list[str]:
    """Page text of the stored PDF, to find clause numbers. [] when the PDF is gone or unreadable."""
    data = svc.blobs.get(f"uploads/{review.id}.pdf")
    if data is None:
        return []
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return extract_pages(doc)
    except Exception:
        log.exception("could not re-read the stored PDF for review %s", review.id)
        return []


def negotiation(svc: Services, review: ReviewResult) -> NegotiationDraft:
    pages = _page_texts(svc, review)
    items = [
        {"finding_id": f.id, "title": f.title, "risk": f.risk, "quote": f.quote,
         "suggested_ask": f.suggested_ask,
         "where": f"Clause {f.clause_ref} (page {f.page})" if f.clause_ref
         else clause_label(pages[f.page - 1], f.quote, f.page) if 1 <= f.page <= len(pages)
         else f"Page {f.page}",
         # law is context only when it governs the contract; fallback law on a foreign-law contract is not
         "context": [c.title for c in f.citations if c.kind in ("law", "case")][:2]
         if review.jurisdiction != "other" else []}
        for f in review.findings if f.risk in ("high", "medium")
    ]
    items += [
        {"finding_id": f"m{i}", "title": f"Missing: {m.title}", "risk": "missing",
         "suggested_ask": m.suggested_ask}
        for i, m in enumerate(review.missing_clauses, start=1)
    ]
    if not items:
        raise UserError(422, "nothing_to_negotiate",
                        "This review has no high or medium findings or missing clauses to raise.")
    user_text = prompts.negotiation_user_text(review.filename, review.parties, items)
    out, _usage = svc.llm.generate(system=prompts.NEGOTIATION_SYSTEM, parts=[user_text],
                                   schema=LLMNegotiation, purpose="negotiation")
    body = _unescape_body(out.body)
    problems = _email_problems(body)
    if problems:  # one bounded retry; keep whichever draft has fewer problems
        log.info("negotiation draft needs a rewrite: %s", "; ".join(problems))
        retry, _ = svc.llm.generate(
            system=prompts.NEGOTIATION_SYSTEM,
            parts=[user_text, "Your previous draft was rejected: " + "; ".join(problems) + ". Rewrite it."],
            schema=LLMNegotiation, purpose="negotiation")
        retry_body = _unescape_body(retry.body)
        if len(_email_problems(retry_body)) <= len(problems):
            out, body = retry, retry_body
    valid = {it["finding_id"] for it in items}
    asks = [a for a in out.asks if a.finding_id in valid]
    seen = {a.finding_id for a in asks}
    # Guarantee one ask per item even if the model skipped one.
    asks += [NegotiationAsk(finding_id=it["finding_id"], ask=it["suggested_ask"])
             for it in items if it["finding_id"] not in seen]
    return NegotiationDraft(review_id=review.id, subject=out.subject.strip(), body=body, asks=asks)


def _unescape_body(body: str) -> str:
    """Gemini sometimes double-escapes newlines inside JSON strings ("\\n"); the email must be plain text."""
    if "\\n" in body and body.count("\n") <= body.count("\\n"):
        body = body.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return body.strip()


OVERLAP_MAX = 0.6
PER_REVIEW_MAX = 2


def _overlap(a: str, b: str) -> float:
    """Share of the shorter text's words that also appear in the other (1.0 = one contains the other)."""
    ta, tb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0


def collapse_hits(hits: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
    """One hit per passage and at most PER_REVIEW_MAX per review, finding passages first.

    Two hits are the same passage when they are in the same review and page and their text overlaps
    by more than OVERLAP_MAX; the finding wins over the raw chunk, otherwise the higher score.
    """
    kept: list[tuple[Chunk, float]] = []
    for c, score in sorted(hits, key=lambda h: h[1], reverse=True):
        for i, (k, ks) in enumerate(kept):
            if k.review_id == c.review_id and k.page == c.page and _overlap(k.text, c.text) > OVERLAP_MAX:
                if c.finding_id and not k.finding_id:
                    kept[i] = (c, max(score, ks))
                break
        else:
            kept.append((c, score))
    per_review: dict[str, list[tuple[Chunk, float]]] = {}
    for h in kept:
        per_review.setdefault(h[0].review_id, []).append(h)
    out = [h for group in per_review.values()
           for h in sorted(group, key=lambda h: (h[0].finding_id is None, -h[1]))[:PER_REVIEW_MAX]]
    return sorted(out, key=lambda h: h[1], reverse=True)


VECTOR_FETCH = 50  # nearest chunks the keyword pass re-scores
VECTOR_WEIGHT, KEYWORD_WEIGHT, PHRASE_BOOST = 0.65, 0.35, 0.15
_WORD = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    return _WORD.findall(normalise(text))


def hybrid_score(q_terms: set[str], q_phrase: str, text: str, vector_score: float) -> float:
    """Vector similarity merged with a keyword pass: the share of query terms found in the passage
    (normalised, stop words ignored), plus a boost when the whole query appears as an exact phrase."""
    words = _words(text)
    kw = len(q_terms & set(words)) / len(q_terms) if q_terms else 0.0
    phrase = 1.0 if q_phrase and f" {q_phrase} " in f" {' '.join(words)} " else 0.0
    return VECTOR_WEIGHT * max(vector_score, 0.0) + KEYWORD_WEIGHT * kw + PHRASE_BOOST * phrase


def search(svc: Services, q: str, owner: str, limit: int = 10) -> list[SearchHit]:
    """Hybrid search of one workspace: the nearest chunks by vector, re-scored by keyword overlap and an
    exact-phrase boost, then de-duplicated per passage."""
    vectors, _ = svc.embedder.embed([q], query=True)
    near = svc.index.query(vectors[0], max(limit * 5, VECTOR_FETCH), owner=owner)
    words = _words(q)
    terms = {w for w in words if w not in STOPWORDS and len(w) > 1}
    phrase = " ".join(words)
    scored = [(c, hybrid_score(terms, phrase, c.text, s)) for c, s in near]
    hits = collapse_hits(scored)[:limit]
    return [
        SearchHit(review_id=c.review_id, filename=c.filename, page=c.page,
                  text=c.text if len(c.text) <= 400 else c.text[:397].rstrip() + "...",
                  clause_type=c.clause_type, finding_id=c.finding_id, score=round(float(s), 4))
        for c, s in hits
    ]


# ---- company knowledge base ------------------------------------------------------------------


def add_company_doc(svc: Services, pdf_bytes: bytes, filename: str, owner: str = DEMO_OWNER) -> CompanyDoc:
    """Store a policy PDF, extract (OCR path if scanned), chunk, embed and index it."""
    doc = validate_pdf(pdf_bytes, svc.settings)
    try:
        page_count = doc.page_count
        pages = extract_pages(doc)
        if needs_ocr(pages):
            pages, _ = transcribe(svc.llm, pdf_bytes, page_count)
            if needs_ocr(pages):
                raise UserError(422, "no_text",
                                "We could not read any text in that PDF. Please upload a clearer copy.")
    finally:
        doc.close()
    doc_id = uuid.uuid4().hex[:12]
    title = re.sub(r"\.pdf$", "", filename or "", flags=re.I).strip() or "Company document"
    texts: list[tuple[int, str]] = [(pno, c) for pno, p in enumerate(pages, start=1) for c in chunk_page(p)]
    if not texts:
        raise UserError(422, "no_text", "We could not read any text in that PDF. Please upload a clearer copy.")
    vectors, _ = svc.embedder.embed([t for _, t in texts])
    chunks = []
    for j, ((pno, text), v) in enumerate(zip(texts, vectors, strict=True)):
        if len(v) != svc.embedder.dim:
            raise ValueError(f"embedding has {len(v)} dims, expected {svc.embedder.dim}")
        chunks.append(CompanyChunk(id=f"{doc_id}-{j}", doc_id=doc_id, title=title, page=pno, text=text,
                                   embedding=v))
    meta = CompanyDoc(id=doc_id, filename=filename or "document.pdf", title=title,
                      created_at=datetime.now(timezone.utc), pages=page_count)
    svc.blobs.put(f"company/{doc_id}.pdf", pdf_bytes)
    svc.company.put(meta, chunks, owner=owner)
    return meta


def delete_company_doc(svc: Services, doc_id: str, owner: str) -> CompanyDoc:
    doc = svc.company.get(doc_id, owner=owner)
    if doc is None or not svc.company.delete(doc_id, owner=owner):
        raise UserError(404, "company_doc_not_found", "We could not find that document.")
    svc.blobs.delete(f"company/{doc_id}.pdf")
    return doc
