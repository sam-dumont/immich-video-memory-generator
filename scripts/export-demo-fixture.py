#!/usr/bin/env python3
"""Write the Remotion demo's fixture module from the hermetic library.

The demo recreates the UI in React over the same pictures the fake Immich
serves, so the two must agree: the thesis, the cut in capture order, the pool
page's first page and its counters. This exports them from
``tests/e2e/fake_library`` into ``docs-site/remotion/src/fixture.ts`` so the
demo cannot drift from the fixture. Run it through ``make demo-fixture``.

It also measures the film the demo ends on, because the scene that plays it
cannot be told its shape in a constant: a re-cut moves every one of them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.e2e.fake_immich import TIMELINE_ASSETS  # noqa: E402
from tests.e2e.fake_library import (  # noqa: E402
    CARRIERS,
    LIBRARY,
    STORY_OF,
    THESIS,
    pool_line,
    summary_line,
)

from immich_memories.api.models import Asset  # noqa: E402
from immich_memories.config_loader import Config  # noqa: E402
from immich_memories.operations.reader_words import stage_words  # noqa: E402
from immich_memories.operations.storyboard import storyboard_from_plan  # noqa: E402
from immich_memories.processing.editorial_timing import (  # noqa: E402
    bind_editorial_timeline,
    build_editorial_timing_policy,
)

# The pass the fixture's editorial route records its rejections under; the pool
# page prints the reader's words for it, not the engine's name.
DROP_STAGE = "picture_review"

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs-site" / "remotion" / "src" / "fixture.ts"
FILM = ROOT / "docs-site" / "remotion" / "public" / "output-preview.mp4"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
POOL_PAGE = 20

# How often the film is sampled when looking for the end of its pictures. The
# ending card is several seconds long, so twice a second is far finer than the
# answer needs and still one decode of the file.
FILM_SAMPLE_FPS = 2


def _taken_label(taken_at: str) -> str:
    return f"{MONTHS[int(taken_at[5:7]) - 1]} {taken_at[8:10]} {taken_at[11:16]}"


def _board(pictures):
    """The same fixture cut and 60-second title policy as the hermetic UI."""
    carriers = [
        {
            "asset_id": p.asset_id,
            "seconds": p.seconds,
            "taken": p.taken_at,
            "kind": "video" if p.is_video else "still",
        }
        for p in pictures
    ]
    assets = {raw["id"]: Asset.model_validate(raw) for raw in TIMELINE_ASSETS}
    policy = build_editorial_timing_policy(
        config=Config(),
        target_seconds=60,
        memory_type="monthly_highlights",
        date_start=date(2024, 6, 1),
        date_end=date(2024, 6, 30),
    )
    timeline = policy.resolve(carriers, assets)
    return storyboard_from_plan(
        {
            "carriers": carriers,
            "render_timing": bind_editorial_timeline(
                policy, timeline, [p.asset_id for p in pictures]
            ),
        },
        None,
    )


def _outcome(picture, timecodes: dict[str, str]) -> str:
    """The line the pool prints under a thumbnail, in CandidateFates' words."""
    if picture.shipped:
        return f"In the cut at {timecodes[picture.asset_id]}: {picture.caption}"
    reason = picture.drop_reason or "not part of any story the month tells"
    return f"Left out at {stage_words(DROP_STAGE)}: {reason}"


def _edge_energy() -> list[tuple[float, float]]:
    """Per-sample sharpness of the film, as (seconds, energy).

    `edgedetect` reads a flat 0 on the film's blurred ending card and 8 to 12 on
    a photograph, which is what makes the end of the pictures findable rather
    than guessable.
    """
    probe = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(FILM),
            "-vf",
            f"fps={FILM_SAMPLE_FPS},edgedetect=mode=wires,signalstats,"
            "metadata=print:key=lavfi.signalstats.YAVG:file=-",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    samples = re.findall(r"pts_time:([0-9.]+)\s*\nlavfi\.signalstats\.YAVG=([0-9.]+)", probe)
    return [(float(time), float(energy)) for time, energy in samples]


