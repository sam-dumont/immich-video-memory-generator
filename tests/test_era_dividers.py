"""A then-and-now has to name both of its eras on screen.

Year dividers are generated from the year *changes* in a cut, and the first
change is dropped: a memory running continuously through its years opens on a
title card that already names the one it starts in, so a card there would say
it twice.

A then-and-now's title names its two ends as a pair. Nothing says which era the
opening block belongs to, and the older half — the whole reason the memory
exists — played unlabeled.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.title_divider_planner import TitleDividerPlanner


def _clip(date: str) -> AssemblyClip:
    return AssemblyClip(path=Path(f"/{date}.mp4"), duration=5.0, asset_id=date, date=date)


def _cut() -> list[AssemblyClip]:
    """A then-and-now as the assembler orders it: oldest era first, grouped."""
    return [_clip("2016-03-01"), _clip("2016-07-01"), _clip("2026-04-01"), _clip("2026-09-01")]


def _planner(memory_type: str | None, max_dividers: int) -> tuple[TitleDividerPlanner, MagicMock]:
    # WHY: the card generator renders video through FFmpeg; the planner under
    # test only needs to know which cards it was asked for.
    generator = MagicMock()
    generator.generate_year_divider.side_effect = lambda year: SimpleNamespace(
        path=Path(f"/divider-{year}.mp4")
    )
    settings = SimpleNamespace(
        max_dividers=max_dividers,
        month_divider_duration=2.0,
        memory_type=memory_type,
    )
    return TitleDividerPlanner(generator, settings), generator


def test_both_eras_get_a_card() -> None:
    planner, _ = _planner("then_and_now", max_dividers=2)

    paths = planner.generate_year_dividers(_cut(), None)

    assert sorted(paths) == [2016, 2026]


def test_the_older_eras_card_opens_the_memory() -> None:
    """Order matters as much as existence — the label has to precede its block."""
    planner, _ = _planner("then_and_now", max_dividers=2)
    clips = _cut()

    result = planner.build_clips_with_year_dividers(
        clips, planner.generate_year_dividers(clips, None)
    )

    assert [c.asset_id for c in result] == [
        "year_divider_2016",
        "2016-03-01",
        "2016-07-01",
        "year_divider_2026",
        "2026-04-01",
        "2026-09-01",
    ]


def test_a_continuous_memory_still_opens_without_a_card() -> None:
    """The rule the then-and-now case is an exception to, kept honest.

    A year in review that runs 2025 into 2026 is titled after the year it opens
    in, so a card naming it again is noise.
    """
    planner, _ = _planner("year_in_review", max_dividers=1)
    clips = [_clip("2025-11-01"), _clip("2026-02-01")]

    result = planner.build_clips_with_year_dividers(
        clips, planner.generate_year_dividers(clips, None)
    )

    assert [c.asset_id for c in result] == ["2025-11-01", "year_divider_2026", "2026-02-01"]
