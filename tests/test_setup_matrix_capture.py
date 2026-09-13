"""The parsers read the run's own output, not something that looks like it.

Every input below is built by calling the renderers the CLI actually prints with
(`render_run_summary`, `saved_path_line`), so a change to either format fails
here instead of quietly producing a plausible wrong number in a published table.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_capture import (  # noqa: E402
    anonymize,
    clock_seconds,
    parse_cgroup_cpu_seconds,
    parse_cgroup_peak_rss_mb,
    parse_models_fetch_seconds,
    parse_prepare_seconds,
    parse_prepared_pictures,
    parse_prepared_producers,
    parse_run_summary,
    parse_saved_path,
    parse_time_peak_rss_mb,
)

from immich_memories.analysis.llm_metrics import LLMCounters  # noqa: E402
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
    # No provider in the matrix returns a price, so this must stay empty rather
    # than be filled from a price list.
    assert usage.est_cost_eur is None


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


def test_the_models_fetch_phase_is_read_from_the_container_s_own_stopwatch() -> None:
    """The container echoes whole seconds; the runner never times the ssh round trip."""
    assert parse_models_fetch_seconds("412\n") == 412.0
    assert parse_models_fetch_seconds("0") == 0.0


def test_a_fetch_that_died_before_the_echo_stays_unmeasured() -> None:
    """`set -u` and a pipe mean the file can hold a shell error, or nothing at all."""
    assert parse_models_fetch_seconds("") is None
    assert parse_models_fetch_seconds("bash: SECONDS: unbound variable") is None
    assert parse_models_fetch_seconds("-12") is None
