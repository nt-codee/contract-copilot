"""Google Cloud adapters: Gemini on Vertex AI, GCS, Firestore (documents + vector search).

SDKs are imported lazily inside constructors so CLOUD=local LLM=fake never loads them.
"""

from __future__ import annotations

import logging
import copy
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from app.config import EMBED_DIM, Settings
from app.budget import BudgetError, cost_micros
from app.models import AuditEvent, CompanyDoc, Identity, Invite, Member, ReviewResult, User, Workspace
from app.ports import (ChatMessage, Chunk, CompanyChunk, InvalidToken, LLMUsage, PdfPart, T, ToolCall,
                       ToolSpec, UpstreamError)

log = logging.getLogger("cc.gcp")

# SDK-level HTTP retries on these statuses and on timeouts. Generation is bounded hard: a call that
# stalls for 45 s is abandoned and retried, at most 3 tries (waits 2 s, 4 s). Embeddings get 5 tries.
LLM_ATTEMPTS = 3
EMBED_ATTEMPTS = 5
# Per-call time budget: a base for a normal contract, plus time for each 1,000 input tokens above a free
# allowance, up to a cap. A fixed budget cut off long contracts: msa-10 (15 pages, 11,000 input tokens in
# the planning turn) was cancelled at 20 s every time ("499 The operation was cancelled"), while a stalled
# call on a normal contract should still be abandoned and retried within a few tens of seconds.
GENERATE_TIMEOUT_MS = 75_000  # review call, normal contract: 5-38 s (outputs 2-3k tokens). Cloud Run logs showed
# this call hitting Vertex 504 DEADLINE_EXCEEDED three times in a row at the old 35 s cap, so one longer try
# (two at most) beats three short ones that all end the same way.
REVIEW_ATTEMPTS = 2
AGENT_TIMEOUT_MS = 20_000  # tool-planning turn, normal contract: 3-4 s, so a stall retries quickly
TRANSCRIBE_TIMEOUT_MS = 120_000  # a whole scanned PDF in one call legitimately takes longer
# Short calls (a vendor reply, the highlight boxes on a scanned page): a small prompt, a small answer, normally 2-8 s.
# A stall is abandoned at 20 s and retried (3 tries), instead of sharing the review budget: a reply once took 80 s
# (one 504, then a 75 s try) for a 773-token prompt.
SHORT_PURPOSES = ("negotiation", "boxes")
SHORT_TIMEOUT_MS = 20_000
EMBED_TIMEOUT_MS = 30_000
FREE_TOKENS = {"agent": 4_000, "review": 6_000}  # input size the base budget already covers
MS_PER_1K_TOKENS = {"agent": 2_500, "review": 2_000}
MAX_TIMEOUT_MS = {"agent": 60_000, "review": 120_000}
CHARS_PER_TOKEN = 3.5  # conservative for English and Bahasa Malaysia; the real count is only known after the call
# 499 is what the server answers when our own deadline expires, so it is retried like a timeout.
RETRY_STATUSES = [429, 499, 500, 503, 504]
EMBED_WORKERS = 8
# A stalled agent or review call is retried on the fallback model (LLM_FALLBACK_MODEL), not the same one: the
# primary gets ONE try; on 504, 499, 429 or a client timeout the remaining tries (same request, same per-try
# budget) go to the fallback. Total tries per call are unchanged, so the worst case stays within the budgets
# above. 500 and 503 still retry on the primary.
FALLBACK_PURPOSES = ("agent", "review")
FALLBACK_STATUSES = (429, 499, 504)
# A model accepts only its own thought signatures; this documented sentinel lets it take turns another model wrote.
FOREIGN_SIGNATURE = b"skip_thought_signature_validator"
QUOTA_BACKOFF_S = (6, 12, 24)  # extra waits after a 429 that survived the SDK retries (adds at most ~42 s)


def _retry_options(attempts: int = LLM_ATTEMPTS, timeout_ms: int = GENERATE_TIMEOUT_MS):
    from google.genai import types

    return types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=attempts, initial_delay=2.0, max_delay=20.0,
                                             exp_base=2.0, http_status_codes=RETRY_STATUSES),
    )


def call_timeout_ms(purpose: str, size_chars: int) -> int:
    """The time budget of one generate call, from what it sends. Scanned-PDF transcription is fixed."""
    if purpose == "transcribe":
        return TRANSCRIBE_TIMEOUT_MS
    if purpose in SHORT_PURPOSES:
        return SHORT_TIMEOUT_MS
    kind = "agent" if purpose == "agent" else "review"
    base = AGENT_TIMEOUT_MS if kind == "agent" else GENERATE_TIMEOUT_MS
    extra_tokens = max(0.0, size_chars / CHARS_PER_TOKEN - FREE_TOKENS[kind])
    return int(min(base + extra_tokens / 1000 * MS_PER_1K_TOKENS[kind], MAX_TIMEOUT_MS[kind]))


def call_attempts(purpose: str) -> int:
    """Tries per generate call: three for the agent turn, transcription and the short calls (they are quick, so a stall
    is retried fast); two for the review, which drafts one big answer (two long tries beat three that end alike)."""
    return LLM_ATTEMPTS if purpose in ("agent", "transcribe", *SHORT_PURPOSES) else REVIEW_ATTEMPTS


