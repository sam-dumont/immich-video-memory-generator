"""The parity harness reads attempts, hashes plans and blocks provider hosts as designed."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "replay_editorial_routes.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("replay_editorial_routes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve the owning module by name
    spec.loader.exec_module(module)
    return module


def _attempt(cache_root: Path, key: str, attempt_id: str, plan: dict, *, age: float = 0) -> Path:
    directory = cache_root / "editorial-runs" / key / "attempts" / attempt_id
    directory.mkdir(parents=True)
    (directory / "status.private.json").write_text(json.dumps({"status": "complete"}))
    (directory / "plan.private.json").write_text(json.dumps(plan))
    (cache_root / "editorial-runs" / key / "latest-attempt.private.json").write_text(
        json.dumps({"attempt_id": attempt_id, "directory": str(directory)})
    )
    if age:
        stamp = time.time() - age
        os.utime(directory / "status.private.json", (stamp, stamp))
    return directory


def test_newest_attempt_ignores_runs_older_than_the_launch(harness, tmp_path):
    old = _attempt(tmp_path, "route-a", "20260901T000000Z-old", {"carriers": []}, age=3600)
    assert harness.newest_attempt(tmp_path, time.time() - 60) is None
    new = _attempt(tmp_path, "route-b", "20260910T000000Z-new", {"carriers": []})
    assert harness.newest_attempt(tmp_path, time.time() - 60) == new
    assert old != new


def test_compare_plan_reports_identical_carriers_and_stable_decision_hash(harness, tmp_path):
    plan = {
        "carriers": [{"asset_id": "a1"}, {"asset_id": "b2"}],
        "content_seconds": 7.0,
        "noise": 1,
    }
    path = tmp_path / "plan.private.json"
    path.write_text(json.dumps(plan))
    sha, count, same, decision = harness.compare_plan(path, {"carrier_asset_ids": ["a1", "b2"]})
    assert (count, same) == (2, True)
    assert sha == harness.sha256_bytes(path.read_bytes())
    reordered = dict(reversed(list(plan.items())))
    assert harness.decision_sha256(reordered) == decision
    assert harness.decision_sha256({**plan, "content_seconds": 8.0}) != decision


def test_compare_plan_flags_a_different_carrier_order(harness, tmp_path):
    path = tmp_path / "plan.private.json"
    path.write_text(json.dumps({"carriers": [{"asset_id": "b2"}, {"asset_id": "a1"}]}))
    _sha, _count, same, _decision = harness.compare_plan(path, {"carrier_asset_ids": ["a1", "b2"]})
    assert same is False


def test_provider_hosts_come_from_every_configured_model_endpoint(harness, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "immich:\n  url: http://immich.local:2283\n"
        "advanced:\n  llm:\n    base_url: http://localhost:8080/v1\n"
        "  editorial:\n    preparation:\n      caption_base_url: http://localhost:8092/v1\n"
    )
    assert harness.provider_hosts(config) == {"localhost:8080", "localhost:8092"}
    bare = tmp_path / "bare.yaml"
    bare.write_text("advanced:\n  llm:\n    base_url: http://model.local:9999/v1\n")
    assert harness.provider_hosts(bare) == {"model.local:9999", "localhost:8092"}


def test_bank_rows_counts_only_judgment_tables(harness, tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "judgments.db") as connection:
        connection.execute("create table judgments (k text)")
        connection.execute("create table visual_judgments (k text)")
        connection.execute("create table other (k text)")
        connection.executemany("insert into judgments values (?)", [("a",), ("b",)])
        connection.execute("insert into other values ('x')")
    assert harness.bank_rows(tmp_path) == 2
    assert harness.bank_rows(tmp_path / "missing") == 0
    store = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store) as connection:
        connection.execute("create table editorial_period_insights (k text)")
        connection.execute("insert into editorial_period_insights values ('p')")
    config = tmp_path / "config.yaml"
    config.write_text(f"advanced:\n  editorial:\n    annotation_database: {store}\n")
    assert harness.bank_rows(tmp_path, config) == 3


def test_route_argv_strips_the_command_word_output_and_harness_owned_flags(harness):
    route = {
        "generate_args": [
            "generate",
            "--year",
            "2024",
            "--no-render",
            "--no-music",
            "--quiet",
            "--output",
            "/somewhere/film.mp4",
            "--duration",
            "60",
        ]
    }
    assert harness.route_argv(route) == ["--year", "2024", "--duration", "60"]
    assert harness.route_argv({"route_args": ["--month", "2"], "generate_args": ["generate"]}) == [
        "--month",
        "2",
    ]


def test_reference_routes_skip_metadata_and_prefer_the_head_baseline(harness):
    reference = {
        "schema_version": 3,
        "generated_for": "x",
        "monthly": {
            "generate_args": ["--year", "2024"],
            "carrier_asset_ids": ["old"],
            "accepted_plan_sha256": "a",
            "head_baseline": {"carrier_asset_ids": ["new"], "plan_sha256": "b"},
        },
        "fresh": {"generate_args": ["--years-back", "3"], "head_baseline": None},
    }
    routes = harness.reference_routes(reference)
    assert sorted(routes) == ["fresh", "monthly"]
    assert harness.baseline_of(routes["monthly"]) == {
        "carrier_asset_ids": ["new"],
        "plan_sha256": "b",
    }
    assert harness.baseline_of(routes["fresh"]) is None
