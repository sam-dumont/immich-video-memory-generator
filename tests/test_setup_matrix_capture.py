"""The parsers read the run's own output, not something that looks like it.

Every input below is built by calling the renderers the CLI actually prints with
(`render_run_summary`, `saved_path_line`), so a change to either format fails
here instead of quietly producing a plausible wrong number in a published table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_capture import (  # noqa: E402
    BYTES_PER_PROMPT_TOKEN,
    HostedUsage,
    anonymize,
    apply_exact_usage,
    clock_seconds,
    downloaded_asset_ids,
    film_clip_count,
    parse_cache_primed,
    parse_caption_origins,
    parse_cgroup_cpu_seconds,
    parse_cgroup_peak_rss_mb,
    parse_encoder,
    parse_models_fetch_seconds,
    parse_prepare_seconds,
    parse_prepared_pictures,
    parse_prepared_producers,
    parse_run_summary,
    parse_saved_path,
    parse_time_peak_rss_mb,
    parse_title_backend,
    prepare_phases,
    read_contract_health,
    read_images_sent,
    reconstruct_usage,
)

from immich_memories.analysis.llm_metrics import LLMCounters  # noqa: E402
from immich_memories.analysis.llm_usage_record import (  # noqa: E402
    USAGE_FILE,
    write_llm_usage,
)
from immich_memories.cli._generate_display import saved_path_line  # noqa: E402
from immich_memories.cli._run_summary import render_run_summary  # noqa: E402

# Excerpts of the first real Mac lane run, copied out of its own logs.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "setup_matrix"


def test_a_run_block_yields_the_numbers_it_printed() -> None:
    text = render_run_summary(
        total_seconds=187.0,
        analysis_seconds=142.0,
        generation_seconds=45.0,
        eligible=133,
        planned=18,
        counters=None,
        preparation_tier="no_captions",
    )
    summary = parse_run_summary(text)
    assert summary.total_s == 187
    assert summary.selection_s == 142
    assert summary.render_s == 45
    assert (summary.planned, summary.eligible) == (18, 133)
    assert summary.tier == "no_captions"


def test_a_run_with_no_model_reports_no_usage() -> None:
    """The recommended NAS setup prints no LLM line at all; that is not a parse failure."""
    text = render_run_summary(
        total_seconds=30.0,
        analysis_seconds=20.0,
        generation_seconds=10.0,
        eligible=10,
        planned=5,
        counters=None,
    )
    assert parse_run_summary(text).usage.calls is None


def test_a_hosted_run_reports_its_tokens_and_says_how_exact_they_are() -> None:
    text = render_run_summary(
        total_seconds=600.0,
        analysis_seconds=560.0,
        generation_seconds=40.0,
        eligible=200,
        planned=20,
        counters=LLMCounters(
            calls=42,
            cache_hits=7,
            prompt_tokens=125_400,
            completion_tokens=8_300,
            wall_seconds=310.0,
        ),
    )
    usage = parse_run_summary(text).usage
    assert usage.calls == 42
    assert usage.cache_hits == 7
    assert usage.tokens_in == 125_400
    assert usage.tokens_out == 8_300
    assert usage.counted_exactly is False
    assert usage.wall_seconds == 310
    # The capture never prices anything: the list price lives in the manifest and
    # the summary is what multiplies it by these counts.
    assert usage.est_cost is None


def test_small_token_counts_are_reported_exactly() -> None:
    text = render_run_summary(
        total_seconds=10.0,
        analysis_seconds=5.0,
        generation_seconds=5.0,
        eligible=3,
        planned=2,
        counters=LLMCounters(calls=2, prompt_tokens=880, completion_tokens=120),
    )
    usage = parse_run_summary(text).usage
    assert (usage.tokens_in, usage.tokens_out) == (880, 120)
    assert usage.counted_exactly is True


def test_the_runs_own_usage_record_replaces_the_rounded_line(tmp_path: Path) -> None:
    """`115.6k` is eight tokens away from the truth, and the table publishes money."""
    counters = LLMCounters(
        calls=93,
        cache_hits=1,
        prompt_tokens=115_592,
        completion_tokens=15_788,
        reasoning_tokens=13_469,
        wall_seconds=325.649,
    )
    text = render_run_summary(
        total_seconds=537.0,
        analysis_seconds=369.0,
        generation_seconds=168.0,
        eligible=130,
        planned=15,
        counters=counters,
    )
    usage = parse_run_summary(text).usage.as_dict()
    assert usage["tokens_in"] == 115_600  # what the line said, rounded
    write_llm_usage(tmp_path, counters)

    apply_exact_usage(usage, tmp_path)

    assert usage["tokens_in"] == 115_592
    assert usage["tokens_out"] == 15_788
    assert usage["reasoning_tokens"] == 13_469
    assert usage["calls"] == 93
    assert usage["cache_hits"] == 1
    assert usage["wall_seconds"] == 325.649
    assert usage["counted_exactly"] is True
    assert usage["usage_source"] == "record"


def test_a_cell_whose_run_left_no_record_keeps_what_the_line_said(tmp_path: Path) -> None:
    """Older runs and remote cells whose copy-out missed the file still report."""
    text = render_run_summary(
        total_seconds=600.0,
        analysis_seconds=560.0,
        generation_seconds=40.0,
        eligible=200,
        planned=20,
        counters=LLMCounters(calls=42, prompt_tokens=125_400, completion_tokens=8_300),
    )
    usage = parse_run_summary(text).usage.as_dict()
    assert not (tmp_path / USAGE_FILE).exists()

    apply_exact_usage(usage, tmp_path)

    assert usage["tokens_in"] == 125_400
    assert usage["counted_exactly"] is False
    assert usage["usage_source"] == "log"


def test_a_cell_that_never_asked_a_model_reports_no_source_at_all(tmp_path: Path) -> None:
    usage = HostedUsage().as_dict()

    apply_exact_usage(usage, tmp_path)

    assert usage["usage_source"] is None
    assert usage["calls"] is None


def _finished_attempt(
    attempt: Path, *, planner: dict, pre: list[tuple[str, int, int, int]]
) -> None:
    """An attempt directory shaped like one a run under the old counting left behind.

    `planner` is the `llm_metrics` block `plan_structure` wrote, `pre` is one
    tuple per pre-planner call: stage, request bytes, completion, reasoning.
    """
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "llm_metrics": planner,
                # A subset of the planner block, not an addition to it: the facts
                # are read inside the scope that wrote llm_metrics.
                "picture_facts_metrics": {
                    "inference_calls": 32,
                    "images_sent": 32,
                    "prompt_tokens": 29_284,
                    "completion_tokens": 3_084,
                },
            }
        )
    )
    calls = attempt / "pre-planner-calls"
    calls.mkdir(exist_ok=True)
    for index, (stage, request_bytes, completion, reasoning) in enumerate(pre):
        name = f"{stage}-{index:032x}"
        (calls / f"{name}.request.private.txt").write_text("x" * request_bytes)
        reply = (
            None
            if completion is None
            else {
                "finish_reason": "stop",
                "completion_tokens": completion,
                "reasoning_tokens": reasoning,
            }
        )
        (calls / f"{name}.outcome.private.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "status": "complete_transport",
                    "reply": reply,
                    "started_at": f"2026-09-14T15:{index:02d}:00+00:00",
                    "finished_at": f"2026-09-14T15:{index:02d}:10+00:00",
                }
            )
        )


_GLM_PLANNER = {
    "llm_calls": 90,
    "llm_cache_hits": 1,
    "llm_prompt_tokens": 115_592,
    "llm_cached_prompt_tokens": 11_904,
    "llm_completion_tokens": 15_788,
    "llm_truncated": 0,
    "llm_wall_seconds": 325.649,
}
# The three pre-planner calls of the glm demo cell: two episode reads and the
# period account, with the request sizes and reply tokens they recorded.
_GLM_PRE = [
    ("episodes", 17_428, 1_814, 279),
    ("episodes", 13_601, 1_371, 233),
    ("period", 4_624, 331, 1),
]


def test_a_run_from_before_the_usage_record_is_reconstructed_from_its_artifacts(
    tmp_path: Path,
) -> None:
    """Five expensive cells finished under the broken counting. Nobody is re-paying.

    The planner block held the 90 calls selection made and the pre-planner
    outcomes hold the 3 that preparation made, which is every one of the 93
    POSTs that cell's log recorded.
    """
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)

    usage = reconstruct_usage(attempt)

    assert usage is not None
    assert usage.calls == 93
    assert usage.cache_hits == 1
    assert usage.tokens_in == 115_592  # the planner's own, exactly as recorded
    assert usage.tokens_out == 15_788 + 1_814 + 1_371 + 331
    assert usage.reasoning_tokens == 279 + 233 + 1
    assert usage.usage_source == "reconstructed"
    assert usage.counted_exactly is False


def test_the_guessed_half_is_kept_out_of_the_counted_half(tmp_path: Path) -> None:
    """A pre-planner outcome records no prompt tokens, so that part is arithmetic.

    It goes in a field of its own. Folding an estimate into `tokens_in` would
    make a number the report prints with a footnote look like a measured one.
    """
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)

    usage = reconstruct_usage(attempt)

    assert usage is not None
    assert usage.tokens_in == 115_592
    assert usage.estimated_prompt_tokens == round(
        (17_428 + 13_601 + 4_624) / BYTES_PER_PROMPT_TOKEN
    )


def test_a_call_that_never_came_back_is_not_billed(tmp_path: Path) -> None:
    """The deepseek cell raised on one episode read; its log shows 105 POSTs, not 106."""
    attempt = tmp_path / "attempt"
    _finished_attempt(
        attempt,
        planner=_GLM_PLANNER | {"llm_calls": 97},
        pre=[*_GLM_PRE, ("episodes", 13_601, None, None)],
    )

    usage = reconstruct_usage(attempt)

    assert usage is not None
    assert usage.calls == 100  # 97 planned + the 3 that answered, not the 4th


def test_picture_facts_are_not_added_to_the_planner_block(tmp_path: Path) -> None:
    """They are inside it. Adding them bills the glm demo cell for 125 calls, not 93.

    `PictureFactsProvider` reads inside the scope `plan_structure` opens, so
    `picture_facts_metrics` is a view of part of `llm_metrics`, not a second
    bill. `images_sent` is the one number it adds, and `read_images_sent`
    already carries that.
    """
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)

    usage = reconstruct_usage(attempt)

    assert usage is not None
    assert usage.calls == 93
    assert usage.tokens_in == 115_592


def test_a_run_that_left_its_own_record_is_never_reconstructed(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)
    write_llm_usage(attempt, LLMCounters(calls=93, prompt_tokens=1))

    assert reconstruct_usage(attempt) is None


def test_the_reconstruction_is_preferred_to_the_rounded_line(tmp_path: Path) -> None:
    """Precedence: the run's own record, then the reconstruction, then the log."""
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)
    text = render_run_summary(
        total_seconds=537.0,
        analysis_seconds=369.0,
        generation_seconds=168.0,
        eligible=130,
        planned=15,
        counters=LLMCounters(calls=42, prompt_tokens=125_400, completion_tokens=8_300),
    )
    usage = parse_run_summary(text).usage.as_dict()

    apply_exact_usage(usage, attempt)

    assert usage["calls"] == 93
    assert usage["usage_source"] == "reconstructed"
    assert usage["counted_exactly"] is False


