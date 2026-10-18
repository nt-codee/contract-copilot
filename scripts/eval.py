#!/usr/bin/env python3
"""Run every sample in samples/contracts/index.json and score it against samples/contracts/expected/<id>.json.

    cd services/api && CLOUD=local LLM=vertex uv run python ../../scripts/eval.py [--only id,id]

Writes eval/results/<timestamp>.json and <timestamp>.md, and prints the Markdown table.
Each sample costs one review call (plus one transcription call for scanned PDFs) and embeddings.

Scoring:
- must_flag recall: an expected item counts as found when a finding has the same clause_type, a
  risk at least as severe as expected, and (when quote_contains is given) its quote or a neighbouring
  page text contains quote_contains. Reported overall, by clause_type, and by risk.
- false highs: a high finding whose clause_type is listed in must_not_flag_high.
- latency p50 / max, mean cost per review (from metrics).

By default search indexing uses the local hash embedder, so a run makes only 1 Gemini call per sample
(2 for scanned PDFs) and the cost column excludes embeddings (about $0.001 per review at
gemini-embedding-2 prices). Pass --with-index to embed with the configured model as production does.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "api"))

from app.adapters import build_services  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import RISK_ORDER, ReviewResult  # noqa: E402
from app.pipeline import normalise, review  # noqa: E402


def _matches(expected: dict, result: ReviewResult) -> str | None:
    want = expected.get("quote_contains")
    for f in result.findings:
        if f.clause_type != expected["clause_type"]:
            continue
        if RISK_ORDER[f.risk] > RISK_ORDER[expected["risk"]]:
            continue
        if want and normalise(want) not in normalise(f.quote):
            # accept the right clause even if the model quoted a different sentence of it,
            # as long as it cites the expected page (±1)
            if abs(f.page - int(expected.get("page", f.page))) > 1:
                continue
        return f.id
    return None


def score(expected: dict, result: ReviewResult) -> dict:
    flags = []
    for e in expected.get("must_flag", []):
        flags.append({**e, "found": _matches(e, result)})
    forbidden = set(expected.get("must_not_flag_high", []))
    false_high = [f.id for f in result.findings if f.risk == "high" and f.clause_type in forbidden]
    return {
        "id": expected["id"], "must_flag": flags, "false_high": false_high,
        "expected_overall_risk": expected.get("expected_overall_risk"),
        "overall_risk": result.verdict.overall_risk,
        "contract_type_ok": result.contract_type == expected.get("contract_type"),
        "language": result.language, "latency_ms": result.metrics.latency_ms,
        "cost_usd_est": result.metrics.cost_usd_est, "findings": len(result.findings),
        "dropped_unverified": result.metrics.dropped_unverified,
    }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def table(scores: list[dict], errors: dict[str, str], model: str) -> str:
    by_ct: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_risk: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in scores:
        for f in s["must_flag"]:
            for bucket, key in ((by_ct, f["clause_type"]), (by_risk, f["risk"])):
                bucket[key][1] += 1
                bucket[key][0] += bool(f["found"])
    total = [sum(v[0] for v in by_risk.values()), sum(v[1] for v in by_risk.values())]
    lat = [s["latency_ms"] for s in scores]
    costs = [s["cost_usd_est"] for s in scores if s["cost_usd_est"] is not None]

    out = [f"# Eval {datetime.now(timezone.utc).isoformat(timespec='seconds')} - model `{model}`", "",
           "| sample | type ok | overall (expected) | must_flag found | false high | findings | dropped | latency s | cost USD |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s in scores:
        found = sum(bool(f["found"]) for f in s["must_flag"])
        out.append(f"| {s['id']} | {'yes' if s['contract_type_ok'] else 'NO'} | {s['overall_risk']} "
                   f"({s['expected_overall_risk']}) | {_pct(found, len(s['must_flag']))} | {len(s['false_high'])} | "
                   f"{s['findings']} | {s['dropped_unverified']} | {s['latency_ms'] / 1000:.1f} | "
                   f"{s['cost_usd_est'] if s['cost_usd_est'] is not None else 'n/a'} |")
    for sid, msg in errors.items():
        out.append(f"| {sid} | ERROR: {msg} | | | | | | | |")
    out += ["", "| recall by clause_type | found |", "|---|---|"]
    out += [f"| {k} | {_pct(*v)} |" for k, v in sorted(by_ct.items())]
    out += ["", "| recall by risk | found |", "|---|---|"]
    out += [f"| {k} | {_pct(*by_risk[k])} |" for k in ("high", "medium", "low") if k in by_risk]
    out += ["", "| summary | value |", "|---|---|",
            f"| must_flag recall | {_pct(*total)} |",
            f"| false highs (must_not_flag_high) | {sum(len(s['false_high']) for s in scores)} |",
            f"| latency p50 / max | {statistics.median(lat) / 1000:.1f} s / {max(lat) / 1000:.1f} s |" if lat else "| latency | n/a |",
            f"| mean cost per review (LLM; embeddings excluded unless --with-index) | ${statistics.mean(costs):.5f} |" if costs else "| mean cost | n/a |",
            f"| samples ok / errored | {len(scores)} / {len(errors)} |"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=Path, default=REPO / "samples" / "contracts")
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "results")
    ap.add_argument("--only", default="", help="comma-separated sample ids")
    ap.add_argument("--with-index", action="store_true", help="embed + index with the real embedder")
    args = ap.parse_args()

    index_path = args.samples / "index.json"
    if not index_path.is_file():
        print(f"error: {index_path} not found", file=sys.stderr)
        return 2
    samples = json.loads(index_path.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        samples = [s for s in samples if s["id"] in wanted]

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s")
    for noisy in ("httpx", "google_genai", "urllib3", "tenacity"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # per-call timings come from cc.gcp
    settings = Settings()
    svc = build_services(settings)
    if not args.with_index:
        from app.adapters.local import HashEmbedder, MemoryIndex

        svc.embedder, svc.index = HashEmbedder(), MemoryIndex()
    scores, errors, raw = [], {}, {}
    for i, s in enumerate(samples, start=1):
        sid = s["id"]
        pdf = args.samples / (s.get("file") or f"{sid}.pdf")
        exp_path = args.samples / "expected" / f"{sid}.json"
        print(f"[eval {i}/{len(samples)}] {sid} ...", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        try:
            expected = json.loads(exp_path.read_text(encoding="utf-8"))
            result = review(svc, pdf.read_bytes(), pdf.name, sample_id=sid)  # one attempt, no retry loop
        except Exception as e:  # keep going; report the failure in the table
            errors[sid] = getattr(e, "message", None) or f"{type(e).__name__}: {str(e)[:120]}"
            print(f"[eval {i}/{len(samples)}] {sid} FAILED: {errors[sid]}", file=sys.stderr, flush=True)
            continue
        sc = score(expected, result)
        scores.append(sc)
        raw[sid] = result.model_dump(mode="json")
        found = sum(bool(f["found"]) for f in sc["must_flag"])
        print(f"[eval {i}/{len(samples)}] {sid} {time.monotonic() - t0:.1f}s "
              f"must_flag {found}/{len(sc['must_flag'])} false_high {len(sc['false_high'])}",
              file=sys.stderr, flush=True)

    md = table(scores, errors, settings.gemini_model if settings.llm == "vertex" else svc.llm.model)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{stamp}.json").write_text(json.dumps(
        {"model": svc.llm.model, "scores": scores, "errors": errors, "results": raw}, indent=2), encoding="utf-8")
    (args.out / f"{stamp}.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    print(f"\nwrote {args.out / (stamp + '.json')}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
