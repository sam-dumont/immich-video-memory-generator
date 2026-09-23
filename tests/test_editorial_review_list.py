"""The shots between 0.2 and 0.5 that nothing else holds, written down for the owner."""

import json
import sqlite3

from immich_memories.analysis.editorial_review_list import (
    FILENAME,
    exposure_probabilities,
    review_count,
    review_note,
    to_check,
    write_review_list,
)


def _carriers(*asset_ids):
    return [{"asset_id": asset_id, "taken": "2024-02-03T09:00:00+00:00"} for asset_id in asset_ids]


def test_a_grey_zone_shot_nothing_else_holds_is_listed():
    rows = to_check(_carriers("a1"), {"a1": {"verdict": "share", "finding": "none"}}, {"a1": 0.35})

    assert rows == [
        {
            "asset_id": "a1",
            "exposure_probability": 0.35,
            "taken": "2024-02-03T09:00:00+00:00",
            "verdict": "share",
        }
    ]


def test_a_shot_its_own_evidence_already_holds_is_not_listed_again():
    verdicts = {"a1": {"verdict": "family_only", "finding": "exposure_evidence"}}

    assert to_check(_carriers("a1"), verdicts, {"a1": 0.35}) == []


def test_a_shot_the_head_holds_outright_is_held_not_listed():
    """0.55 is over the cut: it is `exposure_evidence`, and the list is for the undecided."""
    verdicts = {"a1": {"verdict": "family_only", "finding": "exposure_evidence"}}

    assert to_check(_carriers("a1"), verdicts, {"a1": 0.55}) == []


def test_a_shot_under_the_band_is_not_mentioned():
    assert to_check(_carriers("a1"), {"a1": {"finding": "none"}}, {"a1": 0.19}) == []


def test_an_empty_list_still_writes_a_file_and_counts_zero(tmp_path):
    assert write_review_list(tmp_path, []) == 0
    assert json.loads((tmp_path / FILENAME).read_text())["pictures"] == []
    assert review_count(tmp_path) == 0
    assert review_note(tmp_path, "a1") == ""


def test_the_written_list_is_what_the_summary_counts_and_runs_why_reads(tmp_path):
    rows = to_check(_carriers("a1", "b2"), {}, {"a1": 0.35, "b2": 0.05})

    assert write_review_list(tmp_path, rows) == 1
    assert review_count(tmp_path) == 1
    assert "0.35" in review_note(tmp_path, "a1")
    assert review_note(tmp_path, "b2") == ""


def test_the_probability_comes_from_the_row_the_head_already_wrote(tmp_path):
    from immich_memories.store.editorial_preparation import initialize

    store = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store) as connection:
        initialize(connection)
        connection.executemany(
            "INSERT INTO head_facts VALUES (?,?,?,?,?,?,?)",
            [
                ("a1", "nsfw_marqo", "det-v3", "no", 0.31, "k", "now"),
                ("b2", "nsfw_marqo", "det-v2", "no", 0.31, "k", "now"),
            ],
        )

    assert exposure_probabilities(store, ["a1", "b2"], "det-v3") == {"a1": 0.31}
    assert exposure_probabilities(tmp_path / "absent.sqlite", ["a1"], "det-v3") == {}


def test_the_summary_says_how_many_even_when_there_are_none():
    from immich_memories.cli._run_summary import render_run_summary

    text = render_run_summary(
        total_seconds=10.0,
        analysis_seconds=6.0,
        generation_seconds=4.0,
        eligible=10,
        planned=3,
        counters=None,
        review_before_sharing=0,
    )

    assert "0 pictures to check before sharing" in text


def test_a_finished_cut_writes_its_grey_zone_shots_at_the_version_the_run_reads(tmp_path):
    from immich_memories.analysis.editorial_review_list import write_for_cut
    from immich_memories.store.editorial_preparation import initialize

    store = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store) as connection:
        initialize(connection)
        connection.executemany(
            "INSERT INTO head_facts VALUES (?,?,?,?,?,?,?)",
            [
                ("a1", "nsfw_marqo", "det-v3", "no", 0.31, "k", "now"),
                ("b2", "nsfw_marqo", "det-v3", "no", 0.42, "k", "now"),
            ],
        )

    from types import SimpleNamespace

    source = SimpleNamespace(
        store_path=store,
        artifact_dir=tmp_path,
        config=SimpleNamespace(editorial=SimpleNamespace(head_versions={"nsfw_marqo": "det-v3"})),
    )
    written = write_for_cut(
        source,
        _carriers("a1", "b2"),
        {"b2": {"finding": "exposure_chain"}},
    )

    assert written == 1
    assert review_note(tmp_path, "a1") and not review_note(tmp_path, "b2")
