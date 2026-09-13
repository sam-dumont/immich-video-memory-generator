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

import setup_matrix  # noqa: E402
from setup_matrix_plan import Cell, CellPlan, Plan, Step  # noqa: E402


def _cell_plan(cell_id: str, lane: str, steps: tuple[Step, ...] = ()) -> CellPlan:
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
        cache_dir="",
    )


def test_a_nas_lane_finishes_one_cell_before_it_starts_the_next(monkeypatch, tmp_path) -> None:
    items = [_cell_plan(name, "nas") for name in ("nas-a", "nas-b", "nas-c")]
    plan = Plan(
        library="demo",
        month="2024-06",
        image="image:tag",
        anonymize_required=False,
        cells=tuple(items),
    )
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
    plan = Plan(
        library="demo",
        month="2024-06",
        image="image:tag",
        anonymize_required=False,
        cells=(item,),
    )

    record = setup_matrix.run_remote_cell(item, plan, tmp_path / "out")

    assert record["error"] is None
    assert (destination / "config.yaml").read_text() == "pinned: true\n"


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
