"""Optimize verified screenshots and frame sequences; never invent interface content.

python3 docs/public/make_assets.py --manifest CAPTURE.json [--out-dir DIR]
python3 docs/public/make_assets.py --chart-only --eval eval/results/20261018T124253Z.json
python3 docs/public/make_assets.py --banner CAPTURE_DIR/finding.png --banner-crop 68 0 1440 840

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
        # Half-size frames keep small risk-coloured boxes in the palette; tiny thumbnails lost them.
        tw, th = max(1, ims[0].width // 2), max(1, ims[0].height // 2)
        thumbs = [im.resize((tw, th), Image.Resampling.BOX) for im in ims]
        contact = Image.new("RGB", (tw * min(4, len(thumbs)), th * math.ceil(len(thumbs) / 4)), "white")
        for i, thumb in enumerate(thumbs):
            contact.paste(thumb, ((i % 4) * tw, (i // 4) * th))
        colors = int(job.get("colors", 128))
        palette = contact.quantize(colors=colors, method=Image.Quantize.MAXCOVERAGE)
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
    import statistics
    from matplotlib.patches import Patch
    data = json.loads(eval_path.read_text())
    rows = sorted(data["scores"], key=lambda r: r["latency_ms"])  # slowest at the top
    secs = [r["latency_ms"] / 1000 for r in rows]
    median = statistics.median(secs)
    # The app's tokens (web/tailwind.config): desk blue, paper, ink, and the risk box colours.
    desk, paper, line, ink, mute = "#1E3A6E", "#FFFFFF", "#DCE2EC", "#121A2B", "#5B6680"
    risk = {"high": "#D93A2B", "medium": "#E9A400", "low": "#2F9E6E"}
    # Text stays text in the SVG, so a viewer with Public Sans uses it; others fall back to a clean sans.
    plt.rcParams.update({"svg.fonttype": "none", "font.size": 10,
                         "font.family": ["Public Sans", "Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]})
    fig, ax = plt.subplots(figsize=(10, 7.6), dpi=100)
    fig.patch.set_facecolor(paper)
    ax.set_facecolor(paper)
    y = range(len(rows))
    ax.barh(y, secs, height=0.68, color=[risk[r["overall_risk"]] for r in rows], zorder=2)
    for i, (r, s) in enumerate(zip(rows, secs)):
        ax.text(s + 1.5, i, f"{s:.0f} s", va="center", fontsize=8.5, color=mute, zorder=4,
                bbox={"facecolor": paper, "edgecolor": "none", "pad": 1.2})
    ax.axvline(median, color=desk, linewidth=1.4, linestyle=(0, (4, 3)), zorder=3)
    ax.text(median + 1.5, len(rows) - 0.1, f"median {median:.1f} s", color=desk, fontsize=10, fontweight="bold", va="bottom")
    ax.set_yticks(list(y), [r["id"] for r in rows], fontsize=9, color=ink)
    ax.tick_params(axis="y", length=0, pad=6)
    ax.tick_params(axis="x", colors=mute, labelsize=9, length=0)
    ax.set_xlim(0, max(secs) * 1.1)
    ax.set_ylim(-0.7, len(rows) + 0.6)
    ax.xaxis.grid(True, color=line, linewidth=0.8, zorder=0)
    ax.set_xlabel("seconds per review", color=mute, fontsize=9.5)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    fig.text(0.015, 0.975, f"Time per review, {len(rows)} synthetic contracts", fontsize=15, fontweight="bold", color=ink, va="top")
    fig.text(0.015, 0.935, f"Stored run {eval_path.stem}, {data.get('model', 'model not recorded')}. Bar colour is the overall risk the review gave.",
             fontsize=9.5, color=mute, va="top")
    ax.legend(handles=[Patch(color=risk[k], label=k.capitalize()) for k in ("high", "medium", "low")],
              loc="lower right", frameon=False, fontsize=9, labelcolor=ink, handlelength=1, handleheight=1)
    fig.subplots_adjust(left=0.2, right=0.97, top=0.89, bottom=0.07)
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "eval-latency.svg", metadata={"Date": None, "Creator": "make_assets.py"}, facecolor=paper)
    plt.close(fig)
    print(f"eval-latency.svg: {len(rows)} reviews, median {median:.1f} s, slowest {max(secs):.1f} s")


def font(size: int, bold: bool = False):
    """A clean sans for the banner: Helvetica Neue on macOS, else matplotlib's bundled DejaVu Sans."""
    from PIL import ImageFont
    try:
        return ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", size, index=1 if bold else 0)
    except OSError:
        import matplotlib
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts/ttf" / name), size)


def banner(source: Path, crop: list[int], out: Path) -> None:
    """Frame one verified live capture beside the product name. Every interface pixel is the capture's."""
    from PIL import ImageDraw, ImageFilter
    W, H, deep, desk, paper, high = 1600, 600, "#152B54", "#1E3A6E", "#F5F7FA", "#D93A2B"
    canvas = Image.new("RGB", (W, H), deep)
    draw = ImageDraw.Draw(canvas)
    shot = image(source, 10_000, crop)
    shot.thumbnail((860, 520), Image.Resampling.LANCZOS)
    x, y = W - shot.width - 56, (H - shot.height) // 2
    shadow = Image.new("RGBA", (shot.width + 80, shot.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((40, 52, shot.width + 40, shot.height + 52), 14, fill=(5, 12, 30, 150))
    canvas.paste(shadow.filter(ImageFilter.GaussianBlur(18)), (x - 40, y - 40), shadow.filter(ImageFilter.GaussianBlur(18)))
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 12, fill=255)
    canvas.paste(shot, (x, y), mask)
    draw.rectangle((64, 96, 70, 178), fill=high)
    draw.text((92, 92), "Contract Co-pilot", font=font(40, True), fill="white")
    draw.text((92, 146), "Malaysia  ·  Singapore", font=font(22), fill="#C4D2EE")
    head = font(56, True)
    for i, text in enumerate(("Know what you're", "signing, before", "you sign it.")):
        draw.text((64, 236 + i * 66), text, font=head, fill=paper)
    small = font(22)
    for i, text in enumerate(("Risky clauses boxed on the contract's own pages,", "with the law that applies and what to ask for.")):
        draw.text((64, 462 + i * 32), text, font=small, fill="#C4D2EE")
    draw.rectangle((0, H - 6, W, H), fill=desk)
    out.mkdir(parents=True, exist_ok=True)
    canvas.save(out / "banner.png", optimize=True, compress_level=9)
    print(f"banner.png: {W}x{H} from {source.name} sha256 {digest(source)[:12]}, crop {crop}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs/public")
    parser.add_argument("--chart-only", action="store_true")
    parser.add_argument("--eval", type=Path)
    parser.add_argument("--banner", type=Path, help="a verified live capture PNG to frame in banner.png")
    parser.add_argument("--banner-crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    args = parser.parse_args()
    if args.banner:
        if not args.banner_crop:
            parser.error("--banner requires an explicit --banner-crop")
        banner(args.banner, args.banner_crop, args.out_dir)
    elif args.chart_only:
        if not args.eval:
            parser.error("--chart-only requires an explicit dated --eval artifact")
        chart(args.eval, args.out_dir)
    elif args.manifest:
        assets(args.manifest.resolve(), args.out_dir.resolve())
    else:
        parser.error("Supply --manifest or --chart-only --eval; no implicit source folders")