def test_the_clock_covers_the_preparation_reads_as_well(tmp_path: Path) -> None:
    """The planner block times the planner. The episode reads took 40 s on top of it.

    Leaving them out reports the glm demo cell at 5m 26s against the 6m 08s its
    own summary printed for selection.
    """
    attempt = tmp_path / "attempt"
    _finished_attempt(attempt, planner=_GLM_PLANNER, pre=_GLM_PRE)

    usage = reconstruct_usage(attempt)

    assert usage is not None
    assert usage.wall_seconds == pytest.approx(325.649 + 30.0)


def test_a_rules_cell_reconstructs_nothing_rather_than_a_bill_of_zero(tmp_path: Path) -> None:
    """`provider_metrics` persists measured zeroes, and a rules cell has all of them.

    "Never asked a model" has to keep reading differently from "asked and it was
    free", or every rules row joins the cost table with a price of nothing.
    """
    attempt = tmp_path / "attempt"
    _finished_attempt(
        attempt,
        planner={"llm_calls": 0, "llm_cache_hits": 0, "llm_prompt_tokens": 0},
        pre=[],
    )

    assert reconstruct_usage(attempt) is None


def test_an_attempt_with_no_plan_at_all_reconstructs_nothing(tmp_path: Path) -> None:
    assert reconstruct_usage(tmp_path) is None


