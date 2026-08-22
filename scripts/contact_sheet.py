#!/usr/bin/env python3
"""Render a contact sheet of the clips a memory would use, without rendering it.

Runs the real pipeline and stops the moment selection is final, so what the
sheet shows is what the video would contain. Deliberately NOT --dry-run: that
flag disables the VLM photo scorer and the whole verify/judge/review loop, so
a dry-run sheet audits a pipeline nobody ships.

    scripts/contact_sheet.py --label 2019-12 --out sheets -- \
        --memory-type monthly_highlights --year 2019 --month 12 --duration 60

Everything after `--` is passed to `immich-memories generate` untouched.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections import Counter
from pathlib import Path

from immich_memories.analysis.smart_pipeline import SmartPipeline

THUMB_W, THUMB_H, PAD, LABEL_H, HEADER_H, COLUMNS = 320, 240, 10, 34, 54, 4


class _SelectionIsFinal(Exception):
    """Raised to abandon the run once there is nothing left to learn."""


def _collect(rows: list[dict]):
    original = SmartPipeline.run_selection

    def spy(self, analyzed, progress_callback=None, *, verify=True):
        # verify=True always: the point of the sheet is to see the quality
        # passes, and the caller may not know they can be switched off.
        result = original(self, analyzed, progress_callback, verify=True)
        by_id = {c.clip.asset.id: c for c in analyzed}
        for clip in result.selected_clips:
            member = by_id.get(clip.asset.id)
            start, end = result.clip_segments.get(clip.asset.id, (0.0, 0.0))
            short_side = min(clip.width or 0, clip.height or 0)
            taken = clip.asset.file_created_at
            rows.append(
                {
                    "id": clip.asset.id,
                    "when": taken.strftime("%d %b %H:%M") if taken else "?",
                    "day": taken.strftime("%Y-%m-%d") if taken else "?",
                    "kind": "PHO" if "IMAGE" in str(getattr(clip.asset, "type", "")) else "VID",
                    "city": (clip.asset.exif_info.city if clip.asset.exif_info else None) or "",
                    "score": round(member.score, 2) if member else 0.0,
                    "secs": round(end - start, 1),
                    "res": short_side,
                }
            )
        raise _SelectionIsFinal

    SmartPipeline.run_selection = spy


def _fonts():
    from PIL import ImageFont

    try:
        return (
            ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18),
            ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 26),
        )
    except OSError:
        return ImageFont.load_default(), ImageFont.load_default()


def _draw(rows: list[dict], label: str, subtitle: str, out: Path) -> Path:
    from PIL import Image, ImageDraw

    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config import get_config

    nrows = (len(rows) + COLUMNS - 1) // COLUMNS
    sheet = Image.new(
        "RGB",
        (COLUMNS * (THUMB_W + PAD) + PAD, HEADER_H + nrows * (THUMB_H + LABEL_H + PAD) + PAD),
        (18, 18, 20),
    )
    draw = ImageDraw.Draw(sheet)
    font, bold = _fonts()

    seconds = Counter()
    for row in rows:
        seconds[row["day"]] += row["secs"]
    busiest = seconds.most_common(2)
    draw.text(
        (PAD, 14),
        f"{label}  —  {len(rows)} clips, {sum(r['secs'] for r in rows):.0f}s   |  {subtitle}",
        (235, 235, 235),
        font=bold,
    )

    config = get_config()
    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        for i, row in enumerate(rows):
            x = PAD + (i % COLUMNS) * (THUMB_W + PAD)
            y = HEADER_H + (i // COLUMNS) * (THUMB_H + LABEL_H + PAD)
            try:
                data = client.get_asset_thumbnail(row["id"], "preview")
                image = Image.open(io.BytesIO(data)).convert("RGB")
                image.thumbnail((THUMB_W, THUMB_H))
                sheet.paste(
                    image, (x + (THUMB_W - image.width) // 2, y + (THUMB_H - image.height) // 2)
                )
            except Exception:  # noqa: BLE001 - a missing thumbnail costs a tile, not the sheet
                draw.rectangle([x, y, x + THUMB_W, y + THUMB_H], fill=(40, 40, 44))
            on_busiest = row["day"] in dict(busiest)
            draw.text(
                (x + 2, y + THUMB_H + 4),
                f"{i + 1:2d}. {row['when']}  {row['kind']}  {row['secs']}s",
                (255, 205, 90) if on_busiest else (200, 200, 205),
                font=font,
            )
            res = f"{row['res']}p" if row["res"] else "?"
            draw.text(
                (x + 2, y + THUMB_H + 20),
                f"    {res}  score {row['score']}  {row['city'][:14]}",
                (230, 120, 110) if row["res"] and row["res"] < 1080 else (140, 140, 148),
                font=font,
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Name for the sheet and its file")
    parser.add_argument("--out", required=True, type=Path, help="Directory to write the sheet to")
    parser.add_argument("generate_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = args.generate_args
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    rows: list[dict] = []
    _collect(rows)

    from immich_memories.cli import main as cli_main

    sys.argv = ["immich-memories", "generate", *passthrough]
    try:
        cli_main()
    except (_SelectionIsFinal, SystemExit):
        pass
    except Exception as exc:  # noqa: BLE001 - one failed sheet must not stop a sweep
        print(f"{args.label}: run failed: {type(exc).__name__}: {exc}"[:300])
        return 1

    if not rows:
        print(f"{args.label}: no clips selected")
        return 2

    seconds = Counter()
    for row in rows:
        seconds[row["day"]] += row["secs"]
    subtitle = "busiest: " + ", ".join(f"{d} {s:.0f}s" for d, s in seconds.most_common(2))
    path = _draw(rows, args.label, subtitle, args.out / f"{args.label}.png")
    print(f"{args.label}: {len(rows)} clips -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
