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

import pymupdf
from rapidfuzz import fuzz

from app import prompts
from app.agent import Toolbox, Trace, run_agent
from app.config import DISCLAIMER, Settings, estimate_cost
from app.knowledge import Knowledge
from app.models import (
    RISK_ORDER, Citation, CompanyDoc, Finding, KeyDate, LLMBoxes, LLMCitation, LLMFinding, LLMKeyDate,
    LLMNegotiation, LLMTranscription, Metrics, NegotiationAsk, NegotiationDraft, ReviewResult, SearchHit, Verdict,
)
from app.ports import (LLM, BlobStore, Chunk, CompanyChunk, CompanyStore, Embedder, LLMUsage, PdfPart,
                       ReviewStore, SearchIndex)

log = logging.getLogger("cc.pipeline")

MIN_CHARS_PER_PAGE = 50
FUZZY_THRESHOLD = 90
MIN_QUOTE_CHARS = 12
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
    kb: Knowledge


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


def match_on_page(quote: str, page_text: str) -> str | None:
    """Return the matching original span of `page_text`, or None."""
    nq = normalise(quote)
    np_, idx = _norm_map(page_text)
    if len(nq) < MIN_QUOTE_CHARS or not np_:
        return None
    pos = np_.find(nq)
    if pos >= 0:
        return _clean_span(page_text[idx[pos]: idx[pos + len(nq) - 1] + 1])
    if len(nq) > len(np_):
        return None
    al = fuzz.partial_ratio_alignment(nq, np_, score_cutoff=FUZZY_THRESHOLD)
    if al is None or al.dest_end <= al.dest_start:
        return None
    start, end = idx[al.dest_start], idx[al.dest_end - 1] + 1
    # widen to whole words so the displayed quote never starts or ends mid-word
    while start > 0 and not page_text[start - 1].isspace():
        start -= 1
    while end < len(page_text) and not page_text[end].isspace():
        end += 1
    return _clean_span(page_text[start:end])


def verify_quote(quote: str, page: int, pages: list[str]) -> tuple[int, str] | None:
    """Find `quote` on the claimed page, else on page-1 / page+1. Returns (page, exact_span)."""
    for candidate in (page, page - 1, page + 1):
        if 1 <= candidate <= len(pages):
            span = match_on_page(quote, pages[candidate - 1])
            if span is not None:
                return candidate, span
    return None


def verify_citations(raw: list[LLMCitation], known: dict[str, list[Citation]]) -> tuple[list[Citation], int]:
    """Keep a citation only if a tool returned its ref in this run, and take every displayed field
    (kind, title, excerpt, url, jurisdiction, page) from that KB record, never from the model."""
    kept: list[Citation] = []
    dropped = 0
    for c in raw:
        records = known.get(c.ref.strip())
        if not records:
            dropped += 1
            log.info("dropped unverified citation: %r", c.ref)
            continue
        rec = records[0]
        if rec.kind == "company":  # one document can come back as several passages; keep the first
            rec = next((r for r in records if r.excerpt and c.excerpt.strip()[:40] in r.excerpt), rec)
        if any(k.ref == rec.ref and k.page == rec.page for k in kept):
            continue  # the same source cited twice for one finding
        kept.append(rec.model_copy())
    return kept, dropped


def verify_findings(raw: list[LLMFinding], pages: list[str],
                    known: dict[str, list[Citation]] | None = None) -> tuple[list[Finding], int, int]:
    """Quote verification (drop a finding with no real quote) and citation verification.

    Returns (findings, dropped_findings, dropped_citations).
    """
    known = known or {}
    kept: list[tuple[LLMFinding, int, str]] = []
    dropped = 0
    for f in raw:
        hit = verify_quote(f.quote, f.page, pages)
        if hit is None:
            dropped += 1
            log.info("dropped unverified finding: %r p%s", f.title, f.page)
            continue
        kept.append((f, hit[0], hit[1]))
    kept.sort(key=lambda t: (RISK_ORDER[t[0].risk], t[1]))
    findings: list[Finding] = []
    dropped_citations = 0
    for i, (f, page, span) in enumerate(kept, start=1):
        cites, n = verify_citations(f.citations, known)
        dropped_citations += n
        # a policy breach is a claim about the user's own rules: keep it only if one is cited
        breach = f.policy_breach.strip() if any(c.kind == "company" for c in cites) else ""
        findings.append(Finding(
            id=f"f{i}", clause_type=f.clause_type, title=f.title, risk=f.risk, quote=span, page=page,
            why_risky=f.why_risky, standard=f.standard, suggested_ask=f.suggested_ask,
            baseline_ref=f.baseline_ref or None, citations=cites, policy_breach=breach or None))
    return findings, dropped, dropped_citations


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
            log.info("dropped key date %r (page %s)", k.label, k.page)
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


def locate_boxes(page: pymupdf.Page, quote: str) -> list[list[float]]:
    """Where `quote` sits on `page` as normalised line rects; [] when it cannot be found."""
    quote = _clean_span(quote)
    if not quote:
        return []
    try:
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
    chunks: list[Chunk] = []
    for pno, text in enumerate(pages, start=1):
        for j, c in enumerate(chunk_page(text)):
            chunks.append(Chunk(id=f"{review.id}-p{pno}-{j}", review_id=review.id,
                                filename=review.filename, page=pno, text=c))
    for f in review.findings:
        chunks.append(Chunk(id=f"{review.id}-{f.id}", review_id=review.id, filename=review.filename,
                            page=f.page, text=f"{f.title}: {f.quote}", clause_type=f.clause_type,
                            finding_id=f.id))
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


