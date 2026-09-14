"""How the runner executes a plan: one cell at a time per host, pipes included.

The matrix exists to measure what each setup costs. Two cells sharing a host
share its cores, so every number the run publishes would be wrong. The remote
lanes also move their files with tar over ssh, which is two commands and a pipe
rather than one argv, and that has to survive a binary payload.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402
import setup_matrix  # noqa: E402
import yaml  # noqa: E402
from setup_matrix_plan import (  # noqa: E402
    FROM_OPERATOR_CONFIG,
    HOMEBASE_PINS,
    IMMICH_KEY_ENV,
    Cell,
    CellPlan,
    Plan,
    Step,
)

# Excerpts of the first real Mac lane run, copied out of its own logs.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "setup_matrix"


@pytest.fixture(autouse=True)
def _no_copy_out_pause(monkeypatch) -> None:
    """The pause between copy-out attempts is for a cluster, not for a test to sit out."""
    monkeypatch.setattr(setup_matrix, "COPY_OUT_PAUSE_S", 0)


def _cell_plan(
    cell_id: str,
    lane: str,
    steps: tuple[Step, ...] = (),
    cache_dir: str = "",
    container_limits: str = "",
    manifests: dict[str, str] | None = None,
    operator_immich: bool = False,
    fresh_cache: bool = False,
    pins: dict | None = None,
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
        pins=pins or {},
        config_yaml="",
        steps=steps,
        manifests=manifests or {},
        app_credentials=(),
        cache_dir=cache_dir,
        operator_immich=operator_immich,
        fresh_cache=fresh_cache,
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


def test_a_record_says_the_cell_was_handed_a_home_and_never_says_which_one(tmp_path) -> None:
    """The marker the table reads to claim the three lanes measured the same places.

    Run 1's eight cluster cells planned against Null Island and not one field in
    any record said so. It stays a word: coordinates are the operator's home and
    belong in no file that gets published.
    """
    cache = tmp_path / "cache"
    replayed = _replayed_mac_cell(cache)
    pinned = _cell_plan(
        "mac-local",
        "mac",
        steps=replayed.steps,
        cache_dir=str(cache),
        pins=dict.fromkeys(HOMEBASE_PINS, "$MATRIX_HOMEBASE_LATITUDE"),
    )

    with_home = setup_matrix.run_local_cell(pinned, _plan_of(pinned), tmp_path / "out")
    without = setup_matrix.run_local_cell(replayed, _plan_of(replayed), tmp_path / "out")

    assert with_home["homebase"] == "pinned"
    assert without["homebase"] == "unset"


def test_a_cell_that_emptied_its_bank_is_cold_over_a_cache_that_was_there(tmp_path) -> None:
    """`--fresh-cache` makes the cold number a first derivation, and the record says so.

    Both hosted Melious cells made 0 completions on the last real run: a bank
    left over from an earlier one answered every question, and `selection 2s`
    published as this run's cost.
    """
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    item = _replayed_mac_cell(cache)
    fresh = _cell_plan("mac-local", "mac", steps=item.steps, cache_dir=str(cache), fresh_cache=True)

    record = setup_matrix.run_local_cell(fresh, _plan_of(fresh), tmp_path / "out")
    warm = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["fresh_cache"] is True
    assert record["prepare_cache_primed"] is False
    assert warm["fresh_cache"] is False
    assert warm["prepare_cache_primed"] is True


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


def test_a_cluster_cell_that_lost_its_copy_out_still_reports_what_it_cut(tmp_path) -> None:
    """The container tees its phases into the volume; `kubectl logs` came back anyway.

    `k8s-rules-service` published an empty row with `selection 0s, 14 planned from
    130 candidates` and `generation 5m 13s` in the log beside it, because the only
    copy of the block the capture read was the one the copy-out never brought back.
    """
    item = _cell_plan(
        "k8s-rules-service",
        "k8s",
        steps=(
            Step("logs", ("cat", str(FIXTURES / "k8s-job-logs.stdout.txt"))),
            Step("copy-out", ("true",)),
        ),
    )

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert record["timing"]["total_s"] == 313
    assert record["timing"]["selection_s"] == 0
    assert record["timing"]["render_s"] == 313
    assert (record["planned"], record["eligible"]) == (14, 130)
    # The film was named and never arrived, so nothing measured it.
    assert record["video"] == {}
    assert "copy-out" in record["measurement_notes"]["film"]


def test_a_cluster_cell_that_lost_its_copy_out_still_reports_what_it_prepared(tmp_path) -> None:
    """Both prepare phases are in the same stdout as the end-of-run block."""
    item = _cell_plan(
        "k8s-rules-service",
        "k8s",
        steps=(
            Step("logs", ("cat", str(FIXTURES / "k8s-job-logs.stdout.txt"))),
            Step("copy-out", ("true",)),
        ),
    )

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert record["timing"]["prepare_cold_s"] == 0.0
    assert record["timing"]["prepare_warm_s"] == 0.0
    assert record["prepared"]["pictures"] == 133
    assert record["prepared"]["seconds_per_picture"] == 0.0002
    # The cold phase's table only, not the warm one's rows behind it.
    assert [row["producer"] for row in record["prepared"]["producers"]] == ["previews"]


def _copy_out_that_works_on(attempt: int, tmp_path: Path, film: Path) -> Step:
    """A `kubectl cp` stand-in that only brings the film back on the nth try."""
    counter = tmp_path / "copy-out.count"
    script = tmp_path / "copy-out.sh"
    script.write_text(
        f"n=$(( $(cat {counter} 2>/dev/null || echo 0) + 1 ))\n"
        f"echo $n > {counter}\n"
        f'if [ "$n" -ge {attempt} ]; then mkdir -p {film.parent}; : > {film}; fi\n'
    )
    return Step("copy-out", ("sh", str(script)))


# What the fixture's run named, under the cell directory the copy-out fills.
_FILM = Path("k8s-rules-service_93aa9ed7_20260914_010338_1500/k8s-rules-service_93aa9ed7.mp4")


def _copying_cell(tmp_path: Path, *, works_on: int) -> tuple[CellPlan, Path]:
    film = tmp_path / "out" / "k8s-rules-service" / _FILM
    item = _cell_plan(
        "k8s-rules-service",
        "k8s",
        steps=(
            Step("logs", ("cat", str(FIXTURES / "k8s-job-logs.stdout.txt"))),
            _copy_out_that_works_on(works_on, tmp_path, film),
        ),
    )
    return item, film


def test_a_copy_out_that_ended_early_is_tried_again(tmp_path) -> None:
    """`k8s-rules-service` lost its film to one `error: unexpected EOF` and nothing else.

    A zero exit is not proof the tar stream finished, so what the copy is judged
    on is the file the run named.
    """
    item, film = _copying_cell(tmp_path, works_on=2)

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert (tmp_path / "copy-out.count").read_text().strip() == "2"
    assert film.is_file()
    assert record["measurement_notes"].get("film") is None


def _copy_out_that_truncates(tmp_path: Path, film: Path) -> Step:
    """A tar that brings the film back and still exits 1 on a member it could not finish."""
    counter = tmp_path / "copy-out.count"
    script = tmp_path / "copy-out-truncated.sh"
    script.write_text(
        f"n=$(( $(cat {counter} 2>/dev/null || echo 0) + 1 ))\n"
        f"echo $n > {counter}\n"
        f"mkdir -p {film.parent}; : > {film}\n"
        'echo "./a-take/mastered_calm_acoustic_s522.wav: Truncated tar archive:'
        ' Unknown error: -1" >&2\n'
        'echo "tar: Error exit delayed from previous errors." >&2\n'
        "exit 1\n"
    )
    return Step("copy-out", ("sh", str(script)))


def test_a_truncated_copy_out_is_tried_again_and_does_not_lose_the_cell(tmp_path) -> None:
    """`k8s-gpu-t1000` published `error: copy-out exited 1` over a film that had arrived.

    One member of the archive ended early, tar exited 1 for it, and the cell was
    marked failed with its 54.5 s film sitting in the directory. The copy is tried
    again, and what it is judged on is the film: a member tar could not finish is
    a note, not a lost row.
    """
    film = tmp_path / "out" / "k8s-rules-service" / _FILM
    item = _cell_plan(
        "k8s-rules-service",
        "k8s",
        steps=(
            Step("logs", ("cat", str(FIXTURES / "k8s-job-logs.stdout.txt"))),
            _copy_out_that_truncates(tmp_path, film),
            Step("delete-output-claim", ("true",)),
        ),
    )

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert (tmp_path / "copy-out.count").read_text().strip() == "2"
    assert record["error"] is None
    assert "Truncated tar archive" in record["measurement_notes"]["copy_out"]
    # The tear-down after a tolerated copy still runs, so no claim is left held.
    assert (tmp_path / "out" / "k8s-rules-service" / "delete-output-claim.stdout.log").is_file()


def test_a_copy_out_that_never_brings_the_film_back_says_so(tmp_path) -> None:
    item, film = _copying_cell(tmp_path, works_on=99)

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert (tmp_path / "copy-out.count").read_text().strip() == "3"
    assert not film.is_file()
    assert "3 attempts" in record["measurement_notes"]["film"]


def test_a_cluster_cell_reads_the_attempt_its_container_copied_out(tmp_path) -> None:
    """The cache never comes back, so the attempt inside it is copied into the output volume.

    Both k8s cells that finished came back with `selected_asset_ids` empty and
    `#kept 0` beside a film that plainly had pictures in it, because the only
    copy of the plan was the one on the per-cell editorial cache.
    """
    out_dir = tmp_path / "out"
    attempt = (
        out_dir
        / "k8s-rules-local"
        / "attempts"
        / "k8s-rules-local"
        / "attempts"
        / "20260914T010338Z-1500ab"
    )
    attempt.mkdir(parents=True)
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "story": {"thesis": "a month of moving between the city and the woods"},
                "carriers": [
                    {"asset_id": "home-dog-walk-01", "taken": "2024-06-04T17:45:00+00:00"},
                    {"asset_id": "home-breakfast-01", "taken": "2024-06-01T08:15:00+00:00"},
                ],
            }
        )
    )
    item = _cell_plan("k8s-rules-local", "k8s", steps=(Step("logs", ("true",)),))

    record = setup_matrix.run_remote_cell(item, _plan_of(item), out_dir)

    assert record["selected_asset_ids"] == ["home-breakfast-01", "home-dog-walk-01"]
    assert record["cut"]["selected"][0]["asset_id"] == "home-breakfast-01"


def test_a_remote_cell_reports_whether_its_cache_already_held_a_run(tmp_path) -> None:
    """`prep cold 0s` over a warm bank published as a cold preparation and said nothing."""
    out_dir = tmp_path / "out"
    cluster = out_dir / "k8s-rules-local"
    cluster.mkdir(parents=True)
    (cluster / "cache-primed.txt").write_text("primed\n")
    on_cluster = _cell_plan("k8s-rules-local", "k8s", steps=())
    on_nas = _cell_plan(
        "nas-rules-local", "nas", steps=(Step("make-remote-dir", ("echo", "cold")),)
    )

    cluster_record = setup_matrix.run_remote_cell(on_cluster, _plan_of(on_cluster), out_dir)
    nas_record = setup_matrix.run_remote_cell(on_nas, _plan_of(on_nas), out_dir)

    assert cluster_record["prepare_cache_primed"] is True
    assert nas_record["prepare_cache_primed"] is False


def test_a_remote_cell_that_printed_no_summary_invents_none(tmp_path) -> None:
    """The NAS cell that died in its detectors has no numbers, and none are filled in."""
    item = _cell_plan("nas-rules-local", "nas", steps=(Step("run", ("echo", "Error: no")),))

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert record["timing"]["total_s"] is None
    assert record["measurement_notes"].get("film") is None


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


# The cluster has two GPU nodes and they are not the same card. Which one a run
# used is not something the run may assume: the Job pins its own by node label,
# and the inference service takes whatever the scheduler gives it unless the run
# says otherwise.

_INFERENCE_NODE = "a-gpu-node"
_CARD = "NVIDIA-GeForce-GTX-1070-SHARED"


def _fake_kubectl(answers: dict[str, str]):
    # WHY: the cluster. Both calls are reads of the API server, and the whole
    # point of the function is that it asks the pod first and the node second.
    def run(command, **_kwargs):
        key = "node" if "node" in command else "pod"
        return subprocess.CompletedProcess(command, 0, answers.get(key, ""), "")

    return run


def test_the_run_records_which_card_answered_the_facts_requests(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_kubectl({"pod": _INFERENCE_NODE, "node": _CARD}))
    assert setup_matrix.read_inference_gpu_product(_plan_of()) == _CARD


def test_a_cluster_with_no_card_records_nothing_rather_than_guessing(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_kubectl({"pod": _INFERENCE_NODE, "node": ""}))
    assert setup_matrix.read_inference_gpu_product(_plan_of()) == ""


def test_the_dry_run_shows_the_card_the_service_was_pinned_to(tmp_path, capsys) -> None:
    """A CLI flag, so printing it leaks nothing: it is not a value out of the environment."""
    env_file = tmp_path / "matrix.env"
    env_file.write_text(
        "MATRIX_K8S_CONTEXT=a-context\n"
        "MATRIX_K8S_NAMESPACE=a-namespace\n"
        "MATRIX_FIXTURE_BASE_URL=http://a-fixture.invalid:8078\n"
        "MATRIX_HOMEBASE_LATITUDE=12.3456\n"
        "MATRIX_HOMEBASE_LONGITUDE=-7.8910\n"
    )
    arguments = [
        "--dry-run",
        "--cell",
        "k8s-rules-service",
        "--env-file",
        str(env_file),
        "--out",
        str(tmp_path / "out"),
    ]

    assert setup_matrix.main([*arguments, "--inference-node-product", _CARD]) == 0
    pinned = capsys.readouterr().out
    assert setup_matrix.main(arguments) == 0
    unpinned = capsys.readouterr().out

    assert f"nvidia.com/gpu.product={_CARD}" in pinned
    assert "inference-node" not in unpinned


def test_a_cell_records_what_drew_its_titles_and_what_encoded_its_film(tmp_path) -> None:
    """Read off the run's own log on every lane, never inferred from the lane.

    The first cluster Jobs drew titles on CUDA and encoded in software in the
    same run, so one answer for both would have been the wrong answer for one.
    """
    log = tmp_path / "generate.txt"
    log.write_text(
        "Title kernels: quadrants 1.3.0 on the CUDA backend\n"
        "No hardware acceleration detected, using software encoding\n"
    )
    item = _cell_plan(
        "k8s-gpu-t1000",
        "mac",
        cache_dir=str(tmp_path / "cache"),
        steps=(Step("generate", ("cat", str(log))),),
    )

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["title_backend"] == "CUDA"
    assert record["encoder"] == "software"


def test_a_cell_that_printed_neither_keeps_both_empty(tmp_path) -> None:
    item = _cell_plan(
        "mac-rules",
        "mac",
        cache_dir=str(tmp_path / "cache"),
        steps=(Step("generate", ("echo", "nothing about titles or encoders here")),),
    )

    record = setup_matrix.run_local_cell(item, _plan_of(item), tmp_path / "out")

    assert record["title_backend"] is None
    assert record["encoder"] is None


def test_a_cluster_cell_is_handed_the_operators_immich_at_the_last_moment(tmp_path) -> None:
    """The plan says where the value comes from; the runner puts it in the argv.

    Run 2's four k8s cells all died on "Immich not configured": the ConfigMap is
    built from the pins alone and a real library pins no Immich. The key goes to
    the Secret under the name the loader maps, the URL to the ConfigMap, and
    neither is in the plan anyone prints.
    """
    item = _cell_plan(
        "k8s-rules-local",
        "k8s",
        steps=(
            Step(
                "make-secret", ("echo", f"--from-literal={IMMICH_KEY_ENV}={FROM_OPERATOR_CONFIG}")
            ),
        ),
        manifests={"configmap.yaml": f"data:\n  url: {FROM_OPERATOR_CONFIG}\n"},
        operator_immich=True,
    )
    plan = _plan_of(item)
    plan.operator_credentials.update(
        {"url": "http://immich.invalid:2283", "api_key": "operator-key"}
    )
    source = tmp_path / "operator.yaml"
    source.write_text(yaml.safe_dump({"immich": {"url": "http://immich.invalid:2283"}}))
    out_dir = tmp_path / "out"

    setup_matrix._write_cell_config(item, plan, out_dir, source)
    setup_matrix.run_remote_cell(item, plan, out_dir)

    written = (out_dir / "k8s-rules-local" / "configmap.yaml").read_text()
    created = (out_dir / "k8s-rules-local" / "make-secret.stdout.log").read_text()
    assert written == "data:\n  url: http://immich.invalid:2283\n"
    assert "operator-key" not in written
    assert f"--from-literal={IMMICH_KEY_ENV}=operator-key" in created


def test_the_dry_run_says_which_captioner_the_full_tier_cells_will_get(tmp_path, capsys) -> None:
    """The device is half of what a full-tier row costs, so the plan has to name it.

    Pinned, the transcript applies that overlay by name. On `auto` it prints the
    rule instead of an answer, because resolving it means asking a cluster and a
    dry run asks nothing.
    """
    env_file = tmp_path / "matrix.env"
    env_file.write_text(
        "MATRIX_K8S_CONTEXT=a-context\n"
        "MATRIX_K8S_NAMESPACE=a-namespace\n"
        "MATRIX_FIXTURE_BASE_URL=http://a-fixture.invalid:8078\n"
        "MATRIX_HOMEBASE_LATITUDE=12.3456\n"
        "MATRIX_HOMEBASE_LONGITUDE=-7.8910\n"
    )
    arguments = [
        "--dry-run",
        "--cell",
        "k8s-full-rules",
        "--env-file",
        str(env_file),
        "--out",
        str(tmp_path / "out"),
    ]

    assert setup_matrix.main([*arguments, "--inference-device", "cuda"]) == 0
    pinned = capsys.readouterr().out
    assert setup_matrix.main(arguments) == 0
    undecided = capsys.readouterr().out

    assert "apply-captioner-cuda" in pinned
    assert "overlays/captioner-cuda" in pinned
    assert "captioner-device" in undecided, "an auto run has to say what decides it"
    assert "probe-gpu" in undecided


# A cell whose attempt never came back is not a cell that cut nothing. The film
# is here and the run named every picture it fetched to make it, so the row can
# say how many it kept and which ones -- what it cannot say is the order they
# played in, because a download order is not a play order.


def test_a_cell_that_lost_its_attempt_recovers_its_cut_from_the_log(tmp_path) -> None:
    """`k8s-gpu-t1000` published `selected_asset_ids: []` beside a 54.5 s film."""
    item = _cell_plan(
        "k8s-rules-service",
        "k8s",
        steps=(
            Step("logs", ("cat", str(FIXTURES / "k8s-job-logs.stdout.txt"))),
            Step("copy-out", ("true",)),
        ),
    )

    record = setup_matrix.run_remote_cell(item, _plan_of(item), tmp_path / "out")

    assert len(record["selected_asset_ids"]) == 14
    assert "home-breakfast-02" in record["selected_asset_ids"]
    assert record["cut_source"] == setup_matrix.CUT_FROM_LOG
    # The film counted 14 clips too, so the recovered set is the whole cut.
    assert "14" in record["measurement_notes"]["cut"]


def test_a_recovered_cut_never_claims_an_order_it_could_not_read(tmp_path) -> None:
    """The videos are fetched first and concurrently, so a fetch order is not a cut order.

    Chronological order is a hard rule, and a table that reported it broken off a
    download order would be raising a false alarm about the loudest finding it has.
    """
    from setup_matrix_summary import build_summary

    summary = build_summary(
        library="demo",
        month="2024-06",
        image="ghcr.io/example/app:0.1.0",
        rows=[
            {
                "id": "mac-local",
                "tier": "full",
                "reader": "model",
                "facts": "local",
                "hosted": False,
                "skip_reason": None,
                "timing": {},
                "selected_asset_ids": ["a", "b", "c"],
            },
            {
                "id": "k8s-gpu-t1000",
                "tier": "no_captions",
                "reader": "rules",
                "facts": "service",
                "hosted": False,
                "skip_reason": None,
                "timing": {},
                "cut_source": setup_matrix.CUT_FROM_LOG,
                "selected_asset_ids": ["c", "b", "a"],
            },
        ],
    )
    recovered = next(row for row in summary["cells"] if row["id"] == "k8s-gpu-t1000")

    assert recovered["order_kept"] is None
    assert recovered["overlap_vs_cell_1"] == 1.0


def test_no_row_is_ordered_against_a_reference_cut_that_was_itself_recovered(tmp_path) -> None:
    """Every other row's order is read against `mac-local`, so a fetch order there poisons all of them."""
    from setup_matrix_summary import build_summary

    def row(cell_id: str, selected: list[str], **extra: object) -> dict:
        return {
            "id": cell_id,
            "tier": "full",
            "reader": "rules",
            "facts": "local",
            "hosted": False,
            "skip_reason": None,
            "timing": {},
            "selected_asset_ids": selected,
            **extra,
        }

    summary = build_summary(
        library="demo",
        month="2024-06",
        image="ghcr.io/example/app:0.1.0",
        rows=[
            row("mac-local", ["a", "b", "c"], cut_source=setup_matrix.CUT_FROM_LOG),
            row("nas-rules-local", ["c", "b", "a"]),
        ],
    )

    assert next(r for r in summary["cells"] if r["id"] == "nas-rules-local")["order_kept"] is None


