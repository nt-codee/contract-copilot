# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "matplotlib"]
# ///
"""Rebuild the README images from files in this repo.

    uv run docs/public/make_assets.py [--shots DIR] [--chart-only]

The signed-out screens come from the live site: run `node docs/public/shoot_live.mjs DIR` first, then pass DIR
here (default /tmp/live-shots). The signed-in screens (account menu, admin overview, activity) come from
`node docs/public/shoot_app.mjs DIR` (default /tmp/app-shots, pass it with --app). They use the app's offline
sample data: invented people and a made-up company. The evidence view and the company-rules page are taken from
web/screenshots/r4/signed-in-mock when that folder exists.

The source screenshots live in the working repo and not in the public export, so the generated images are
committed under docs/public. Numbers in the chart come from the newest
eval/results/*.json, nothing is typed in by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "public"
MOCK_SHOTS = ROOT / "web" / "screenshots" / "r4" / "signed-in-mock"
APP = Path("/tmp/app-shots")
if "--app" in __import__("sys").argv:
    APP = Path(__import__("sys").argv[__import__("sys").argv.index("--app") + 1])
SHOTS = Path("/tmp/live-shots")
if "--shots" in __import__("sys").argv:
    SHOTS = Path(__import__("sys").argv[__import__("sys").argv.index("--shots") + 1])

INK, REDLINE, AMBER, PAPER = "#121A2B", "#B3261E", "#C98A00", "#F6F2E9"
MUTED = "#5B6577"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path, idx in (("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
                      ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
                      ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                       else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)):
        try:
            return ImageFont.truetype(path, size, index=idx)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def banner() -> None:
    w, h = 1600, 600
    canvas = Image.new("RGB", (w, h), INK)
    d = ImageDraw.Draw(canvas)
    # a red margin rule, like a mark-up in the margin of a page
    d.rectangle((72, 96, 78, 190), fill=REDLINE)
    d.text((100, 92), "Contract Co-pilot", font=font(34, True), fill="#FFFFFF")
    d.text((100, 150), "Malaysia  ·  Singapore", font=font(20), fill="#9FB0CC")

    tag = font(54, True)
    for i, line in enumerate(("Know what you're", "signing, before", "you sign it.")):
        d.text((72, 236 + i * 66), line, font=tag, fill=PAPER)
    for i, line in enumerate(("Every risky clause boxed on the contract's own",
                              "pages, with the law that applies and a reply",
                              "ready to send.")):
        d.text((72, 452 + i * 32), line, font=font(23), fill="#C8D3E6")

    shot = Image.open(SHOTS / "review-selected-1440.png").convert("RGB")
    sw = 800
    shot = shot.resize((sw, round(shot.height * sw / shot.width)), Image.LANCZOS)
    shot = rounded(shot, 14)
    x, y = w - sw - 48, 64
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x, y + 14, x + sw, y + shot.height + 14), 14, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow)
    canvas.alpha_composite(shot, (x, y))  # runs off the bottom edge on purpose
    canvas.convert("RGB").save(OUT / "banner.png", optimize=True)


def screens() -> None:
    """Crop and resize the live product screens to a README-friendly width."""
    (OUT / "screens").mkdir(exist_ok=True)
    # (source folder, source name, output name, crop box in source pixels or None)
    jobs = (
        (SHOTS, "review-selected-1440", "reader", None),
        (SHOTS, "review-draft-1440", "reply", None),
        (SHOTS, "review-1440", "dates", (979, 0, 1440, 410)),   # the verdict card with its key date
        (SHOTS, "knowledge-entry-1440", "citation", None),
        (SHOTS, "library-1440", "library", (0, 0, 1440, 1100)),
        (MOCK_SHOTS, "rules-1440", "rules", (0, 0, 1440, 1100)),
        # signed in, sample data
        (APP, "account-menu-1440", "team-account", (0, 320, 320, 900)),   # sidebar with the account / workspace menu
        (APP, "admin-1440", "team-overview", (0, 0, 1440, 1100)),         # usage dashboard, aggregates only
        (MOCK_SHOTS, "evidence-1440", "team-evidence", None),             # contract beside the company rule it breaks
        (APP, "activity-1440", "team-activity", (0, 0, 1440, 1000)),      # audit trail
    )
    for folder, src, name, box in jobs:
        f = folder / f"{src}.png"
        if not f.exists():
            print(f"skip {name}: {f} not found")
            continue
        im = Image.open(f).convert("RGB")
        if box:
            im = im.crop((box[0], box[1], box[2], min(box[3], im.height)))
        if im.width > 1100:
            im = im.resize((1100, round(im.height * 1100 / im.width)), Image.LANCZOS)
        im.save(OUT / "screens" / f"{name}.png", optimize=True)


def newest_eval() -> tuple[Path, dict]:
    p = ROOT / "eval" / "results" / "20261018T075234Z.json"  # pinned to the run the README quotes
    return p, json.loads(p.read_text())


def chart() -> None:
    p, data = newest_eval()
    order = {"nda": 0, "msa": 1, "emp": 2, "ten": 3, "hp": 4}
    rows = sorted(data["scores"], key=lambda r: (order.get(r["id"].split("-")[0], 9), r["id"]))
    names = [r["id"] for r in rows]
    secs = [r["latency_ms"] / 1000 for r in rows]
    s_sorted = sorted(secs)
    mid = len(s_sorted) // 2
    p50 = (s_sorted[mid - 1] + s_sorted[mid]) / 2 if len(s_sorted) % 2 == 0 else s_sorted[mid]
    colours = [REDLINE if r["overall_risk"] == "high" else AMBER if r["overall_risk"] == "medium" else INK
               for r in rows]

    plt.rcParams.update({"font.family": ["Helvetica Neue", "Helvetica", "DejaVu Sans"], "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=100)
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    bars = ax.bar(range(len(rows)), secs, color=colours, width=0.68)
    ax.axhline(p50, color=INK, lw=1, ls=(0, (4, 3)))
    for b, s in zip(bars, secs):
        ax.text(b.get_x() + b.get_width() / 2, s + 0.8, f"{s:.0f}", ha="center", color=INK, fontsize=8, fontweight="bold")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(names, fontsize=8, color=INK, rotation=60, ha="right")
    ax.set_ylabel("seconds to review", color=INK)
    ax.tick_params(colors=INK, length=0)
    ax.set_ylim(0, max(secs) * 1.15)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(INK)
    ax.yaxis.grid(True, color="#DDD6C6", lw=0.8)
    ax.set_axisbelow(True)
    ax.set_title("Time to review each synthetic contract", loc="left", color=INK, fontsize=13,
                 fontweight="bold", pad=24)
    ax.text(0, 1.04, f"{len(rows)} contracts, run {p.stem}. Bar colour = overall risk the review gave: "
            "high (red), medium (amber), low (ink). "
            f"Dashed line = median, {p50:.1f} s.", transform=ax.transAxes, color=MUTED, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "eval-latency.svg", format="svg", metadata={"Date": None, "Creator": "make_assets.py"})


if __name__ == "__main__":
    import sys
    if "--chart-only" in sys.argv:
        chart()
    else:
        banner()
        screens()
        chart()
    print("done")
