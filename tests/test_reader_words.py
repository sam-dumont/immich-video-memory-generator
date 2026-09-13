"""Engine words never reach a reader: stage names map to plain words on both surfaces."""

from __future__ import annotations

from immich_memories.analysis.selection_trace import ClipStory
from immich_memories.cli._runs_reading import why_text
from immich_memories.operations.reader_words import stage_words


def test_known_stages_have_reader_words() -> None:
    assert stage_words("audience") == "the family-viewing check"
    assert stage_words("shareability") == "the shareability check"
    assert stage_words("final_duplicate_review") == "the duplicate review"
    assert (
        stage_words("owner-required-after-audience")
        == "the owner's tick, after the family-viewing check"
    )


def test_an_unknown_stage_falls_back_to_plain_words() -> None:
    assert stage_words("story-key-compaction") == "the story key compaction pass"


def test_runs_why_speaks_reader_words_and_never_says_admitted() -> None:
    story = ClipStory(
        asset_id="a1",
        facts="a cake on a table",
        shipped=True,
        survived=("audience", "shareability"),
        dropped_at=None,
        admitted_at="owner-required-after-audience",
    )
    text = why_text("a1", story, None)
    assert "admitted" not in text
    assert "passed the family-viewing check, the shareability check" in text
    assert "kept at the owner's tick, after the family-viewing check" in text


def test_runs_why_wraps_a_long_reason_at_the_given_width() -> None:
    story = ClipStory(
        asset_id="a2",
        facts="",
        shipped=False,
        survived=(),
        dropped_at="shareability",
        admitted_at=None,
        reason="a very long explanation " * 8,
    )
    text = why_text("a2", story, None, width=60)
    assert all(len(line) <= 60 for line in text.splitlines()), text
    assert "left out at the shareability check" in text