def test_a_recapture_rebuilds_a_record_from_the_directory_that_is_already_there(
    tmp_path,
) -> None:
    """Reading a cell again must not need the cell run again: the cluster ones cost hours."""
    out_dir = tmp_path / "out"
    cell_dir = out_dir / "k8s-rules-service"
    cell_dir.mkdir(parents=True)
    (cell_dir / "logs.stdout.log").write_text((FIXTURES / "k8s-job-logs.stdout.txt").read_text())
    item = _cell_plan("k8s-rules-service", "k8s")

    record = setup_matrix.recapture_cell(item, _plan_of(item), out_dir)

    assert record["timing"]["total_s"] == 313
    assert (record["planned"], record["eligible"]) == (14, 130)
    assert len(record["selected_asset_ids"]) == 14
    assert record["error"] is None


def test_a_recapture_keeps_what_only_the_running_process_could_measure(tmp_path) -> None:
    """Peak memory and CPU on the Mac lane are counted live; no file holds them."""
    out_dir = tmp_path / "out"
    cell_dir = out_dir / "k8s-rules-service"
    cell_dir.mkdir(parents=True)
    (cell_dir / "logs.stdout.log").write_text((FIXTURES / "k8s-job-logs.stdout.txt").read_text())
    (cell_dir / "timing.json").write_text(
        json.dumps({"id": "k8s-rules-service", "timing": {"peak_rss_mb": 1974.0, "cpu_s": 2643.7}})
    )
    item = _cell_plan("k8s-rules-service", "k8s")

    record = setup_matrix.recapture_cell(item, _plan_of(item), out_dir)

    assert record["timing"]["peak_rss_mb"] == 1974.0
    assert record["timing"]["cpu_s"] == 2643.7


def test_a_recapture_reports_the_home_the_run_had_and_not_the_one_the_plan_has(
    tmp_path,
) -> None:
    """The plan is today's and the directory is the run's.

    `k8s-gpu-t1000` was cut before anything pinned a home. A recapture that
    answered off today's manifest would put "pinned" on a row made at Null
    Island, which is the exact claim the marker exists to make honestly.
    """
    out_dir = tmp_path / "out"
    cell_dir = out_dir / "k8s-rules-service"
    cell_dir.mkdir(parents=True)
    (cell_dir / "logs.stdout.log").write_text((FIXTURES / "k8s-job-logs.stdout.txt").read_text())
    (cell_dir / "timing.json").write_text(json.dumps({"id": "k8s-rules-service", "timing": {}}))
    item = _cell_plan(
        "k8s-rules-service", "k8s", pins=dict.fromkeys(HOMEBASE_PINS, "$MATRIX_HOMEBASE_LATITUDE")
    )

    record = setup_matrix.recapture_cell(item, _plan_of(item), out_dir)

    assert record["homebase"] == "unknown"