def test_the_saved_line_is_the_only_place_a_run_names_its_file(tmp_path: Path) -> None:
    """`generate` has no --json, so this line is the whole contract."""
    target = tmp_path / "mac-local.mp4"
    assert parse_saved_path(saved_path_line(target)) == str(target)


def test_a_long_path_wraps_under_the_label_and_still_parses() -> None:
    long_path = Path("/tmp") / ("a" * 90) / "cell.mp4"  # noqa: S108
    line = saved_path_line(long_path)
    assert "\n" in line, "this case only exists because the renderer wraps"
    assert parse_saved_path(line) == str(long_path)


def test_a_home_relative_path_is_expanded_back(monkeypatch) -> None:
    """`display_path` prints ~ for the home directory; a file operation needs the real path."""
    target = Path.home() / "Movies" / "cell.mp4"
    line = saved_path_line(target)
    assert line.count("~") == 1
    assert parse_saved_path(line) == str(target)


def test_a_quiet_run_names_its_film_under_a_log_prefix() -> None:
    """What the matrix actually runs: `generate --quiet`, so the label goes through logging.

    The formatter stamps the first line and leaves the wrapped path on the next
    one, which is why the label cannot be anchored to the start of a line.
    """
    text = (FIXTURES / "generate-saved.stdout.txt").read_text()
    assert parse_saved_path(text) == (
        "output/setup-matrix/demo/run1/mac-local/"
        "mac-local_af64ef21_20260913_221211_52d6/mac-local_af64ef21.mp4"
    )


