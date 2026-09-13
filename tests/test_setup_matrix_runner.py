"""How the runner executes a plan: one cell at a time per host, pipes included.

The matrix exists to measure what each setup costs. Two cells sharing a host
share its cores, so every number the run publishes would be wrong. The remote
lanes also move their files with tar over ssh, which is two commands and a pipe
rather than one argv, and that has to survive a binary payload.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402
import setup_matrix  # noqa: E402
from setup_matrix_plan import Cell, CellPlan, Plan, Step  # noqa: E402

# Excerpts of the first real Mac lane run, copied out of its own logs.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "setup_matrix"


def _cell_plan(
    cell_id: str,
    lane: str,
    steps: tuple[Step, ...] = (),
    cache_dir: str = "",
    container_limits: str = "",
) -> CellPlan:
    cell = Cell(
        id=cell_id,
        lane=lane,
        reader="local",
        facts="local",
        tier="no_captions",
        why="",
        requires_env=(),
        config={},
        inference_overlay=False,
    )
    return CellPlan(
        cell=cell,
        pins={},
        config_yaml="",
        steps=steps,
        manifests={},
        app_credentials=(),
        cache_dir=cache_dir,
        container_limits=container_limits,
    )


def _plan_of(*items: CellPlan) -> Plan:
    return Plan(
        library="demo",
        month="2024-06",
        image="image:tag",
        anonymize_required=False,
        cells=tuple(items),
    )


def _replayed_mac_cell(cache: Path) -> CellPlan:
    """A cell whose three steps replay the logs the first real Mac run wrote.

    Real subprocesses, because what is under test is what the runner reads back
    out of a child it actually spawned.
    """
    return _cell_plan(
        "mac-local",
        "mac",
        cache_dir=str(cache),
        steps=(
            Step("prepare-cold", ("cat", str(FIXTURES / "prepare-cold.stdout.txt"))),
            Step("prepare-warm", ("cat", str(FIXTURES / "prepare-cold.stdout.txt"))),
            Step("generate", ("cat", str(FIXTURES / "generate-saved.stdout.txt"))),
        ),
    )


def test_a_cell_is_cold_unless_its_own_cache_is_already_on_disk(tmp_path) -> None:
    """Cells no longer share a bank, so `cold` means this cell's cache did not exist."""
    cache = tmp_path / "cache"
    item = _replayed_mac_cell(cache)

    fresh = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")
    cache.mkdir(parents=True, exist_ok=True)
    rerun = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert fresh["prepare_cache_primed"] is False
    assert rerun["prepare_cache_primed"] is True


def test_a_local_cell_reads_back_what_its_prepare_and_its_generate_printed(tmp_path) -> None:
    """The film and the per-producer table were both on the terminal and in neither record."""
    item = _replayed_mac_cell(tmp_path / "cache")

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["prepared"]["pictures"] == 133
    assert record["prepared"]["seconds_per_picture"] == 0.1153
    assert [row["producer"] for row in record["prepared"]["producers"]] == ["previews", "captions"]
    # The CLI prints the path relative to the directory the runner starts it in.
    assert record["video"]["path"] == str(
        setup_matrix.REPO_ROOT
        / "output/setup-matrix/demo/run1/mac-local"
        / "mac-local_af64ef21_20260913_221211_52d6/mac-local_af64ef21.mp4"
    )


@pytest.mark.skipif(not Path("/usr/bin/time").is_file(), reason="no /usr/bin/time on this host")
def test_a_step_reports_its_own_peak_rather_than_the_whole_lanes(tmp_path) -> None:
    """`getrusage` on RUSAGE_CHILDREN is the max over every child the runner ever reaped.

    Both Mac cells came back at exactly 1014.9 MB because of it. These steps are
    three `cat` invocations, so anything near that figure is the old number.
    """
    item = _replayed_mac_cell(tmp_path / "cache")

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert 0 < record["timing"]["peak_rss_mb"] < 100


def test_a_host_with_no_time_wrapper_says_why_the_peak_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(setup_matrix, "TIME_BINARY", tmp_path / "no-such-time")
    item = _replayed_mac_cell(tmp_path / "cache")

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["timing"]["peak_rss_mb"] is None
    assert "/usr/bin/time" in record["measurement_notes"]["peak_rss_mb"]


def test_the_cut_the_run_recorded_is_also_the_list_of_ids(tmp_path) -> None:
    """The overlap column is computed from the id list, so it has to be the cut."""
    cache = tmp_path / "cache"
    attempt = cache / "editorial-runs" / "mac-local" / "attempts" / "20260913T200634Z-72bcc8ac"
    attempt.mkdir(parents=True)
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "story": {"thesis": "a month of moving between the city and the woods"},
                "carriers": [
                    {"asset_id": "home-dog-walk-01", "taken": "2024-06-04T17:45:00+00:00"},
                    {"asset_id": "home-breakfast-01", "taken": "2024-06-01T08:15:00+00:00"},
                    {"asset_id": "home-football-lawn-01", "taken": "2024-06-02T16:00:00+00:00"},
                ],
            }
        )
    )
    item = _replayed_mac_cell(cache)

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["selected_asset_ids"] == [
        "home-breakfast-01",
        "home-football-lawn-01",
        "home-dog-walk-01",
    ]
    assert record["selected_asset_ids"] == [shot["asset_id"] for shot in record["cut"]["selected"]]