def _chars(system: str, contents) -> int:
    """Characters sent: the system prompt plus every text part, function call and function response."""
    total = len(system)
    for c in contents:
        for p in (getattr(c, "parts", None) or [c]):
            total += len(getattr(p, "text", None) or "")
            for fn in (getattr(p, "function_call", None), getattr(p, "function_response", None)):
                if fn is not None:
                    total += len(str(getattr(fn, "args", None) or getattr(fn, "response", None) or ""))
    return total


def stalled(e: Exception) -> bool:
    """A failure the fallback model should answer: the endpoint is stalled or out of quota, not refusing."""
    if getattr(e, "code", None) in FALLBACK_STATUSES:
        return True
    name = type(e).__name__.lower()
    return "timeout" in name or "timed out" in str(e).lower()


NO_OUTPUT_STATUSES = (429, 499, 503, 504)  # DEADLINE_EXCEEDED, CANCELLED, UNAVAILABLE, RESOURCE_EXHAUSTED


def no_output_failure(e: Exception) -> bool:
    """True only when the provider definitely produced no output: one of NO_OUTPUT_STATUSES, or our own client
    timeout (a Timeout exception class: nothing was received). Anything else (500, resets, protocol errors,
    unknown exceptions) is ambiguous and keeps its full hold."""
    code = getattr(e, "code", None)
    if type(code) is int:
        return code in NO_OUTPUT_STATUSES
    return any("timeout" in cls.__name__.lower() for cls in type(e).__mro__)


def _foreign_turns(contents):
    """The same contents with every thought signature replaced by FOREIGN_SIGNATURE (for another model)."""
    out = []
    for c in contents:
        parts = getattr(c, "parts", None)
        if not parts or not any(getattr(p, "thought_signature", None) for p in parts):
            out.append(c)
            continue
        out.append(c.model_copy(update={"parts": [
            p.model_copy(update={"thought_signature": FOREIGN_SIGNATURE}) if getattr(p, "thought_signature", None)
            else p for p in parts]}))
    return out


def _upstream_failure(what: str, e: Exception, user_message: str) -> UpstreamError:
    """Log the upstream status and message for operators; the user only sees `user_message`."""
    status = getattr(e, "code", None)
    status = status if isinstance(status, int) else None
    log.warning("%s failed after retries: status=%s message=%s", what, status,
                str(getattr(e, "message", None) or e)[:300])
    return UpstreamError(user_message, status)