def test_clock_reads_both_shapes_the_summary_prints() -> None:
    assert clock_seconds("42s") == 42
    assert clock_seconds("3m 07s") == 187
    assert clock_seconds("not a clock") is None


def test_cgroup_v2_and_v1_counters_both_read() -> None:
    assert parse_cgroup_peak_rss_mb("2147483648\n") == 2048.0
    assert parse_cgroup_cpu_seconds("usage_usec 12500000\nuser_usec 9\n") == 12.5
    assert parse_cgroup_cpu_seconds("12500000000\n") == 12.5


def test_a_container_that_answered_nothing_leaves_the_field_unmeasured() -> None:
    assert parse_cgroup_peak_rss_mb("") is None
    assert parse_cgroup_cpu_seconds("") is None


def test_both_flavours_of_usr_bin_time_report_the_peak_of_one_step() -> None:
    """BSD counts bytes, GNU counts kilobytes, and the matrix has a lane on each.

    This is the only per-step peak a local cell can get: getrusage on
    RUSAGE_CHILDREN is the maximum over every child the runner ever reaped, so
    it hands every cell of a lane the same number.
    """
    bsd = (
        "        0.00 real         0.00 user         0.00 sys\n"
        "             1310720  maximum resident set size\n"
        "                   0  average shared memory size\n"
    )
    gnu = (
        '\tCommand being timed: "immich-memories prepare"\n'
        "\tMaximum resident set size (kbytes): 1039872\n"
        "\tExit status: 0\n"
    )
    assert parse_time_peak_rss_mb(bsd) == 1.2
    assert parse_time_peak_rss_mb(gnu) == 1015.5
    assert parse_time_peak_rss_mb("no such wrapper on this host") is None


def test_anonymising_keeps_overlap_computable_and_drops_the_words() -> None:
    """February is published as aggregates, so ids become handles and reasons go."""
    record = {
        "cells": [
            {
                "id": "mac-local",
                "selected_asset_ids": ["real-asset-1", "real-asset-2"],
                "cut": {
                    "thesis": "the weekend they went to the coast",
                    "selected": [{"asset_id": "real-asset-1", "reason": "her face, lit"}],
                },
            }
        ]
    }
    stripped = anonymize(record)
    cell = stripped["cells"][0]
    assert cell["selected_asset_ids"] == ["asset-001", "asset-002"]
    assert cell["cut"]["selected"][0]["asset_id"] == "asset-001"
    assert cell["cut"]["selected"][0]["reason"] is None
    assert cell["cut"]["thesis"] is None
    assert "real-asset-1" not in str(stripped)


def test_prepare_seconds_come_from_the_table_prepare_actually_prints() -> None:
    """`prepare` has no --json; the total row of its rate table is the whole contract."""
    from immich_memories.analysis.preparation_report import ProducerCost, rate_report

    for elapsed, expected in ((64.0, 64.0), (720.0, 720.0), (3840.0, 3840.0)):
        costs = [ProducerCost(producer="heads", pending=133, seconds=elapsed)]
        text = "\n".join(rate_report(costs, pictures=133, library_size=1000))
        assert parse_prepare_seconds(text) == expected

    banked = "1,337 pictures prepared at 0.4812 s/picture."
    assert parse_prepared_pictures(banked) == (1337, 0.4812)
    assert parse_prepared_pictures("nothing of the sort") == (None, None)


