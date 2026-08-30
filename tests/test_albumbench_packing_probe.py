"""AlbumBench packing ablations keep the labels fixed while changing request count."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_albumbench_packing as probe


def _album(album_id: str, event: str, count: int, *, split: str = "test") -> dict:
    return {
        "album_id": album_id,
        "event_type": event,
        "num_images": count,
        "images": [
            {"image_id": f"{index:03d}", "path": f"images/{album_id}/{index:03d}.jpg"}
            for index in range(1, count + 1)
        ],
        "metadata": {"split": split},
    }


def _task(album_id: str, query: int, selected: int, total: int) -> dict:
    image_ids = [f"{index:03d}" for index in range(1, total + 1)]
    return {
        "task_id": f"task-{album_id}-{query}",
        "album_id": album_id,
        "task_type": "intent_selection",
        "prompt": f"Select query {query}.",
        "image_ids": image_ids,
        "target": {"selected_images": image_ids[:selected]},
        "metadata": {"source_task_type": f"selection_query_{query}"},
    }


def test_sample_uses_the_smallest_test_album_and_the_most_balanced_query() -> None:
    albums = [
        _album("train", "BeachTrip", 20, split="train"),
        _album("large", "BeachTrip", 50),
        _album("small", "BeachTrip", 30),
    ]
    tasks = [
        _task("small", 1, 2, 30),
        _task("small", 2, 14, 30),
        _task("small", 3, 29, 30),
        _task("large", 1, 25, 50),
    ]

    sample = probe.select_probe_tasks(albums, tasks, event_types=("BeachTrip",))

    assert len(sample) == 1
    assert sample[0].album_id == "small"
    assert sample[0].task_id == "task-small-2"
    assert len(sample[0].expected_selected) == 14


def test_batches_preserve_order_and_reduce_requests() -> None:
    values = tuple(f"I{index:03d}" for index in range(1, 14))

    packed = probe.batches(values, 6)

    assert packed == (values[:6], values[6:12], values[12:])
    assert probe.request_count(len(values), 1) == 13
    assert probe.request_count(len(values), 4) == 4
    assert probe.request_count(len(values), 6) == 3


def test_selection_response_is_strict_unique_and_grounded() -> None:
    raw = json.dumps(
        {
            "schema_version": probe.SELECTION_SCHEMA,
            "selected": ["I001", "I003"],
        }
    )

    assert probe.read_selection(raw, ("I001", "I002", "I003")) == ("I001", "I003")

    duplicate = raw.replace('["I001", "I003"]', '["I001", "I001"]')
    with pytest.raises(ValueError, match="unique"):
        probe.read_selection(duplicate, ("I001", "I002", "I003"))

    ungrounded = raw.replace("I003", "I999")
    with pytest.raises(ValueError, match="grounded"):
        probe.read_selection(ungrounded, ("I001", "I002", "I003"))


def test_selection_metrics_report_quality_and_not_only_exact_match() -> None:
    metrics = probe.selection_metrics(
        all_ids=("1", "2", "3", "4", "5"),
        expected=frozenset({"1", "2", "3"}),
        predicted=frozenset({"2", "3", "4"}),
    )

    assert metrics == {
        "true_positive": 2,
        "false_positive": 1,
        "false_negative": 1,
        "true_negative": 1,
        "precision": pytest.approx(2 / 3),
        "recall": pytest.approx(2 / 3),
        "f1": pytest.approx(2 / 3),
        "accuracy": pytest.approx(3 / 5),
        "exact": False,
    }
