"""`people scan` and `people show` — the graph from a terminal.

The library answered here is invented; the point of the assertions is the
shape of the output, not who is in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.people.companion import add_confirmed_person, load_document, people_entries


@dataclass
class _Person:
    id: str
    name: str
    birth_date: date | None = None


@dataclass
class _Bucket:
    time_bucket: str
    count: int


@dataclass
class _Account:
    name: str


class _Library:
    """An Immich holding one household and one person met at a race."""

    people = [
        _Person("p1", "Alex Example"),
        _Person("p2", "Sam Sample"),
        _Person("p3", "Rowan Example"),
    ]
    months = {
        "p1": [(date(2010 + m // 12, m % 12 + 1, 1), 20) for m in range(190)],
        "p2": [(date(2018 + (5 + m) // 12, (5 + m) % 12 + 1, 1), 20) for m in range(90)],
        "p3": [(date(2021, 5, 1), 80), (date(2023, 5, 1), 80)],
    }

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self) -> _Library:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_all_people(self, with_hidden: bool = False) -> list[_Person]:
        return self.people

    def get_time_buckets(self, **kwargs: object) -> list[_Bucket]:
        return [
            _Bucket(f"{month:%Y-%m-%d}T00:00:00.000Z", count)
            for month, count in self.months[str(kwargs["person_id"])]
        ]

    def count_assets_with_people(self, person_ids: list[str]) -> int:
        return 0

    def get_current_user(self) -> _Account:
        return _Account("Alex Example")


def _unwrapped(text: str) -> str:
    """Rich wraps a long path to the terminal width; the path is still there."""
    return "".join(text.split())


def _run(args: list[str], client: object | None = None) -> str:
    from immich_memories.cli import main
    from immich_memories.config_loader import Config

    config = Config()
    config.immich.url = "https://immich.example.com"
    config.immich.api_key = "not-a-real-key"

    # WHY: the CLI group loads the user's real config directory on startup, and
    # the scan would otherwise talk to whatever Immich this machine points at.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.api.sync_client.SyncImmichClient", client or _Library),
    ):
        result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


class TestScan:
    def test_it_writes_the_people_file_and_says_where(self, tmp_path):
        out = tmp_path / "people.yaml"

        output = _run(["people", "scan", "--out", str(out)])

        assert _unwrapped(str(out)) in _unwrapped(output)
        assert len(people_entries(load_document(out))) == 3

    def test_it_writes_the_refreshable_evidence_graph_beside_the_people_file(self, tmp_path):
        out = tmp_path / "people.yaml"

        output = _run(["people", "scan", "--out", str(out)])

        graph_path = tmp_path / "people-graph.json"
        graph = json.loads(graph_path.read_text())
        assert _unwrapped(str(graph_path)) in _unwrapped(output)
        assert len(graph["nodes"]) == 3
        assert graph["edges"] == []

    def test_it_refreshes_a_confirmed_immich_face_below_the_normal_floor(self, tmp_path):
        class LibraryWithUncle(_Library):
            people = [*_Library.people, _Person("p4", "Taylor Sample")]
            months = {**_Library.months, "p4": [(date(2020, 1, 1), 8)]}

        out = tmp_path / "people.yaml"
        add_confirmed_person(out, "Taylor Sample", person_id="p4", role="uncle")

        _run(["people", "scan", "--out", str(out)], LibraryWithUncle)

        taylor = next(entry for entry in people_entries(load_document(out)) if "p4" in entry["ids"])
        assert taylor["inferred"]["evidence"]["count"] == 8
        assert taylor["confirmed"]["role"] == "uncle"

    def test_it_reports_the_tiers_without_reading_out_the_roster(self, tmp_path):
        out = tmp_path / "people.yaml"

        output = _run(["people", "scan", "--out", str(out)])

        assert "inner" in output and "event" in output
        assert "Rowan Example" not in output

    def test_it_says_how_it_worked_out_who_the_owner_is(self, tmp_path):
        out = tmp_path / "people.yaml"

        output = _run(["people", "scan", "--out", str(out)])

        assert "account" in output

    def test_being_told_the_owner_puts_that_name_in_the_file(self, tmp_path):
        out = tmp_path / "people.yaml"

        _run(["people", "scan", "--out", str(out), "--owner", "Sam Sample"])

        assert load_document(out)["owner"]["name"] == "Sam Sample"


class TestTheBareCommand:
    def test_it_still_lists_the_people_immich_knows(self):
        # `immich-memories people` predates the graph and is documented as the
        # way to find the exact name `--person` matches on. Growing subcommands
        # underneath it must not take that away.
        output = _run(["people"], _Library)

        assert "Rowan Example" in output
        assert "3 named people" in output


class TestShow:
    def test_it_reads_the_file_a_scan_left_behind(self, tmp_path):
        out = tmp_path / "people.yaml"
        _run(["people", "scan", "--out", str(out)])

        output = _run(["people", "show", "--file", str(out)])

        assert "Alex Example" in output
        assert "inner" in output

    def test_it_says_so_when_no_scan_has_run(self, tmp_path):
        output = _run(["people", "show", "--file", str(tmp_path / "nothing.yaml")])

        assert "people scan" in output

    def test_one_tier_can_be_asked_for_on_its_own(self, tmp_path):
        out = tmp_path / "people.yaml"
        _run(["people", "scan", "--out", str(out)])

        output = _run(["people", "show", "--file", str(out), "--tier", "event"])

        assert "Rowan Example" in output
        assert "Sam Sample" not in output


def test_the_default_file_sits_in_the_immich_memories_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from immich_memories.people.companion import default_people_path

    assert default_people_path().parent.name == ".immich-memories"
