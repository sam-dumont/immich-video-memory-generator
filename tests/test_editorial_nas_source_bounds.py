"""What a NAS install can refuse on its own: its own films, stub clips, runaway clips, screens."""

import sqlite3
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_source_gate import screen_document_rejections
from immich_memories.analysis.editorial_speech import resolve_speech_cuts
from immich_memories.analysis.editorial_structure_budget import (
    MIN_MOTION_SECONDS,
    MOTION_CAP_SECONDS,
)
from immich_memories.analysis.generated_source_provenance import (
    generated_source_ids,
    recorded_generated_ids,
)


def _line(asset_id, *, heads=(), description="", text=""):
    return SimpleNamespace(asset_id=asset_id, heads=heads, description=description, text=text)


def _batch(*lines):
    return SimpleNamespace(lines=list(lines))


def test_a_phone_screenshot_is_refused_as_a_source_not_only_as_a_carrier():
    """The pixel size is a metadata fact, so it answers with or without the caption seat."""
    batch = _batch(
        _line(
            "shot",
            heads=(("doc_docling", "photograph"),),
            text="2024-02-01 | resolution:1170x2532",
        ),
        _line(
            "photo",
            heads=(("doc_docling", "photograph"),),
            text="2024-02-01 | resolution:4032x3024",
        ),
    )
    assert screen_document_rejections(batch) == {"shot": "screenshot-resolution"}


def test_a_video_the_same_size_as_a_phone_screen_is_not_a_screenshot():
    batch = _batch(_line("clip", text="2024-02-01 | VIDEO 4.0 s | resolution:1170x2532"))
    assert screen_document_rejections(batch) == {}


def test_the_document_head_still_answers_first():
    batch = _batch(
        _line("scan", heads=(("doc_docling", "table"),), text="2024-02-01 | resolution:1170x2532")
    )
    assert screen_document_rejections(batch) == {"scan": "screen-docling:table"}


def test_our_own_uploaded_films_are_named_by_the_tag_and_by_the_receipts(tmp_path):
    database = tmp_path / "cache.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE pipeline_runs (run_id TEXT, immich_asset_id TEXT)")
        db.executemany(
            "INSERT INTO pipeline_runs VALUES (?, ?)",
            [("r1", "uploaded"), ("r2", None), ("r3", "  "), ("r4", "uploaded")],
        )
    assert recorded_generated_ids(database) == frozenset({"uploaded"})
    assert generated_source_ids(tagged=lambda: ["tagged"], cache_database=database) == frozenset(
        {"tagged", "uploaded"}
    )


def test_a_library_with_no_receipts_file_still_reads_the_tag(tmp_path):
    assert recorded_generated_ids(tmp_path / "absent.db") == frozenset()
    assert generated_source_ids(
        tagged=lambda: ["tagged"], cache_database=tmp_path / "absent.db"
    ) == frozenset({"tagged"})


def test_a_server_that_refuses_the_tag_query_leaves_the_receipts_answering(tmp_path):
    def refused():
        raise OSError("no tag scope on this key")

    assert (
        generated_source_ids(tagged=refused, cache_database=tmp_path / "absent.db") == frozenset()
    )


def _video(asset_id, seconds, regions=()):
    return {
        "asset_id": asset_id,
        "kind": "video",
        "seconds": min(seconds, MOTION_CAP_SECONDS),
        "raw_seconds": seconds,
        "start_time": 0.0,
        "speech_regions": list(regions),
    }


def test_a_clip_finishing_its_sentence_stops_at_twice_the_motion_cap():
    """The speech-safe end used to follow one utterance to the end of a 17-second source."""
    [out] = resolve_speech_cuts([_video("long", 30.0)], lambda _a: [(0.0, 25.0)], buffer=0.0)
    assert out["seconds"] == pytest.approx(2 * MOTION_CAP_SECONDS)


def test_a_clip_whose_sentence_ends_early_is_not_stretched_to_the_cap():
    [out] = resolve_speech_cuts([_video("short", 30.0)], lambda _a: [(0.0, 7.5)], buffer=0.0)
    assert out["seconds"] == pytest.approx(7.5)


def test_the_admission_floor_is_two_seconds():
    assert MIN_MOTION_SECONDS == 2.0