def _film_facts() -> dict[str, object]:
    """What the demo's last scene needs to know about the film it plays.

    `OutputPreviewScene` plays the film's closing seconds and must stop on a
    photograph: the film ends on a blurred card, and a demo that ends on mush is
    the README's first impression. Both numbers move on every re-cut, so both
    are measured here rather than typed there.
    """
    duration = float(
        subprocess.run(  # noqa: S603
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(FILM),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    samples = _edge_energy()
    energies = sorted(energy for _, energy in samples)
    picture_level = energies[len(energies) // 2]
    sharp = [time for time, energy in samples if energy >= picture_level / 2]
    return {
        "seconds": round(duration, 2),
        # The last sample still showing a photograph, not the crossfade into the card.
        "pictures_end": round(max(sharp), 2),
        # The UI's own rounding, from `_format_file_size` in ui/pages/_step4_generate.py.
        "size": f"{FILM.stat().st_size / 1_048_576:.0f} MB",
    }


def _shots(pictures, board) -> list[dict]:
    shots = []
    previous_month = None
    for picture, timed in zip(pictures, board.shots, strict=True):
        month = picture.taken_at[:7]
        shot = {
            "picture": f"library/{picture.source.name}",
            "day": picture.taken_at[:10],
            "motion": picture.is_video,
            "seconds": round(timed.seconds, 2),
            "start": timed.start,
            "story": STORY_OF[picture.asset_id].title,
            "reason": picture.caption,
        }
        if month != previous_month:
            shot["chapter"] = f"{MONTHS[int(month[5:7]) - 1]} {month[:4]}".replace("Jun ", "June ")
            previous_month = month
        shots.append(shot)
    return shots


def main() -> None:
    board = _board(CARRIERS)
    recut = _board(CARRIERS[1:])
    shots = _shots(CARRIERS, board)
    timecodes = {shot.asset_id: shot.timecode for shot in board.shots}
    pool = [
        {
            "picture": f"library/{picture.source.name}",
            "motion": picture.is_video,
            "taken": _taken_label(picture.taken_at),
            "file": picture.filename,
            "favourite": picture.is_favorite,
            "ticked": picture.shipped,
            "seconds": int(picture.seconds),
            "outcome": _outcome(picture, timecodes),
        }
        for picture in LIBRARY[:POOL_PAGE]
    ]
    videos = sum(1 for picture in LIBRARY if picture.is_video)
    film = _film_facts()
    body = "\n".join(
        [
            "// Generated by scripts/export-demo-fixture.py from tests/e2e/fake_library.py",
            "// and from docs-site/remotion/public/output-preview.mp4.",
            "// Do not edit: run `make demo-fixture` after the fixture library or the film changes.",
            "",
            "export type Shot = {",
            "  picture: string;",
            "  day: string;",
            "  motion: boolean;",
            "  seconds: number;",
            "  start: number;",
            "  story: string;",
            "  reason: string;",
            "  /** Set on the first picture of a month: the real page prints a chapter label. */",
            "  chapter?: string;",
            "};",
            "",
            "export type PoolCard = {",
            "  picture: string;",
            "  motion: boolean;",
            "  taken: string;",
            "  file: string;",
            "  favourite: boolean;",
            "  ticked: boolean;",
            "  seconds: number;",
            "  /** What the saved cut did with this picture, as the pool page prints it. */",
            "  outcome: string;",
            "};",
            "",
            f"export const THESIS = {json.dumps(THESIS)};",
            f"export const SUMMARY_LINE = {json.dumps(summary_line())};",
            f"export const POOL_LINE = {json.dumps(pool_line())};",
            f"export const POOL_TOTAL = {len(LIBRARY)};",
            f"export const POOL_VIDEOS = {videos};",
            f"export const POOL_PAGE = {POOL_PAGE};",
            f"export const CUT_COUNT = {len(CARRIERS)};",
            f"export const CUT_SECONDS = {int(sum(picture.seconds for picture in CARRIERS))};",
            f"export const CUT_FILM_SECONDS = {board.film_seconds};",
            f"export const RECUT_FILM_SECONDS = {recut.film_seconds};",
            "",
            "/** How long output-preview.mp4 runs, measured. */",
            f"export const FILM_SECONDS = {film['seconds']};",
            "/** The last second of it that still shows a photograph, before the ending card. */",
            f"export const FILM_PICTURES_END = {film['pictures_end']};",
            "/** Its size, in the words the completion page prints. */",
            f"export const FILM_SIZE = {json.dumps(film['size'])};",
            "",
            f"export const SHOTS: Shot[] = {json.dumps(shots, indent=2, ensure_ascii=False)};",
            f"export const RECUT_SHOTS: Shot[] = {json.dumps(_shots(CARRIERS[1:], recut), indent=2, ensure_ascii=False)};",
            f"export const POOL: PoolCard[] = {json.dumps(pool, indent=2, ensure_ascii=False)};",
            "",
        ]
    )
    OUT.write_text(body)
    print(
        f"wrote {OUT.relative_to(OUT.parents[3])}: {len(shots)} shots, {len(pool)} pool cards, "
        f"film {film['seconds']}s with pictures to {film['pictures_end']}s ({film['size']})"
    )


if __name__ == "__main__":
    main()
