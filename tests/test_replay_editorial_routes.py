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


def _hashes(directory: Path, episodes: list[tuple[str, str, dict[str, str]]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "evidence-hashes.json"
    path.write_text(
        json.dumps(
            {
                "schema": "episode-evidence-provenance-v1",
                "episodes": [
                    {
                        "group_id": group_id,
                        "evidence_key": key,
                        "assets": [
                            {"asset_id": asset_id, "line_sha256": line_sha}
                            for asset_id, line_sha in assets.items()
                        ],
                    }
                    for group_id, key, assets in episodes
                ],
            }
        )
    )
    return path


def test_evidence_drift_names_the_first_moved_episode_and_counts_its_changed_lines(
    harness, tmp_path
):
    steady = ("g0", "k0", {"a": "h-a"})
    _hashes(tmp_path / "old", [steady, ("g1", "k1", {"b": "h-b", "c": "h-c", "d": "h-d"})])
    _hashes(tmp_path / "new", [steady, ("g1", "k9", {"b": "h-b", "c": "MOVED", "d": "ALSO"})])

    drift = harness.evidence_drift(tmp_path / "new", tmp_path / "old")

    assert "g1" in drift
    assert "2/3" in drift
    assert "c" in drift
    assert "g0" not in drift


def test_evidence_drift_reports_membership_instead_of_guessing_at_a_missing_episode(
    harness, tmp_path
):
    _hashes(tmp_path / "old", [("g1", "k1", {"b": "h-b"})])
    _hashes(tmp_path / "new", [("g2", "k2", {"b": "h-b"})])

    assert "1 new" in harness.evidence_drift(tmp_path / "new", tmp_path / "old")


def test_evidence_drift_says_which_side_it_could_not_read_instead_of_nothing(harness, tmp_path):
    _hashes(tmp_path / "new", [("g1", "k1", {"b": "h-b"})])
    (tmp_path / "old").mkdir()

    gone = harness.evidence_drift(tmp_path / "new", tmp_path / "absent")
    hashless = harness.evidence_drift(tmp_path / "new", tmp_path / "old")

    assert gone == "no evidence diff: the baseline attempt is gone from disk"
    assert hashless == f"no evidence diff: the baseline predates {harness.EVIDENCE_HASHES}"


def test_the_baseline_attempt_directory_comes_from_the_reference_when_it_names_one(harness):
    assert harness.baseline_attempt_dir({}) is None
    named = {
        "head_baseline": {"attempt_dir": "~/runs/head"},
        "accepted_attempt_dir": "~/runs/accepted",
    }
    assert harness.baseline_attempt_dir(named) == Path("~/runs/head").expanduser()
    assert harness.baseline_attempt_dir({"accepted_attempt_dir": "~/runs/accepted"}) == (
        Path("~/runs/accepted").expanduser()
    )


def test_the_store_fingerprint_survives_an_insert_that_a_delete_paid_for(harness, tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "judgments.db") as connection:
        connection.execute("create table judgments (k text)")
        connection.execute("create table other (k text)")
        connection.executemany("insert into judgments values (?)", [("a",), ("b",)])
        connection.execute("insert into other values ('x')")
    banked = harness.store_fingerprint(tmp_path)
    assert set(banked) == {"judgments"}
    assert banked["judgments"][0] == 2

    with sqlite3.connect(tmp_path / "judgments.db") as connection:
        connection.execute("delete from judgments where k = 'a'")
        connection.execute("insert into judgments values ('c')")

    current = harness.store_fingerprint(tmp_path)
    assert current["judgments"][0] == banked["judgments"][0]
    assert current != banked
    assert "judgments" in harness.store_drift(banked, current)


def test_a_changed_cut_names_the_store_when_the_store_is_what_moved(harness, tmp_path):
    banked = {"judgments": [10, 10]}
    route = {"head_baseline": {"attempt_dir": str(tmp_path / "absent")}}
    baseline = {"plan_sha256": "accepted", "store_fingerprint": banked}
    call = {"plan_sha": "other", "same_carriers": False, "route": route, "attempt": tmp_path}

    moved, moved_detail = harness.verdict(baseline=baseline, store={"judgments": [11, 11]}, **call)
    steady, steady_detail = harness.verdict(baseline=baseline, store=banked, **call)
    unbanked, blind_detail = harness.verdict(
        baseline={"plan_sha256": "accepted"}, store=banked, **call
    )

    assert (moved, steady, unbanked) == ("store-moved", "changed", "changed")
    assert "+1 rows" in moved_detail
    assert "store unchanged" in steady_detail
    assert "not banked" in blind_detail


def test_a_pinned_day_lets_a_clock_scoped_route_replay_the_day_it_was_cut(harness):
    route = {"route_args": ["--memory-type", "on_this_day", "--years-back", "3"]}

    argv = harness.route_argv({**route, "target_date": "2024-07-15"})

    assert argv[:4] == ["--memory-type", "on_this_day", "--years-back", "3"]
    assert argv[argv.index("--automation-target-date") + 1] == "2024-07-15"
    assert argv[argv.index("--memory-key") + 1] == "on_this_day:2024-07-15:2024-07-15:"
    assert harness.route_argv(route) == argv[:4]


def _outcome(harness, **overrides):
    fields = {
        "route": "r",
        "seed": "11",
        "status": "changed",
        "seconds": 1.0,
        "blocked_calls": 0,
        "bank_rows_added": 0,
        "attempt_dir": "/runs/a",
        "plan_sha256": "plan",
        "carriers": 3,
        "carriers_identical": False,
        "decision_sha256": "decision",
    }
    return harness.RouteOutcome(**{**fields, **overrides})


def test_a_bank_needs_the_seeds_to_agree_on_the_decision_not_on_the_plan_bytes(harness):
    noisy = [_outcome(harness), _outcome(harness, seed="97", plan_sha256="other")]
    split = [_outcome(harness), _outcome(harness, seed="97", decision_sha256="other")]

    assert harness.bank_refusal(noisy) == ""
    assert "disagreed" in harness.bank_refusal(split)
    assert "cold-needed" in harness.bank_refusal([_outcome(harness, status="cold-needed")])
    assert harness.bank_refusal([_outcome(harness, attempt_dir=None)]) == "no attempt to bank"
