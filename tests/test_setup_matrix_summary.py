"""The published record says what it measured, and names what it did not."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_summary import (  # noqa: E402
    REFERENCE_CELL,
    build_markdown,
    build_summary,
    jaccard,
    order_kept,
)


def _row(cell_id: str, **overrides) -> dict:
    row = {
        "id": cell_id,
        "lane": "mac",
        "reader": "rules",
        "facts": "local",
        "tier": "full",
        "hosted": False,
        "skip_reason": None,
        "prepare_cache_primed": False,
        "timing": {
            "prepare_cold_s": 120.0,
            "prepare_warm_s": 2.0,
            "selection_s": 40.0,
            "render_s": 20.0,
            "total_s": 60.0,
            "peak_rss_mb": 2048.0,
            "cpu_s": 300.0,
        },
        "hosted_usage": {},
        "selected_asset_ids": ["a", "b", "c"],
        "video": {"duration_s": 61.4},
    }
    row.update(overrides)
    return row


def _summary(rows: list[dict]) -> dict:
    return build_summary(
        library="demo", month="2024-06", image="ghcr.io/example/app:0.84.1", rows=rows
    )


def test_overlap_is_measured_against_the_reference_cut() -> None:
    summary = _summary(
        [
            _row(REFERENCE_CELL),
            _row("nas-rules-local", selected_asset_ids=["a", "b", "d"]),
        ]
    )
    assert summary["cells"][0]["overlap_vs_cell_1"] == 1.0
    assert summary["cells"][1]["overlap_vs_cell_1"] == 0.5


def test_an_empty_cut_has_no_overlap_rather_than_a_zero() -> None:
    """Zero overlap and "this cell produced nothing" are different findings."""
    assert jaccard(["a"], []) is None


def test_order_kept_catches_a_setup_that_reordered_the_story() -> None:
    """Chronological order is a hard rule, so a false here is a defect, not a result."""
    assert order_kept(["a", "b", "c"], ["a", "c"]) is True
    assert order_kept(["a", "b", "c"], ["c", "a"]) is False
    assert order_kept(["a", "b", "c"], ["a"]) is None


def test_a_skipped_cell_stays_in_the_table_and_says_why() -> None:
    summary = _summary(
        [
            _row(REFERENCE_CELL),
            _row("nas-rules-local", skip_reason="needs MATRIX_NAS_SSH, absent"),
        ]
    )
    assert any("nas-rules-local: not run" in line for line in summary["unmeasured"])
    assert "skipped" in build_markdown(summary)


def test_a_hosted_cell_declares_that_no_price_was_measured() -> None:
    summary = _summary(
        [
            _row(REFERENCE_CELL),
            _row(
                "k8s-hosted-zai",
                hosted=True,
                hosted_usage={"tokens_in": 100, "tokens_out": 10, "est_cost_eur": None},
            ),
        ]
    )
    assert any("API cost in euro" in line for line in summary["unmeasured"])


def test_a_missing_timing_is_named_not_filled_in() -> None:
    row = _row("k8s-rules-local")
    row["timing"]["peak_rss_mb"] = None
    summary = _summary([_row(REFERENCE_CELL), row])
    assert any("peak_rss_mb" in line for line in summary["unmeasured"])
    assert "| - |" in build_markdown(summary)


def test_a_field_the_cell_explained_is_named_with_its_own_reason() -> None:
    """A host with no way to measure a field did not simply fail to report it."""
    row = _row("mac-local")
    row["timing"]["peak_rss_mb"] = None
    row["measurement_notes"] = {"peak_rss_mb": "per-step peak memory. /usr/bin/time is absent."}
    summary = _summary([row])
    assert "mac-local: per-step peak memory. /usr/bin/time is absent." in summary["unmeasured"]
    assert not any("peak_rss_mb, " in line for line in summary["unmeasured"])


def test_a_gap_that_is_not_a_timing_is_named_too() -> None:
    """A cell whose copy-out failed has its numbers and no film, and the run says which."""
    row = _row(
        "k8s-rules-service",
        measurement_notes={"film": "the film. The copy-out never brought it back."},
        video={},
    )

    unmeasured = _summary([row])["unmeasured"]

    assert "k8s-rules-service: the film. The copy-out never brought it back." in unmeasured


def test_a_primed_bank_means_cold_was_not_cold() -> None:
    summary = _summary([_row(REFERENCE_CELL, prepare_cache_primed=True)])
    assert any("true cold preparation" in line for line in summary["unmeasured"])


def test_the_table_carries_one_row_per_cell() -> None:
    rows = [_row(REFERENCE_CELL), _row("nas-rules-local"), _row("k8s-rules-local")]
    table = [line for line in build_markdown(_summary(rows)).splitlines() if line.startswith("| ")]
    assert len(table) == len(rows) + 1, "one header row plus one row per cell"