def test_a_real_cold_prepare_yields_its_count_and_every_producer_row() -> None:
    """`print_success` puts a tick in front of the count, and the table says who paid.

    The rate table is the only place a run says which producer the preparation
    seconds went to, and on the first Mac run that was captions for all of it.
    """
    text = (FIXTURES / "prepare-cold.stdout.txt").read_text()
    assert parse_prepared_pictures(text) == (133, 0.1153)
    assert parse_prepared_producers(text) == [
        {
            "producer": "previews",
            "pending": 133,
            "seconds_per_picture": 0.0001,
            "share_pct": 0.0,
            "seconds": 0.0,
        },
        {
            "producer": "captions",
            "pending": 133,
            "seconds_per_picture": 0.1152,
            "share_pct": 100.0,
            "seconds": 15.0,
        },
    ]


def test_a_cell_whose_facts_came_off_the_service_still_reports_its_rate_table() -> None:
    """The table gains a `service s/pic` column, and `elapsed` stops being the last one.

    `k8s-gpu-t1000` published `prepare_cold_s: null` and no producers beside a
    table that named every one of them: the patterns ended at `elapsed`, and the
    column `rate_report` adds the moment another machine charges itself for a
    producer pushed the end of the line past them.
    """
    text = (FIXTURES / "prepare-cold-service.stdout.txt").read_text()

    assert parse_prepare_seconds(text) == 68.0
    assert parse_prepared_pictures(text) == (133, 0.5096)
    assert [(row["producer"], row["seconds"]) for row in parse_prepared_producers(text)] == [
        ("previews", 5.0),
        ("pixels", 4.0),
        ("remote_facts", 60.0),
    ]


def test_the_two_prepares_of_one_remote_stdout_are_read_apart() -> None:
    """A container tees each phase into a file AND prints both into one stream.

    That stream is all there is when the copy-out loses the files, and the rate
    table is the same shape in both halves, so reading the pair as one returns
    the cell's producers twice over.
    """
    text = (FIXTURES / "k8s-job-logs.stdout.txt").read_text()

    phases = prepare_phases(text)

    assert len(phases) == 2
    assert parse_prepared_pictures(phases[0]) == (133, 0.0002)
    assert parse_prepared_pictures(phases[1]) == (133, 0.0)
    assert [row["producer"] for row in parse_prepared_producers(text)] == ["previews", "previews"]
    assert [row["producer"] for row in parse_prepared_producers(phases[0])] == ["previews"]


def test_a_lane_says_in_one_word_whether_the_cache_already_held_a_run() -> None:
    """Neither remote lane can tell from the directory: both create it before the run."""
    assert parse_cache_primed("primed\n") is True
    assert parse_cache_primed("cold\n") is False
    assert parse_cache_primed("ssh: connect to host: Connection refused\n") is None
    assert parse_cache_primed("") is None


def test_the_models_fetch_phase_is_read_from_the_container_s_own_stopwatch() -> None:
    """The container echoes whole seconds; the runner never times the ssh round trip."""
    assert parse_models_fetch_seconds("412\n") == 412.0
    assert parse_models_fetch_seconds("0") == 0.0


def test_a_fetch_that_died_before_the_echo_stays_unmeasured() -> None:
    """`set -u` and a pipe mean the file can hold a shell error, or nothing at all."""
    assert parse_models_fetch_seconds("") is None
    assert parse_models_fetch_seconds("bash: SECONDS: unbound variable") is None
    assert parse_models_fetch_seconds("-12") is None


# What drew the titles and what encoded the film. Both come out of a logger
# rather than a renderer, and the lines below are copied from the first real
# cluster run: its Jobs asked for no GPU at all, drew their titles on CUDA off
# the shared card anyway, and encoded in software because the NVIDIA runtime
# exposed `compute,utility` and every NVENC probe died on -22.

_SRC = Path(__file__).resolve().parent.parent / "src" / "immich_memories"
_CUDA_TITLES = "Title kernels: quadrants 1.3.0 on the CUDA backend"
_NO_HWACCEL = "No hardware acceleration detected, using software encoding"