class GeminiLLM:
    fallback_model = ""  # set from LLM_FALLBACK_MODEL in __init__
    _local = threading.local()  # which model answered the current thread's last call

    def with_budget(self, account):
        scoped = copy.copy(self)
        scoped._account_budget = account
        return scoped

    def __init__(self, settings: Settings):
        from google import genai

        self.model = settings.gemini_model
        self.fallback_model = settings.llm_fallback_model if settings.llm_fallback_model != self.model else ""
        self._thinking = settings.gemini_thinking
        self.calls = 0
        self._client = genai.Client(vertexai=True, project=settings.gcp_project,
                                    location=settings.gemini_location, http_options=_retry_options())
        # Optional: write each raw response to RECORD_DIR/<purpose>.json, to refresh test fixtures.
        self._record_dir = os.environ.get("RECORD_DIR") or None

    def _config(self, system: str, purpose: str = "", size_chars: int = 0, **extra):
        from google.genai import types

        timeout = call_timeout_ms(purpose, size_chars)
        attempts = call_attempts(purpose)
        return types.GenerateContentConfig(
            system_instruction=system,
            http_options=_retry_options(attempts, timeout),
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking) if self._thinking else None,
            **extra,
        )

    def _call(self, purpose: str, contents, config):
        if getattr(self, "_account_budget", None) is not None:
            return self._metered_call(purpose, contents, config)
        self.calls += 1
        t0 = time.monotonic()
        resp = None
        model = self.model
        self._local.answered = model
        opts = getattr(config, "http_options", None)
        tries = opts.retry_options.attempts if opts is not None and opts.retry_options else 1
        if self.fallback_model and purpose in FALLBACK_PURPOSES and tries > 1:
            first = config.model_copy(update={"http_options": _retry_options(1, opts.timeout)})
            try:
                resp = self._client.models.generate_content(model=model, contents=contents, config=first)
            except Exception as e:
                rest = config.model_copy(update={"http_options": _retry_options(tries - 1, opts.timeout)})
                if stalled(e):
                    log.warning("gemini %s on %s stalled (status=%s); retrying on fallback model %s", purpose, model,
                                getattr(e, "code", None), self.fallback_model)
                    model, contents = self.fallback_model, _foreign_turns(contents)
                elif getattr(e, "code", None) not in RETRY_STATUSES:
                    raise _upstream_failure(f"gemini {purpose} call", e,
                                            "The AI service did not respond. Please try again in a minute.") from e
                config = rest
        for wait in (*QUOTA_BACKOFF_S, None):
            if resp is not None:
                break
            try:
                resp = self._client.models.generate_content(model=model, contents=contents, config=config)
                break
            except Exception as e:
                # The SDK has already retried (3 tries, 2 s and 4 s apart), which is too short for a quota
                # window. A 429 gets a few longer waits; every other failure ends here as before.
                if getattr(e, "code", None) == 429 and wait is not None:
                    pause = wait + random.uniform(0, 2)
                    log.warning("gemini %s quota exhausted (429); waiting %.0fs before trying again", purpose, pause)
                    time.sleep(pause)
                    continue
                raise _upstream_failure(f"gemini {purpose} call", e,
                                        "The AI service did not respond. Please try again in a minute.") from e
        self._local.answered = model
        um = resp.usage_metadata
        log.info("gemini %s on %s: %.1fs (in %s, out %s, thinking %s tokens)", purpose, model, time.monotonic() - t0,
                 um.prompt_token_count if um else "?", um.candidates_token_count if um else "?",
                 um.thoughts_token_count if um else "?")
        return resp

    def _metered_call(self, purpose, contents, config):
        """Exactly one reservation per actual HTTP attempt; no hidden SDK generation retries."""
        from google.genai import types

        account = self._account_budget
        self.calls += 1
        caps = {"agent": 4096, "review": 16384, "transcribe": 32768, "negotiation": 4096, "boxes": 2048}
        if purpose not in caps:
            account.fail(BudgetError(503, "budget_unavailable", "This AI operation has no bounded budget. Please try again later."))
        opts = config.http_options
        single = config.model_copy(update={"max_output_tokens": caps[purpose], "candidate_count": 1,
                                           "automatic_continuation": False,
                                           "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
                                           "http_options": _retry_options(1, opts.timeout)})
        model, remaining, attempt, quota = self.model, call_attempts(purpose), 0, 0
        group = remaining
        self._local.answered = model
        while True:
            account.require()
            try:
                cost_micros(model, 0)  # reject unknown prices before even requesting an unbilled count
                generation = types.GenerationConfig(**{k: v for k, v in single.model_dump(exclude_none=True).items()
                                                         if k in types.GenerationConfig.model_fields})
                count = self._client.models.count_tokens(model=model, contents=contents,
                    config=types.CountTokensConfig(system_instruction=single.system_instruction, tools=single.tools,
                                                   generation_config=generation,
                                                   http_options=_retry_options(1, opts.timeout)))
                tokens = count.total_tokens
                if type(tokens) is not int or tokens <= 0:
                    raise ValueError("unknown input token count")
            except BudgetError as exc:
                account.fail(exc)
            except Exception:
                account.fail(BudgetError(503, "budget_unavailable", "AI token accounting is unavailable. Please try again later."))
            reservation = account.reserve(model, tokens, caps[purpose])
            try:
                resp = self._client.models.generate_content(model=model, contents=contents, config=single)
            except Exception as exc:
                # A failed attempt can be billed: its full reservation stays held, unless the provider clearly
                # returned nothing, when only the input cost is kept (capped per account per day).
                if no_output_failure(exc):
                    account.release_failed(reservation, model, tokens)
                remaining -= 1
                if attempt == 0 and self.fallback_model and purpose in FALLBACK_PURPOSES and stalled(exc):
                    model, contents = self.fallback_model, _foreign_turns(contents)
                    group = max(1, call_attempts(purpose) - 1)
                attempt += 1
                retryable = getattr(exc, "code", None) in RETRY_STATUSES or stalled(exc)
                if remaining and retryable:
                    time.sleep(min(2 ** min(attempt, 3), 20))
                    continue
                if getattr(exc, "code", None) == 429 and quota < len(QUOTA_BACKOFF_S):
                    time.sleep(QUOTA_BACKOFF_S[quota])
                    quota += 1
                    remaining = group
                    continue
                raise _upstream_failure(f"gemini {purpose} call", exc,
                                        "The AI service did not respond. Please try again in a minute.") from exc
            usage = resp.usage_metadata
            # No refund for absent/partial usage, even on a syntactically successful response.
            observed = [getattr(usage, k, None) for k in
                        ("prompt_token_count", "candidates_token_count", "thoughts_token_count")]
            if all(type(n) is int and n >= 0 for n in observed):
                account.settle(reservation, model, observed[0], observed[1] + observed[2])
            self._local.answered = model
            return resp

    def _usage(self, resp, purpose: str = "") -> LLMUsage:
        um = resp.usage_metadata
        tin = (um.prompt_token_count or 0) if um else 0
        # thinking tokens are billed as output
        tout = ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0
        answered = getattr(self._local, "answered", self.model)
        if answered != self.model:
            return LLMUsage(model=self.model, fallback_model=answered, fallback_input_tokens=tin,
                            fallback_output_tokens=tout, fallback_purposes=[purpose or "?"])
        return LLMUsage(model=self.model, input_tokens=tin, output_tokens=tout)

    def chat(self, *, system: str, messages: list[ChatMessage], tools: list[ToolSpec],
             purpose: str, force_tools: bool = False) -> tuple[ChatMessage, LLMUsage]:
        """One function-calling turn. We run the loop ourselves, so automatic calling is off."""
        from google.genai import types

        contents = [self._content(m) for m in messages]
        config = self._config(
            system, purpose, _chars(system, contents),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY" if force_tools else "AUTO")),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.parameters) for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self._call(purpose, contents, config)
        cands = resp.candidates or []
        if not cands or cands[0].content is None:
            log.warning("gemini %s returned no content (finish_reason=%s)", purpose,
                        cands[0].finish_reason if cands else None)
            raise UpstreamError("The AI service returned an unexpected answer. Please try again.")
        content = cands[0].content
        parts = content.parts or []
        calls = [ToolCall(name=p.function_call.name, args=dict(p.function_call.args or {}),
                          id=p.function_call.id) for p in parts if p.function_call]
        text = "".join(p.text for p in parts if p.text and not p.thought)
        return ChatMessage(role="model", text=text, tool_calls=calls, raw=content), self._usage(resp, purpose)

    @staticmethod
    def _content(m: ChatMessage):
        from google.genai import types

        if m.role == "model" and m.raw is not None:
            return m.raw  # as returned, so thought signatures go back unchanged
        if m.role == "model":
            parts = ([types.Part.from_text(text=m.text)] if m.text else []) + [
                types.Part(function_call=types.FunctionCall(name=c.name, args=c.args, id=c.id))
                for c in m.tool_calls]
            return types.Content(role="model", parts=parts)
        if m.role == "tool":
            return types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(name=r.name, response=r.result, id=r.id))
                for r in m.tool_results])
        return types.Content(role="user", parts=[types.Part.from_text(text=m.text)])

    def generate(self, *, system: str, parts: list[str | PdfPart], schema: type[T],
                 purpose: str) -> tuple[T, LLMUsage]:
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=p.data, mime_type=p.mime_type) if isinstance(p, PdfPart)
            else types.Part.from_text(text=p)
            for p in parts
        ]
        config = self._config(system, purpose, _chars(system, contents),
                              response_mime_type="application/json", response_schema=schema)
        resp = self._call(purpose, contents, config)

        text = resp.text or ""
        if self._record_dir:
            Path(self._record_dir).mkdir(parents=True, exist_ok=True)
            Path(self._record_dir, f"{purpose}.json").write_text(text, encoding="utf-8")
        try:
            parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(text)
        except ValidationError as e:
            # error locations and types only: the full message echoes the model's output, which holds contract text
            log.error("gemini %s returned an unexpected shape: %s", purpose,
                      "; ".join(f"{'.'.join(map(str, x['loc']))}:{x['type']}" for x in e.errors())[:500])
            raise UpstreamError("The AI service returned an unexpected answer. Please try again.") from e
        return parsed, self._usage(resp, purpose)


