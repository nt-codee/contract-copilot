"""Optimize verified screenshots and frame sequences; never invent interface content.

python3 docs/public/make_assets.py --manifest CAPTURE.json [--out-dir DIR]
python3 docs/public/make_assets.py --chart-only --eval eval/results/20261018T124253Z.json

Requires existing Pillow; the optional chart also uses existing matplotlib. No browser, network or
model calls. A manifest supplies capture provenance, explicit screen jobs and ordered GIF frames.
Missing frames fail the run. Sources are never silently substituted with an older or offline image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Image names must be relative to the manifest/output directory")
    return path


def image(source: Path, width: int, crop: list[int] | None = None) -> Image.Image:
    with Image.open(source) as original:
        im = original.convert("RGB")
    if crop:
        x0, y0, x1, y1 = crop
        if not (0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height):
            raise ValueError(f"Crop outside source image: {source.name}")
        im = im.crop(tuple(crop))
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
    return im


def provenance(capture: dict) -> dict:
    if capture.get("data_scope") != "synthetic-public":
        raise ValueError("Only verified synthetic-public captures may become public assets")
    if capture.get("mode") not in {"live", "local", "mock", "illustrative"}:
        raise ValueError("Capture must explicitly identify live/local/mock/illustrative mode")
    for key in ("commit", "revision", "captured_at", "recipe"):
        if not capture.get(key):
            raise ValueError(f"Capture is missing {key}")
    # Keep the public origin and capture recipe; never publish input machine paths or raw browser logs.
    url = urlsplit(capture.get("base_url", ""))
    origin = f"{url.scheme}://{url.hostname}" + (f":{url.port}" if url.port else "") if url.hostname else None
    return {**{key: capture[key] for key in ("commit", "revision", "captured_at", "mode", "data_scope", "recipe")},
            "base_url": origin, "viewport": capture.get("viewport"),
            "timing": capture.get("timing", "Frames held for readability; not a review-duration benchmark.")}


def assets(manifest_path: Path, out: Path) -> None:
    data = json.loads(manifest_path.read_text())
    if data.get("errors"):
        raise ValueError("Capture reports errors; verify them before generating public assets")
    record = {"capture": provenance(data["capture"]), "assets": []}
    out.mkdir(parents=True, exist_ok=True)
    for job in data.get("screens", []):
        source = inside(manifest_path.parent, job["input"])
        target = inside(out, job["output"])
        if target.suffix.lower() != ".png":
            raise ValueError("Still outputs must be PNG")
        im = image(source, int(job.get("max_width", 1280)), job.get("crop"))
        target.parent.mkdir(parents=True, exist_ok=True)
        colors = job.get("colors")
        if colors:
            im = im.quantize(colors=int(colors), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        im.save(target, optimize=True, compress_level=9)
        record["assets"].append({"file": job["output"], "width": im.width, "height": im.height,
                                 "bytes": target.stat().st_size, "sha256": digest(target),
                                 "source_frame": source.name, "source_sha256": digest(source),
                                 "crop": job.get("crop"), "palette_colors": colors,
                                 "caption": job.get("caption", "Actual captured interface.")})
    for job in data.get("animations", []):
        target = inside(out, job["output"])
        if target.suffix.lower() != ".gif" or not job.get("frames"):
            raise ValueError("Animations require GIF output and an explicit frame sequence")
        sources = [inside(manifest_path.parent, f["input"]) for f in job["frames"]]
        ims = [image(src, int(job.get("max_width", 1080)), f.get("crop")) for src, f in zip(sources, job["frames"])]
        if len({im.size for im in ims}) != 1:
            raise ValueError("Animation frames must share dimensions after explicit crops/resizing")
        durations = [int(f["duration_ms"]) for f in job["frames"]]
        if any(ms < 20 or ms > 60_000 for ms in durations):
            raise ValueError("Frame hold must be 20–60000 ms")
        # One shared palette reduces size/flicker; every pixel still comes from the supplied captures.
        thumbs = []
        for im in ims:
            thumb = im.copy()
            thumb.thumbnail((240, 160))
            thumbs.append(thumb)
        contact = Image.new("RGB", (240 * min(4, len(thumbs)), 160 * math.ceil(len(thumbs) / 4)), "white")
        for i, thumb in enumerate(thumbs):
            contact.paste(thumb, ((i % 4) * 240, (i // 4) * 160))
        colors = int(job.get("colors", 128))
        palette = contact.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in ims]
        target.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(target, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True, disposal=2)
        with Image.open(target) as saved:
            count = saved.n_frames
            actual_ms = 0
            for i in range(count):
                saved.seek(i)
                actual_ms += saved.info.get("duration", 0)
        record["assets"].append({"file": job["output"], "width": ims[0].width, "height": ims[0].height,
                                 "frames": count, "duration_ms": actual_ms, "bytes": target.stat().st_size,
                                 "sha256": digest(target), "palette_colors": colors,
                                 "source_frames": [{"file": src.name, "sha256": digest(src), "hold_ms": ms} for src, ms in zip(sources, durations)],
                                 "caption": job.get("caption", "Actual captured interactions, held for readability; not a speed benchmark.")})
    if not record["assets"]:
        raise ValueError("Manifest contains no output jobs")
    (out / "media-evidence.json").write_text(json.dumps(record, indent=2) + "\n")
    for item in record["assets"]:
        print(f'{item["file"]}: {item["width"]}x{item["height"]}, {item["bytes"] / 1024:.1f} KiB')


def chart(eval_path: Path, out: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cc-media-matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = json.loads(eval_path.read_text())
    rows = data["scores"]
    secs = [r["latency_ms"] / 1000 for r in rows]
    import statistics
    median = statistics.median(secs)
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=100)
    colors = [{"high": "#B3261E", "medium": "#8A5300", "low": "#1B6B4A"}[r["overall_risk"]] for r in rows]
    ax.bar(range(len(rows)), secs, color=colors)
    ax.axhline(median, color="#121A2B", linestyle="--", linewidth=1)
    ax.set_xticks(range(len(rows)), [r["id"] for r in rows], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("seconds per review")
    ax.set_title(f"Stored synthetic run {eval_path.stem}: median {median:.1f} s", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "eval-latency.svg", metadata={"Date": None, "Creator": "make_assets.py"})
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs/public")
    parser.add_argument("--chart-only", action="store_true")
    parser.add_argument("--eval", type=Path)
    args = parser.parse_args()
    if args.chart_only:
        if not args.eval:
            parser.error("--chart-only requires an explicit dated --eval artifact")
        chart(args.eval, args.out_dir)
    elif args.manifest:
        assets(args.manifest.resolve(), args.out_dir.resolve())
    else:
        parser.error("Supply --manifest or --chart-only --eval; no implicit source folders")