def test_the_title_backend_is_read_off_the_line_that_names_it() -> None:
    assert parse_title_backend(f"before\n{_CUDA_TITLES}\nafter") == "CUDA"
    assert (
        parse_title_backend(
            "No GPU kernel library on this platform; title screens use the PIL renderer"
        )
        == "PIL"
    )
    assert parse_title_backend("a run that never mentioned titles") is None


def test_the_encoder_is_what_the_assembly_said_it_used() -> None:
    """The assembly names the encoder, and it is the only line that says what ran."""
    assert parse_encoder("Streaming SDR assembly with h264_nvenc") == "h264_nvenc"
    assert parse_encoder("Streaming HLG HDR assembly with hevc_videotoolbox") == "hevc_videotoolbox"


def test_a_run_that_named_no_encoder_reports_the_backend_it_found() -> None:
    """`nvidia` is not `h264_nvenc`, and turning one into the other here would be a guess."""
    assert parse_encoder(_NO_HWACCEL) == "software"
    assert parse_encoder("Detected NVIDIA hardware acceleration: nvidia: T1000 (H.264 encode)") == (
        "nvidia"
    )
    assert parse_encoder("a run that never mentioned an encoder") is None


def test_the_lines_these_two_parsers_read_are_still_the_lines_the_app_prints() -> None:
    """Both come from a logger, so there is no renderer to call and compare against.

    The anchor is the source itself: reword either line and this fails here,
    instead of every row in a published table quietly showing a dash.
    """
    assert (
        '"Title kernels: %s %s on the %s backend"' in (_SRC / "titles" / "kernels.py").read_text()
    )
    hardware = (_SRC / "processing" / "hardware_detection.py").read_text()
    assert _NO_HWACCEL in hardware
    assembly = (_SRC / "processing" / "assembly_engine.py").read_text()
    assert '"Streaming SDR assembly with %s"' in assembly
    assert '"Streaming %s HDR assembly with %s"' in assembly


def _episode_warning(exc_type: str, detail: str) -> str:
    """The line the episode reader warns with, through the formatter the CLI installs."""
    return f"2026-09-14 02:24:51 WARNING text episode provider failed ({exc_type}): {detail}"


def _calls(attempt: Path, names: list[str]) -> Path:
    directory = attempt / "calls"
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).write_text("{}")
    return attempt


def test_a_reader_the_contracts_never_refused_reports_a_zero(tmp_path: Path) -> None:
    attempt = _calls(
        tmp_path, ["01-structure.request.private.txt", "02-story-pick-K01.request.private.txt"]
    )
    assert read_contract_health(attempt, "") == {"rejections": 0, "repairs": 0}


def test_every_repair_is_a_rejection_the_run_recovered_from(tmp_path: Path) -> None:
    attempt = _calls(
        tmp_path,
        [
            "02-story-pick-K01-source.request.private.txt",
            "03-story-pick-K01-source-repair.request.private.txt",
            "04-story-weights-repair-2.request.private.txt",
        ],
    )
    assert read_contract_health(attempt, "") == {"rejections": 2, "repairs": 2}


def test_an_answer_nothing_recovered_is_a_rejection_and_not_a_repair(tmp_path: Path) -> None:
    attempt = _calls(
        tmp_path,
        [
            "05-story-pick-K02-source.failure.private.json",
            "06-json-failure-4000.private.json",
            # A reply that was normalised, not one that was refused.
            "07-json-decoding-4000.private.json",
        ],
    )
    assert read_contract_health(attempt, "") == {"rejections": 2, "repairs": 0}


def test_the_episode_readers_warnings_count_once_per_reason(tmp_path: Path) -> None:
    """It warns once per distinct reason (#913), and a lane carries its stdout twice."""
    log = "\n".join(
        [
            _episode_warning("TextCompletionFailure", "incomplete after 2 attempts"),
            _episode_warning("TextCompletionFailure", "incomplete after 2 attempts"),
            _episode_warning("TimeoutError", "read timed out"),
        ]
    )
    attempt = _calls(tmp_path, ["01-episodes.request.private.txt"])
    assert read_contract_health(attempt, log) == {"rejections": 2, "repairs": 0}


def test_a_cell_that_asked_no_model_reports_nothing_rather_than_zero(tmp_path: Path) -> None:
    """A zero for a rules cell would read as a model that behaved perfectly."""
    assert read_contract_health(tmp_path, "") == {"rejections": None, "repairs": None}
    assert read_contract_health(None, "") == {"rejections": None, "repairs": None}