class GeminiEmbedder:
    """gemini-embedding-2 accepts ONE content per request (verified 2026-10-18), so we fan out."""

    def __init__(self, settings: Settings):
        from google import genai

        self.model = settings.embed_model
        self.dim = EMBED_DIM
        self.calls = 0
        self._client = genai.Client(vertexai=True, project=settings.gcp_project,
                                    location=settings.embed_location,
                                    http_options=_retry_options(EMBED_ATTEMPTS, EMBED_TIMEOUT_MS))

    def with_budget(self, account):
        scoped = copy.copy(self)
        scoped._account_budget = account
        return scoped

    def _one(self, text: str, task: str) -> tuple[list[float], int]:
        from google.genai import types

        self.calls += 1
        account = getattr(self, "_account_budget", None)
        if account is not None:
            # Text-only tokenizer upper bound: one token per UTF-8 byte plus 1024 for fixed task framing.
            # This intentionally exceeds observed text token counts; no 4-chars/token underestimate.
            bound = len(text.encode("utf-8")) + 1024
            for attempt in range(EMBED_ATTEMPTS):
                hold = account.reserve(self.model, bound)
                try:
                    r = self._client.models.embed_content(model=self.model, contents=text,
                        config=types.EmbedContentConfig(output_dimensionality=self.dim, task_type=task,
                                                       http_options=_retry_options(1, EMBED_TIMEOUT_MS)))
                    break
                except Exception as exc:
                    if attempt + 1 < EMBED_ATTEMPTS and (getattr(exc, "code", None) in RETRY_STATUSES or stalled(exc)):
                        time.sleep(min(2 ** (attempt + 1), 20))
                        continue
                    raise
            emb = r.embeddings[0]
            observed = getattr(getattr(emb, "statistics", None), "token_count", None)
            if isinstance(observed, (int, float)) and math.isfinite(observed) and observed >= 0:
                tokens = math.ceil(observed)
                account.settle(hold, self.model, tokens)
            else:
                tokens = bound  # no observed usage: keep reservation in full
            return list(emb.values), tokens
        r = self._client.models.embed_content(
            model=self.model, contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self.dim, task_type=task),
        )
        emb = r.embeddings[0]
        tokens = int(emb.statistics.token_count) if emb.statistics and emb.statistics.token_count else len(text) // 4
        return list(emb.values), tokens

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[list[list[float]], int]:
        task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        try:
            with ThreadPoolExecutor(max_workers=min(EMBED_WORKERS, max(len(texts), 1))) as pool:
                results = list(pool.map(lambda t: self._one(t, task), texts))
        except BudgetError:
            raise
        except Exception as e:
            raise _upstream_failure("gemini embedding", e,
                                    "Search is unavailable right now. Please try again in a minute.") from e
        vectors = [v for v, _ in results]
        for v in vectors:
            if len(v) != self.dim:
                raise UpstreamError("Search is misconfigured (embedding size mismatch).")
        return vectors, sum(t for _, t in results)


