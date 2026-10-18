#!/usr/bin/env python3
"""Review one PDF and print the ReviewResult JSON.

Run from services/api so uv picks up the project environment:
    cd services/api && CLOUD=local LLM=vertex uv run python ../../scripts/review.py <file.pdf>
Progress goes to stderr; stdout is only the JSON.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "services" / "api"
sys.path.insert(0, str(API_DIR))

from app.adapters import build_services  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipeline import UserError, review  # noqa: E402
from app.ports import UpstreamError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--sample-id", default=None)
    ap.add_argument("--no-index", action="store_true",
                    help="index with the local hash embedder (no embedding API calls)")
    args = ap.parse_args()

    if not args.pdf.is_file():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s")
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = Settings()
    svc = build_services(settings)
    if args.no_index:
        from app.adapters.local import HashEmbedder, MemoryIndex

        svc.embedder, svc.index = HashEmbedder(), MemoryIndex()
    print(f"[review] {args.pdf.name}: CLOUD={settings.cloud} LLM={settings.llm} model={svc.llm.model}",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        result = review(svc, args.pdf.read_bytes(), args.pdf.name, sample_id=args.sample_id)
    except (UserError, UpstreamError) as e:
        print(f"error: {getattr(e, 'message', str(e))}", file=sys.stderr)
        return 1
    m = result.metrics
    print(f"[review] done in {time.monotonic() - t0:.1f}s: {len(result.findings)} findings, "
          f"{m.dropped_unverified} dropped, "
          f"{sum(len(f.citations) for f in result.findings)} citations "
          f"({m.dropped_unverified_citations} dropped), jurisdiction={result.jurisdiction}, "
          f"{len(result.trace)} trace steps, {m.input_tokens} in / {m.output_tokens} out tokens, "
          f"~${m.cost_usd_est}; calls: llm={getattr(svc.llm, 'calls', '?')} "
          f"embed={getattr(svc.embedder, 'calls', '?')}", file=sys.stderr, flush=True)
    for t in result.trace:
        print(f"[trace {t.step:>2}] {t.summary}" + (f" ({t.ms} ms)" if t.ms is not None else ""),
              file=sys.stderr, flush=True)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