def test_the_tiles_the_reader_was_sent_come_off_the_plan(tmp_path: Path) -> None:
    """The reader is mostly text, and mostly is not never: those tiles are billed."""
    (tmp_path / "plan.private.json").write_text(
        json.dumps(
            {
                "picture_facts_metrics": {"images_sent": 36, "inference_calls": 12},
                "sampled_pair_metrics": {"images_sent": 4},
                "story_motion_facts": {"wall_seconds": 1.0},
                "tiers": {"a-story": "full"},
            }
        )
    )
    assert read_images_sent(tmp_path) == 40


def test_a_plan_that_counted_no_tiles_reports_nothing(tmp_path: Path) -> None:
    (tmp_path / "plan.private.json").write_text(json.dumps({"tiers": {}}))
    assert read_images_sent(tmp_path) is None
    assert read_images_sent(tmp_path / "no-attempt-here") is None


# What a run leaves in its log about which pictures it actually cut with, for a
# cell whose attempt directory never came back. `k8s-gpu-t1000` published
# `selected_asset_ids: []` beside a 54.5 s film it had plainly made out of
# something: the copy-out truncated, the attempt stayed on the volume, and the
# download lines were sitting in the log the whole time.


def _download_log(asset_ids: list[str], *, clips: int) -> str:
    """The lines a run really leaves: httpx on the URL `asset_service` builds, then the timing."""
    base = "http://a-fixture.invalid:8078"
    head = "2026-09-14 11:21:45,475 [INFO] httpx [-]: HTTP Request: GET "
    lines = ["2026-09-14 11:21:45,000 [INFO] immich_memories.progress [-]: Downloading clips..."]
    lines += [
        f'{head}{base}/api/assets/{asset_id}/original "HTTP/1.1 200 OK"' for asset_id in asset_ids
    ]
    lines.append(
        f"2026-09-14 11:25:35,849 [INFO] immich_memories.generate [-]: Pipeline timing "
        f"({clips} clips, 230.4s total): download=135.2s (59%), assembly=78.4s (34%)"
    )
    return "\n".join(lines) + "\n"


def test_the_pictures_a_run_fetched_are_read_back_off_its_log() -> None:
    text = _download_log(["garden-cake", "lake-sunset", "garden-cake"], clips=2)

    assert downloaded_asset_ids(text) == ["garden-cake", "lake-sunset"]


def test_a_thumbnail_is_not_a_picture_the_run_cut_with() -> None:
    """Preparation pulls a thumbnail for all 133; the cut downloads the originals."""
    text = _download_log(["garden-cake"], clips=1).replace(
        "Downloading clips...",
        "Preparing\n2026-09-14 11:00:00,000 [INFO] httpx [-]: HTTP Request: GET "
        'http://a-fixture.invalid:8078/api/assets/never-cut/thumbnail?size=preview "HTTP/1.1 200 OK"',
    )

    assert downloaded_asset_ids(text) == ["garden-cake"]


def test_the_film_says_how_many_clips_it_was_made_of() -> None:
    """The count is the film's own; the decoder lines name photos by id and videos by a hash."""
    assert film_clip_count(_download_log(["garden-cake"], clips=14)) == 14
    assert film_clip_count("a log from a run that never assembled anything") is None


def test_the_captioners_behind_a_cell_are_read_off_the_line_prepare_prints() -> None:
    """A matrix row compares readers given the same facts; two captioners break that."""
    from immich_memories.operations.caption_origins import caption_origin_summary

    provenance = {
        "origins": [
            {
                "model_id": "smolvlm2-500m-base-public",
                "endpoint": "http://localhost:8092/v1",
                "served": {"owned_by": "llamacpp", "meta.ftype": "Q8_0"},
                "control_digest": "61df0a0c11b612f4",
                "assets": 120,
            },
            {"status": "unknown", "assets": 13},
        ],
        "by_asset": {},
    }
    log = f"some other line\n{caption_origin_summary(provenance)}\nand another\n"

    read = parse_caption_origins(log)
    assert read["distinct"] == 2
    assert read["captions"] == 133
    assert read["mixed"] is True
    assert read["labels"][-1] == "unknown x13"


def test_a_cell_that_captioned_nothing_reports_no_captioners() -> None:
    assert parse_caption_origins("2 pictures prepared at 0.1 s/picture.") is None