class GcsBlobStore:
    def __init__(self, settings: Settings):
        from google.cloud import storage

        if not settings.upload_bucket:
            raise RuntimeError("UPLOAD_BUCKET must be set when CLOUD=gcp")
        self._bucket = storage.Client(project=settings.gcp_project).bucket(settings.upload_bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> None:
        self._bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(key).download_as_bytes()
        except NotFound:
            return None

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(key).delete()
        except NotFound:
            pass


def _firestore_client(settings: Settings):
    from google.cloud import firestore

    return firestore.Client(project=settings.gcp_project, database=settings.firestore_db)


def _eq(field: str, value):
    from google.cloud.firestore_v1.base_query import FieldFilter

    return FieldFilter(field, "==", value)


def _delete_refs(db, refs: list) -> int:
    for start in range(0, len(refs), 400):  # Firestore batch limit is 500 writes
        batch = db.batch()
        for r in refs[start:start + 400]:
            batch.delete(r)
        batch.commit()
    return len(refs)


def _iso(dt: datetime) -> str:
    """Prefix-comparable with the ISO strings pydantic stores in `created_at` / `at`."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ---- auth ---------------------------------------------------------------------------------------


class _CachedRequest:
    """google-auth transport that remembers GET responses (the Identity Platform signing certs) for `ttl` seconds,
    so a verification is a local signature check, not a network call."""

    def __init__(self, ttl: float = 3600.0):
        from google.auth.transport import requests as google_requests

        self._inner = google_requests.Request()
        self._ttl = ttl
        self._cache: dict[str, tuple[float, object]] = {}

    def __call__(self, url, method="GET", body=None, headers=None, timeout=120, **kwargs):
        if method == "GET":
            hit = self._cache.get(url)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
        resp = self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        if method == "GET" and getattr(resp, "status", 200) == 200:
            self._cache[url] = (time.monotonic(), resp)
        return resp


class IdentityPlatformVerifier:
    """Verifies a Google Cloud Identity Platform ID token: issuer https://securetoken.google.com/<project>,
    audience <project>. google-auth's `verify_firebase_token` performs exactly that check."""

    def __init__(self, settings: Settings):
        self._project = settings.gcp_project
        self._request = _CachedRequest()

    def verify(self, token: str) -> Identity:
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_firebase_token(token, self._request, audience=self._project)
        except Exception as e:  # ValueError for a bad/expired token; transport errors fall here too
            # Never log the exception text: google-auth puts the token itself in some messages
            # ("Wrong number of segments in token: <token>", "Can't parse segment: <segment>").
            log.info("token rejected: %s", type(e).__name__)
            raise InvalidToken("token rejected") from None
        if not claims:
            raise InvalidToken("token not verified")
        # verify_firebase_token checks the signature (Google's securetoken keys), expiry and audience; also pin the issuer.
        if claims.get("iss") != f"https://securetoken.google.com/{self._project}":
            log.info("token rejected: wrong issuer")
            raise InvalidToken("wrong issuer")
        uid = claims.get("user_id") or claims.get("sub")
        email = claims.get("email")
        if not uid or not email:
            raise InvalidToken("token has no uid or email")
        return Identity(uid=str(uid), email=str(email), name=claims.get("name"),
                        photo_url=claims.get("picture"), email_verified=bool(claims.get("email_verified")),
                        hd=(str(claims["hd"]).lower() if claims.get("hd") else None))


# ---- reviews ------------------------------------------------------------------------------------


def _to_firestore(review: ReviewResult) -> dict:
    """Firestore rejects arrays of arrays: store each box [x0,y0,x1,y1] as a map."""
    d = review.model_dump(mode="json")
    for f in d["findings"]:
        f["boxes"] = [_box_to_map(b) for b in f.get("boxes", [])]
        for c in f.get("citations", []):  # company citations carry boxes too
            c["boxes"] = [_box_to_map(b) for b in c.get("boxes", [])]
    return d


def _box_to_map(b: list[float]) -> dict:
    return dict(zip(("x0", "y0", "x1", "y1"), b))


def _box_from_map(b) -> list[float]:
    return [b["x0"], b["y0"], b["x1"], b["y1"]] if isinstance(b, dict) else b


def _from_firestore(d: dict) -> ReviewResult:
    d = dict(d)
    d["findings"] = [
        {**f, "boxes": [_box_from_map(b) for b in f.get("boxes", [])],
         "citations": [{**c, "boxes": [_box_from_map(b) for b in c.get("boxes", [])]}
                       for c in f.get("citations", [])]}
        for f in d.get("findings", [])
    ]
    return ReviewResult.model_validate(d)


class FirestoreReviewStore:
    """Collection `reviews`. Every read filters on the `owner` field. Owner-scoped lists sort in memory
    (one workspace is small), which avoids a composite index for equality + order."""

    COLLECTION = "reviews"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)

    def save(self, review: ReviewResult) -> None:
        self._col.document(review.id).set(_to_firestore(review))

    def get(self, review_id: str, *, owner: str) -> ReviewResult | None:
        snap = self._col.document(review_id).get()
        if not snap.exists:
            return None
        d = snap.to_dict()
        return _from_firestore(d) if d.get("owner") == owner else None

    def _owned(self, owner: str, *extra) -> list[ReviewResult]:
        q = self._col.where(filter=_eq("owner", owner))
        for e in extra:
            q = q.where(filter=e)
        items = [_from_firestore(s.to_dict()) for s in q.stream()]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items

    def list(self, *, owner: str, limit: int = 50) -> list[ReviewResult]:
        return self._owned(owner)[:limit]

    def find_by_sample(self, sample_id: str, *, owner: str) -> ReviewResult | None:
        # newest wins (so ?refresh=1 replaces the cache)
        found = self._owned(owner, _eq("sample_id", sample_id))
        return found[0] if found else None

    def runs_of(self, content_hash: str, *, owner: str) -> list[ReviewResult]:
        return self._owned(owner, _eq("content_hash", content_hash))

    def delete(self, review_id: str, *, owner: str) -> bool:
        if self.get(review_id, owner=owner) is None:
            return False
        self._col.document(review_id).delete()
        return True

    def delete_owner(self, owner: str) -> list[str]:
        refs = [s.reference for s in self._col.where(filter=_eq("owner", owner)).stream()]
        _delete_refs(self._db, refs)
        return [r.id for r in refs]

    def scan(self, *, limit: int, since: datetime | None = None, owner: str | None = None) -> Iterator[ReviewResult]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        if owner is not None:  # one workspace is small: filter and sort in memory, no composite index
            yield from [r for r in self._owned(owner) if since is None or r.created_at >= since][:limit]
            return
        q = self._col
        if since is not None:
            q = q.where(filter=FieldFilter("created_at", ">=", _iso(since)))
        q = q.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
        for s in q.stream():
            yield _from_firestore(s.to_dict())


