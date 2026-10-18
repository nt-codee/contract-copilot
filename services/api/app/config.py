"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Owner id of the public demo workspace (visitors who are not signed in).
DEMO_OWNER = "demo"

DISCLAIMER = "This is an automated review to help you prepare. It is not legal advice."

# Embedding dimension is fixed: the Firestore vector index (infra stack) is created at 768,
# and Firestore caps vector indexes at 2048 dimensions.
EMBED_DIM = 768

# USD per 1M tokens: (input, output).
# Source: https://cloud.google.com/vertex-ai/generative-ai/pricing, fetched 2026-10-18 09:15 +08.
# - gemini-3.8-flash: introductory $0.75 in / $3.75 out per 1M tokens through 2026-12-31;
#   standard $1.50 / $7.50 from 2027-01-01. Thinking tokens bill as output.
# - gemini-embedding-2 ("Gemini Embedding 2, Unified Multimodal, Preview"): text input
#   $0.20 per 1M tokens online; no charge for output.
# - text-multilingual-embedding-002 (fallback only): "Embeddings for Text" $0.000025 per 1,000
#   characters, approximated here at 4 chars/token = $0.10 per 1M tokens.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    # fallback model (LLM_FALLBACK_MODEL). $1.50 in / $9.00 out per 1M tokens: the official pricing page did
    # not render in our fetch; four consistent third-party price pages (metatext.io, markaicode.com,
    # thepricer.org, evolink.ai), read 2026-10-18 18:45 +08. Re-check against the official page.
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-embedding-2": (0.20, 0.0),
    "text-multilingual-embedding-002": (0.10, 0.0),
}


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = PRICES_PER_1M.get(model)
    if price is None:
        return None
    return ((input_tokens or 0) * price[0] + (output_tokens or 0) * price[1]) / 1_000_000


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    cloud: str = field(default_factory=lambda: _env("CLOUD", "local"))
    llm: str = field(default_factory=lambda: _env("LLM", "fake"))
    gcp_project: str = field(default_factory=lambda: _env("GCP_PROJECT", "contract-copilot-abc26"))
    gemini_location: str = field(default_factory=lambda: _env("GEMINI_LOCATION", "global"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.8-flash"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "gemini-embedding-2"))
    # Gemini 3 thinking level: "low" | "medium" | "high" | "" (model default). Thinking bills as output.
    gemini_thinking: str = field(default_factory=lambda: _env("GEMINI_THINKING", "low"))
    # answers the retry of a stalled agent or review call ("" turns the fallback off)
    llm_fallback_model: str = field(default_factory=lambda: os.environ.get("LLM_FALLBACK_MODEL", "gemini-3.5-flash").strip())
    embed_location: str = field(default_factory=lambda: _env("EMBED_LOCATION", "global"))
    upload_bucket: str = field(default_factory=lambda: _env("UPLOAD_BUCKET", ""))
    firestore_db: str = field(default_factory=lambda: _env("FIRESTORE_DB", "(default)"))
    # In the image REPO_ROOT is /app, so these resolve to /app/knowledge, /app/samples/... there too.
    samples_dir: Path = field(
        default_factory=lambda: Path(_env("SAMPLES_DIR", str(REPO_ROOT / "samples" / "contracts")))
    )
    company_samples_dir: Path = field(
        default_factory=lambda: Path(_env("COMPANY_SAMPLES_DIR", str(REPO_ROOT / "samples" / "company")))
    )
    # knowledge/law/{my,sg}/*.md and knowledge/playbook/{nda,service_agreement}/*.md
    knowledge_dir: Path = field(
        default_factory=lambda: Path(_env("KNOWLEDGE_DIR", str(REPO_ROOT / "knowledge")))
    )
    # bucket holding the ingested law/playbook markdown (kb/<version>/<id>.md); Pulumi names it <project>-knowledge
    knowledge_bucket: str = field(default_factory=lambda: _env(
        "KNOWLEDGE_BUCKET", f"{_env('GCP_PROJECT', 'contract-copilot-abc26')}-knowledge"))
    # Law of this jurisdiction is the fallback for a contract governed by a foreign law (my | sg).
    home_jurisdiction: str = field(
        default_factory=lambda: _env("HOME_JURISDICTION", "my").lower() if _env("HOME_JURISDICTION", "my").lower() in ("my", "sg") else "my")
    web_dist: Path = field(default_factory=lambda: Path(_env("WEB_DIST", "/app/web/dist")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/tmp/cc-data")))
    dev_cors: bool = field(default_factory=lambda: _env("DEV_CORS", "") == "1")
    rate_limit_per_hour: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_HOUR", "10")))  # per IP address
    rate_limit_user_per_hour: int = field(default_factory=lambda: int(_env("RATE_LIMIT_USER_PER_HOUR", "30")))  # per signed-in user
    # Global cap on fresh model reviews per UTC day, across instances (uploads and sample refreshes; admins exempt).
    max_model_reviews_per_day: int = field(default_factory=lambda: int(_env("MAX_MODEL_REVIEWS_PER_DAY", "300")))
    # Content-Security-Policy: report-only (default), enforce, or off. Report-only never blocks anything.
    csp_mode: str = field(default_factory=lambda: _env("CSP_MODE", "report-only"))

    # Auth. ADMIN_EMAILS is comma-separated and comes from a secret Pulumi config, never a committed file.
    admin_emails: frozenset[str] = field(default_factory=lambda: frozenset(
        e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()))
    # Identity Platform client config (JSON: apiKey, authDomain, projectId), served at GET /api/auth/config so the
    # web reads it at runtime.
    identity_web_config: str = field(default_factory=lambda: _env("IDENTITY_WEB_CONFIG", ""))
    app_version: str = field(default_factory=lambda: _env("APP_VERSION", "dev"))

    max_bytes: int = 10 * 1024 * 1024
    max_pages: int = 40