def test_a_nas_lane_finishes_one_cell_before_it_starts_the_next(monkeypatch, tmp_path) -> None:
    items = [_cell_plan(name, "nas") for name in ("nas-a", "nas-b", "nas-c")]
    plan = _plan_of(*items)
    events: list[str] = []

    def fake_remote_cell(item: CellPlan, _plan: Plan, _out_dir: Path) -> dict:
        events.append(f"start {item.cell.id}")
        # A parallel lane would interleave another start inside this window.
        time.sleep(0.02)
        events.append(f"end {item.cell.id}")
        return {"id": item.cell.id}

    # WHY: replaces the docker/kubectl subprocess that runs a cell on a real host.
    monkeypatch.setattr(setup_matrix, "run_remote_cell", fake_remote_cell)

    records = setup_matrix.run_lane("nas", items, plan, tmp_path)

    assert events == [
        "start nas-a",
        "end nas-a",
        "start nas-b",
        "end nas-b",
        "start nas-c",
        "end nas-c",
    ]
    assert [row["id"] for row in records] == ["nas-a", "nas-b", "nas-c"]


def test_a_piped_step_carries_a_tar_stream_from_one_command_to_the_other(tmp_path) -> None:
    """The NAS moves its files this way because its ssh server has no SFTP subsystem."""
    source = tmp_path / "cell"
    source.mkdir()
    (source / "config.yaml").write_text("pinned: true\n")
    destination = tmp_path / "remote"
    destination.mkdir()
    item = _cell_plan(
        "nas-pipe",
        "nas",
        steps=(
            Step(
                "push-config",
                ("tar", "-C", str(source), "-cf", "-", "config.yaml"),
                pipe_to=("tar", "-C", str(destination), "-xf", "-"),
            ),
        ),
    )
    plan = _plan_of(item)

    record = setup_matrix.run_remote_cell(item, plan, tmp_path / "out")

    assert record["error"] is None
    assert (destination / "config.yaml").read_text() == "pinned: true\n"


def test_a_nas_cell_records_what_its_container_was_actually_pinned_to(tmp_path) -> None:
    """The published table has no column for it, so the cell's own record is where it lives."""
    item = _cell_plan("nas-limits", "nas", container_limits="--cpuset-cpus 0-3 --memory 4g")
    plan = Plan(
        library="demo",
        month="2024-06",
        image="image:tag",
        anonymize_required=False,
        cells=(item,),
    )

    record = setup_matrix.run_remote_cell(item, plan, tmp_path / "out")

    assert record["container_limits"] == "--cpuset-cpus 0-3 --memory 4g"


def _banked_cell(out_dir: Path, cell_id: str, selected: list[str]) -> None:
    cell_dir = out_dir / cell_id
    cell_dir.mkdir(parents=True)
    (cell_dir / "timing.json").write_text(
        json.dumps(
            {
                "id": cell_id,
                "lane": cell_id.split("-")[0],
                "reader": "rules",
                "facts": "local",
                "tier": "no_captions",
                "why": "",
                "hosted": False,
                "skip_reason": None,
                "timing": {"total_s": 12.0},
                "selected_asset_ids": selected,
            }
        )
    )


def test_the_summary_is_rebuilt_from_every_lane_that_has_run(monkeypatch, tmp_path) -> None:
    """Lanes are separate invocations into one output directory, so the table is on disk."""
    out_dir = tmp_path / "out"
    _banked_cell(out_dir, "mac-local", ["a", "b", "c"])
    _banked_cell(out_dir, "nas-rules-local", ["a", "b"])
    _banked_cell(out_dir, "k8s-rules-local", ["z"])
    # WHY: the runner merges os.environ under its env files, and an operator's
    # own matrix variables would change which cells the plan reports as skipped.
    for name in ("MATRIX_NAS_SSH", "MATRIX_K8S_CONTEXT", "MATRIX_OMLX_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    exit_code = setup_matrix.main(
        [
            "--summarize-only",
            "--env-file",
            str(tmp_path / "no-such.env"),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    summary = json.loads((out_dir / "summary.data.json").read_text())
    rows = {row["id"]: row for row in summary["cells"]}
    assert [row["id"] for row in summary["cells"]][:2] == ["mac-local", "nas-rules-local"]
    assert rows["nas-rules-local"]["overlap_vs_cell_1"] == 0.667
    assert rows["k8s-rules-local"]["overlap_vs_cell_1"] == 0.0
    assert "k8s-rules-local" in (out_dir / "summary.md").read_text()