class FirestoreIndex:
    """Collection `chunks`, vector field `embedding` (768 dims, COSINE) with an `owner` prefilter.

    Needs the composite vector index (owner ASC, embedding vector), created by infrastructure/gcp/app."""

    COLLECTION = "chunks"
    FIELD = "embedding"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)

    def upsert(self, chunks: list[Chunk]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for start in range(0, len(chunks), 400):  # Firestore batch limit is 500 writes
            batch = self._db.batch()
            for c in chunks[start:start + 400]:
                batch.set(self._col.document(c.id), {
                    "owner": c.owner, "review_id": c.review_id, "filename": c.filename, "page": c.page,
                    "text": c.text, "clause_type": c.clause_type, "finding_id": c.finding_id,
                    self.FIELD: Vector(c.embedding),
                })
            batch.commit()

    def query(self, vector: list[float], limit: int, *, owner: str) -> list[tuple[Chunk, float]]:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q = self._col.where(filter=_eq("owner", owner)).find_nearest(
            vector_field=self.FIELD, query_vector=Vector(vector),
            distance_measure=DistanceMeasure.COSINE, limit=limit, distance_result_field="vector_distance")
        out: list[tuple[Chunk, float]] = []
        for s in q.stream():
            d = s.to_dict()
            chunk = Chunk(id=s.id, review_id=d["review_id"], filename=d["filename"], page=d["page"],
                          text=d["text"], clause_type=d.get("clause_type"), finding_id=d.get("finding_id"),
                          owner=d.get("owner", owner))
            out.append((chunk, 1.0 - float(d.get("vector_distance", 1.0))))
        return out

    @staticmethod
    def _chunk(s, owner):
        d = s.to_dict()
        return Chunk(id=s.id, review_id=d["review_id"], filename=d["filename"], page=d["page"],
                     text=d["text"], clause_type=d.get("clause_type"), finding_id=d.get("finding_id"),
                     owner=d.get("owner", owner), embedding=list(d.get("embedding") or []))

    def keyword(self, query: str, limit: int, *, owner: str) -> list[tuple[Chunk, float]]:
        from app.keyword import rank
        # Scan real indexed chunks of this workspace, not only finding excerpts. No new index required.
        chunks = [self._chunk(s, owner) for s in self._col.where(filter=_eq("owner", owner)).stream()]
        return rank(chunks, query, limit)

    def chunks(self, review_id: str, *, owner: str) -> list[Chunk]:
        q = self._col.where(filter=_eq("owner", owner)).where(filter=_eq("review_id", review_id))
        return [self._chunk(s, owner) for s in q.stream()]

    def delete_review(self, review_id: str, *, owner: str) -> None:
        q = self._col.where(filter=_eq("owner", owner)).where(filter=_eq("review_id", review_id))
        _delete_refs(self._db, [s.reference for s in q.stream()])

    def delete_owner(self, owner: str) -> None:
        _delete_refs(self._db, [s.reference for s in self._col.where(filter=_eq("owner", owner)).stream()])


class FirestoreCompanyStore:
    """Collections `company_docs` (metadata) and `company_chunks` (text + 768-d `embedding`, COSINE),
    both carrying an `owner` field that every read filters on.

    Needs the composite vector index (owner ASC, embedding vector) on company_chunks (infrastructure/gcp/app).
    """

    DOCS = "company_docs"
    CHUNKS = "company_chunks"
    FIELD = "embedding"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._docs = self._db.collection(self.DOCS)
        self._chunks = self._db.collection(self.CHUNKS)

    def put(self, doc: CompanyDoc, chunks: list[CompanyChunk], *, owner: str) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for start in range(0, len(chunks), 400):
            batch = self._db.batch()
            for c in chunks[start:start + 400]:
                batch.set(self._chunks.document(c.id), {
                    "owner": owner, "doc_id": c.doc_id, "title": c.title, "page": c.page, "text": c.text,
                    self.FIELD: Vector(c.embedding),
                })
            batch.commit()
        # last: a doc is listed only when searchable
        self._docs.document(doc.id).set({**doc.model_dump(mode="json"), "owner": owner})

    @staticmethod
    def _doc(d: dict) -> CompanyDoc:
        return CompanyDoc.model_validate({k: v for k, v in d.items() if k != "owner"})

    def list(self, *, owner: str) -> list[CompanyDoc]:
        docs = [self._doc(s.to_dict()) for s in self._docs.where(filter=_eq("owner", owner)).stream()]
        return sorted(docs, key=lambda d: d.created_at, reverse=True)

    def get(self, doc_id: str, *, owner: str) -> CompanyDoc | None:
        snap = self._docs.document(doc_id).get()
        if not snap.exists or snap.to_dict().get("owner") != owner:
            return None
        return self._doc(snap.to_dict())

    def delete(self, doc_id: str, *, owner: str) -> bool:
        if self.get(doc_id, owner=owner) is None:
            return False
        q = self._chunks.where(filter=_eq("owner", owner)).where(filter=_eq("doc_id", doc_id))
        _delete_refs(self._db, [s.reference for s in q.stream()])
        self._docs.document(doc_id).delete()
        return True

    CHOSEN_OVERFETCH = 200  # passages fetched when the reader chose documents, then filtered to those documents

    def search(self, vector: list[float], limit: int, *, owner: str,
               doc_ids: frozenset[str] | None = None) -> list[tuple[CompanyChunk, float]]:
        """With `doc_ids` the nearest passages of the workspace are over-fetched and filtered to those documents:
        a vector prefilter on doc_id would need another composite index. A chosen document whose passages are all
        far from the query can therefore be missed when the workspace holds many other policies."""
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q = self._chunks.where(filter=_eq("owner", owner)).find_nearest(
            vector_field=self.FIELD, query_vector=Vector(vector),
            distance_measure=DistanceMeasure.COSINE, limit=max(limit, self.CHOSEN_OVERFETCH) if doc_ids else limit,
            distance_result_field="vector_distance")
        out: list[tuple[CompanyChunk, float]] = []
        for s in q.stream():
            d = s.to_dict()
            if doc_ids is not None and d["doc_id"] not in doc_ids:
                continue
            if len(out) >= limit:
                break
            out.append((CompanyChunk(id=s.id, doc_id=d["doc_id"], title=d["title"], page=d["page"],
                                     text=d["text"]), 1.0 - float(d.get("vector_distance", 1.0))))
        return out

    def delete_owner(self, owner: str) -> list[str]:
        doc_refs = [s.reference for s in self._docs.where(filter=_eq("owner", owner)).stream()]
        _delete_refs(self._db, [s.reference for s in self._chunks.where(filter=_eq("owner", owner)).stream()])
        _delete_refs(self._db, doc_refs)
        return [r.id for r in doc_refs]


# ---- users and the audit trail ----------------------------------------------------------------


class FirestoreUserStore:
    COLLECTION = "users"

    def __init__(self, settings: Settings):
        self._col = _firestore_client(settings).collection(self.COLLECTION)

    def get(self, uid: str) -> User | None:
        snap = self._col.document(uid).get()
        return User.model_validate(snap.to_dict()) if snap.exists else None

    def put(self, user: User) -> None:
        self._col.document(user.uid).set(user.model_dump(mode="json", exclude={"workspaces", "platform_admin"}))

    def list(self) -> list[User]:
        return [User.model_validate(s.to_dict()) for s in self._col.stream()]

    def delete(self, uid: str) -> None:
        self._col.document(uid).delete()


class FirestoreWorkspaceStore:
    """Collections `workspaces`, `workspace_members` (document id `<workspace_id>_<uid>`, with the
    `workspace_id` and `uid` also stored as fields) and `invites` (document id = the code)."""

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._ws = self._db.collection("workspaces")
        self._mem = self._db.collection("workspace_members")
        self._inv = self._db.collection("invites")

    @staticmethod
    def _member_id(workspace_id: str, uid: str) -> str:
        return f"{workspace_id}_{uid}"

    def get(self, workspace_id: str) -> Workspace | None:
        snap = self._ws.document(workspace_id).get()
        return Workspace.model_validate(snap.to_dict()) if snap.exists else None

    def create(self, ws: Workspace) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            self._ws.document(ws.id).create(ws.model_dump(mode="json", exclude={"member_count"}))
            return True
        except AlreadyExists:
            return False

    def put(self, ws: Workspace) -> None:
        self._ws.document(ws.id).set(ws.model_dump(mode="json", exclude={"member_count"}))

    def delete(self, workspace_id: str) -> None:
        _delete_refs(self._db, [s.reference for s in self._mem.where(filter=_eq("workspace_id", workspace_id)).stream()])
        _delete_refs(self._db, [s.reference for s in self._inv.where(filter=_eq("workspace_id", workspace_id)).stream()])
        self._ws.document(workspace_id).delete()

    def find_by_domain(self, domain: str) -> Workspace | None:
        for s in self._ws.where(filter=_eq("domain", domain.lower())).limit(1).stream():
            return Workspace.model_validate(s.to_dict())
        return None

    def list_all(self) -> list[Workspace]:
        return [Workspace.model_validate(s.to_dict()) for s in self._ws.stream()]

    @staticmethod
    def _member(d: dict) -> Member:
        return Member.model_validate({k: v for k, v in d.items() if k != "workspace_id"})

    def get_member(self, workspace_id: str, uid: str) -> Member | None:
        snap = self._mem.document(self._member_id(workspace_id, uid)).get()
        return self._member(snap.to_dict()) if snap.exists else None

    def put_member(self, workspace_id: str, member: Member) -> None:
        self._mem.document(self._member_id(workspace_id, member.uid)).set(
            {**member.model_dump(mode="json"), "workspace_id": workspace_id})

    def delete_member(self, workspace_id: str, uid: str) -> None:
        self._mem.document(self._member_id(workspace_id, uid)).delete()

    def members(self, workspace_id: str) -> list[Member]:
        ms = [self._member(s.to_dict()) for s in self._mem.where(filter=_eq("workspace_id", workspace_id)).stream()]
        return sorted(ms, key=lambda m: str(m.joined_at or ""))

    def memberships(self, uid: str) -> list[tuple[Workspace, Member]]:
        out = []
        for s in self._mem.where(filter=_eq("uid", uid)).stream():
            d = s.to_dict()
            ws = self.get(d["workspace_id"])
            if ws is not None:
                out.append((ws, self._member(d)))
        return sorted(out, key=lambda t: str(t[1].joined_at or ""))

    def put_invite(self, invite: Invite) -> None:
        self._inv.document(invite.code).set(invite.model_dump(mode="json"))

    def get_invite(self, code: str) -> Invite | None:
        snap = self._inv.document(code).get()
        return Invite.model_validate(snap.to_dict()) if snap.exists else None

    def delete_invite(self, code: str) -> None:
        self._inv.document(code).delete()


class FirestoreSpendCounter:
    """Collection `spend_counters`, one document per key (`reviews-YYYY-MM-DD`, UTC). The check and the increment
    happen in one Firestore transaction, so concurrent instances can never overshoot the cap. No index needed."""

    COLLECTION = "spend_counters"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)

    def take(self, key: str, cap: int) -> bool:
        from google.cloud import firestore

        from app.spend import next_count

        ref = self._col.document(key)

        @firestore.transactional
        def run(transaction) -> bool:
            snap = ref.get(transaction=transaction)
            current = int((snap.to_dict() or {}).get("count", 0)) if snap.exists else 0
            new = next_count(current, cap)
            if new is None:
                return False
            transaction.set(ref, {"count": new, "updated_at": datetime.now(timezone.utc).isoformat()})
            return True

        return run(self._db.transaction())

    def used(self, key: str) -> int:
        snap = self._col.document(key).get()
        return int((snap.to_dict() or {}).get("count", 0)) if snap.exists else 0