def review(svc: Services, pdf_bytes: bytes, filename: str, sample_id: str | None = None) -> ReviewResult:
    t0 = time.monotonic()
    doc = validate_pdf(pdf_bytes, svc.settings)
    try:
        return _review(svc, doc, pdf_bytes, filename, sample_id, t0)
    finally:
        doc.close()


def _review(svc: Services, doc: pymupdf.Document, pdf_bytes: bytes, filename: str,
            sample_id: str | None, t0: float) -> ReviewResult:
    page_count = doc.page_count
    trace = Trace()
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
              (time.monotonic() - t_read) * 1000)

    agent = run_agent(svc.llm, Toolbox(svc.kb, svc.company, svc.embedder, pages), pages, trace)
    out = agent.review
    usage.add(agent.usage)

    t_verify = time.monotonic()
    findings, dropped, dropped_cites = verify_findings(out.findings, pages, agent.known)
    if scanned:
        usage.add(boxes_from_llm(svc.llm, doc, findings))
    else:
        for f in findings:
            f.boxes = locate_boxes(doc[f.page - 1], f.quote)
    n_cites = sum(len(f.citations) for f in findings)
    trace.add("verify", "Checked every quote against the contract and every source against the library",
              f"kept {len(findings)} findings and {n_cites} sources; dropped {dropped} unsupported findings "
              f"and {dropped_cites} unverified sources", (time.monotonic() - t_verify) * 1000)
    key_dates = build_key_dates(out.key_dates, findings, page_count)
    jurisdiction = out.jurisdiction.strip().lower()
    jurisdiction = jurisdiction if jurisdiction in ("my", "sg", "other") else "my"
    headline = out.verdict.headline.strip()
    if len(headline) > 160:
        headline = headline[:157].rstrip() + "..."
    summary = [s.strip() for s in out.verdict.summary if s.strip()][:3] or [headline]

    review_id = uuid.uuid4().hex[:12]
    result = ReviewResult(
        id=review_id, created_at=datetime.now(timezone.utc), filename=filename, sample_id=sample_id,
        contract_type=out.contract_type, language=out.language.strip().lower()[:5] or "en",
        pages=page_count, parties=out.parties,
        verdict=Verdict(overall_risk=out.verdict.overall_risk, headline=headline, summary=summary),
        findings=findings, missing_clauses=out.missing_clauses,
        metrics=Metrics(latency_ms=0, model=svc.llm.model, dropped_unverified=dropped,
                        dropped_unverified_citations=dropped_cites),
        disclaimer=DISCLAIMER, jurisdiction=jurisdiction, trace=trace.steps, key_dates=key_dates,
    )

    svc.blobs.put(f"uploads/{review_id}.pdf", pdf_bytes)

    embed_tokens = 0
    try:
        embed_tokens = index_review(svc, result, pages)
    except Exception:  # search is secondary; the review itself must still be returned
        log.exception("indexing failed for review %s", review_id)

    cost = estimate_cost(usage.model, usage.input_tokens, usage.output_tokens)
    embed_cost = estimate_cost(svc.embedder.model, embed_tokens, 0)
    if cost is not None and embed_cost is not None:
        cost += embed_cost
    result.metrics = Metrics(
        latency_ms=int((time.monotonic() - t0) * 1000), model=usage.model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        cost_usd_est=round(cost, 6) if cost is not None else None, dropped_unverified=dropped,
        dropped_unverified_citations=dropped_cites,
    )
    svc.store.save(result)
    return result


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
         "where": clause_label(pages[f.page - 1], f.quote, f.page) if 1 <= f.page <= len(pages)
         else f"Page {f.page}",
         "context": [c.title for c in f.citations if c.kind in ("law", "case")][:2]}
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


def search(svc: Services, q: str, limit: int = 10) -> list[SearchHit]:
    vectors, _ = svc.embedder.embed([q], query=True)
    # over-fetch: neighbouring chunks and the finding that quotes them crowd the top of the list
    hits = collapse_hits(svc.index.query(vectors[0], max(limit * 5, 30)))[:limit]
    return [
        SearchHit(review_id=c.review_id, filename=c.filename, page=c.page,
                  text=c.text if len(c.text) <= 400 else c.text[:397].rstrip() + "...",
                  clause_type=c.clause_type, finding_id=c.finding_id, score=round(float(s), 4))
        for c, s in hits
    ]


# ---- company knowledge base ------------------------------------------------------------------


def add_company_doc(svc: Services, pdf_bytes: bytes, filename: str) -> CompanyDoc:
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
    svc.company.put(meta, chunks)
    return meta


def delete_company_doc(svc: Services, doc_id: str) -> None:
    if not svc.company.delete(doc_id):
        raise UserError(404, "company_doc_not_found", "We could not find that document.")
    svc.blobs.delete(f"company/{doc_id}.pdf")