class FirestoreAuditLog:
    """Collection `audit_events`, one document per event, scoped by `workspace_id`. Append-only: nothing
    here updates a document; the only deletes are a user's own 'delete my data' (`delete_actor`) and the
    removal of a workspace whose last member deleted their data (`delete_workspace`).

    Needs composite indexes (workspace_id ASC, at DESC) and (workspace_id ASC, actor ASC, at DESC),
    created by infrastructure/gcp/app."""

    COLLECTION = "audit_events"

    def __init__(self, settings: Settings):
        self._db = _firestore_client(settings)
        self._col = self._db.collection(self.COLLECTION)

    def append(self, event: AuditEvent) -> None:
        # create() fails if the id exists, so an event can never be overwritten
        self._col.document(event.id).create(event.model_dump(mode="json"))

    def list_for(self, workspace_id: str, limit: int, *, actor: str | None = None) -> list[AuditEvent]:
        from google.cloud import firestore

        q = self._col.where(filter=_eq("workspace_id", workspace_id))
        if actor is not None:
            q = q.where(filter=_eq("actor", actor))
        q = q.order_by("at", direction=firestore.Query.DESCENDING).limit(limit)
        return [AuditEvent.model_validate(s.to_dict()) for s in q.stream()]

    def list_all(self, limit: int) -> list[AuditEvent]:
        from google.cloud import firestore

        q = self._col.order_by("at", direction=firestore.Query.DESCENDING).limit(limit)
        return [AuditEvent.model_validate(s.to_dict()) for s in q.stream()]

    def delete_actor(self, actor: str) -> int:
        return _delete_refs(self._db, [s.reference for s in self._col.where(filter=_eq("actor", actor)).stream()])

    def delete_workspace(self, workspace_id: str) -> int:
        return _delete_refs(self._db, [s.reference for s in
                                       self._col.where(filter=_eq("workspace_id", workspace_id)).stream()])
