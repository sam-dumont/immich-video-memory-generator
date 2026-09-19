"""The published record says what it measured, and names what it did not."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from immich_memories.analysis.llm_metrics import collecting, record_reply
from immich_memories.analysis.llm_usage_record import write_llm_usage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_capture import apply_exact_usage  # noqa: E402
from setup_matrix_summary import (  # noqa: E402
    REFERENCE_CELL,
    build_markdown,
    build_summary,
    estimated_cost,
    jaccard,
    order_kept,
    render_device,
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
                hosted_usage={"tokens_in": 100, "tokens_out": 10, "est_cost": None},
            ),
        ]
    )
    assert any("what the API cost" in line for line in summary["unmeasured"])


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


def test_the_table_names_the_card_a_gpu_cell_rendered_on() -> None:
    """Two GPU rows are only worth reading if each says which card it was."""
    summary = _summary(
        [
            _row(REFERENCE_CELL),
            _row("k8s-gpu-t1000", gpu_product="NVIDIA-T1000-8GB-SHARED"),
            _row("k8s-gpu-1070", gpu_product="NVIDIA-GeForce-GTX-1070-SHARED"),
        ]
    )
    table = build_markdown(summary)

    assert "## Render device" in table
    assert "- `k8s-gpu-t1000`: NVIDIA-T1000-8GB-SHARED" in table
    assert "- `k8s-gpu-1070`: NVIDIA-GeForce-GTX-1070-SHARED" in table


def test_the_render_device_column_keeps_the_titles_and_the_encoder_apart() -> None:
    """They disagreed on the first cluster run, and that disagreement is the finding.

    Those Jobs asked for no GPU, drew their titles on the shared card anyway, and
    encoded in software because the runtime gave the pod no `video` capability.
    """
    rows = [
        _row(REFERENCE_CELL, title_backend="Metal", encoder="h264_videotoolbox"),
        _row("k8s-rules-service", title_backend="CUDA", encoder="software"),
        _row("k8s-gpu-t1000", title_backend="CUDA", encoder="h264_nvenc"),
        _row("nas-rules-local", title_backend="PIL"),
    ]
    table = build_markdown(_summary(rows))

    assert "render device" in table
    assert "CUDA titles / software" in table
    assert "CUDA titles / h264_nvenc" in table
    assert "PIL titles / ?" in table, "half an answer is still the half the run printed"


def test_a_cell_that_named_neither_shows_a_dash() -> None:
    assert render_device(_row("k8s-rules-local")) == "-"


def test_a_skipped_row_fills_every_column() -> None:
    """One column was added and the skipped row is built by count, not by hand."""
    table = build_markdown(_summary([_row("k8s-full-rules", skip_reason="no overlay")]))
    header, _, row = table.splitlines()[4:7]
    assert row.count("|") == header.count("|")


def test_a_run_with_no_gpu_cell_gets_no_render_device_section() -> None:
    assert "Render device" not in build_markdown(_summary([_row(REFERENCE_CELL)]))


def test_the_record_says_which_card_answered_the_facts_requests() -> None:
    """Unpinned, the scheduler picks, so every service row shares whichever card it got."""
    summary = build_summary(
        library="demo",
        month="2024-06",
        image="ghcr.io/example/app:0.84.1",
        rows=[_row(REFERENCE_CELL)],
        inference_gpu_product="NVIDIA-T1000-8GB-SHARED",
    )
    assert summary["inference_gpu_product"] == "NVIDIA-T1000-8GB-SHARED"
    assert _summary([_row(REFERENCE_CELL)])["inference_gpu_product"] is None


def test_the_record_says_which_device_wrote_the_captions() -> None:
    """A full-tier row is mostly preparation, and preparation is mostly captions.

    Measured on the cluster: 3.5 s a picture on the CPU image against 0.08 to
    0.23 s on a GPU, so `prep cold` on a full-tier row means nothing at all until
    the record says which of the two answered.
    """
    summary = build_summary(
        library="demo",
        month="2024-06",
        image="ghcr.io/example/app:0.84.1",
        rows=[_row(REFERENCE_CELL)],
        captioner_device="cuda",
    )
    assert summary["captioner_device"] == "cuda"
    assert _summary([_row(REFERENCE_CELL)])["captioner_device"] is None


PRICES = {
    "hosted_melious": {
        "currency": "EUR",
        "gemma-4-31b": {
            "input_per_million": 0.10,
            "output_per_million": 0.30,
            "source": "https://melious.ai/hub/models/gemma-4-31b",
            "retrieved": "2026-09-14",
        },
    }
}


def _priced(rows: list[dict]) -> dict:
    return build_summary(
        library="demo",
        month="2024-06",
        image="ghcr.io/example/app:0.84.1",
        rows=rows,
        pricing=PRICES,
    )


def test_a_priced_row_multiplies_the_list_by_the_tokens_it_measured() -> None:
    row = _row(
        "mac-hosted-melious-gemma-4-31b",
        hosted=True,
        reader="hosted_melious",
        reader_model="gemma-4-31b",
        contract={"rejections": 0, "repairs": 0},
        hosted_usage={
            "calls": 42,
            "tokens_in": 125_400,
            "tokens_out": 8_300,
            "images_sent": 36,
            "est_cost": None,
        },
    )
    summary = _priced([_row(REFERENCE_CELL), row])

    usage = summary["cells"][1]["hosted_usage"]
    assert usage["est_cost"] == 0.015  # 125_400 in at 0.10 plus 8_300 out at 0.30
    assert usage["cost_currency"] == "EUR"
    assert usage["price_source"] == "https://melious.ai/hub/models/gemma-4-31b"
    assert not any("what the API cost" in line for line in summary["unmeasured"])
    table = build_markdown(summary)
    assert "list price times measured tokens" in table
    assert "42 calls, 125.4k in / 8.3k out, 36 tiles" in table


def test_a_model_with_no_price_row_stays_unmeasured() -> None:
    """A token count with no price is not money, and the table has to keep saying so."""
    row = _row(
        "nas-hosted-zai",
        hosted=True,
        reader="hosted_zai",
        reader_model="glm-5.3-flash",
        hosted_usage={"tokens_in": 100, "tokens_out": 10, "est_cost": None},
    )
    summary = _priced([_row(REFERENCE_CELL), row])
    assert summary["cells"][1]["hosted_usage"]["est_cost"] is None
    assert any("what the API cost" in line for line in summary["unmeasured"])


def test_a_price_with_no_token_count_buys_nothing() -> None:
    price = PRICES["hosted_melious"]["gemma-4-31b"]
    assert estimated_cost({"tokens_in": None, "tokens_out": None}, price) is None
    assert estimated_cost({"tokens_in": 10, "tokens_out": 1}, None) is None


@pytest.mark.parametrize(
    "stage,known",
    [("caption_controls", True), ("caption", True), ("motion", True), ("reader", False)],
)
@pytest.mark.parametrize("old_cost", [None, 42.0])
def test_report_does_not_price_preparation_or_unknown_usage_as_hosted_reader_tokens(
    tmp_path, stage, known, old_cost
):
    with collecting() as counters:
        record_reply(prompt_tokens=1000, completion_tokens=100, stage=stage, usage_known=known)
    write_llm_usage(tmp_path, counters)
    usage = {"est_cost": old_cost}
    apply_exact_usage(usage, tmp_path)
    row = _row(
        "mixed-cost",
        hosted=True,
        reader="hosted_melious",
        reader_model="gemma-4-31b",
        hosted_usage=usage,
    )

    summary = _priced([_row(REFERENCE_CELL), row])

    assert summary["cells"][1]["hosted_usage"]["est_cost"] is None
    expected = "preparation" if known else "usage"
    assert any(expected in note for note in summary["unmeasured"])


def test_the_contract_column_carries_the_rejections_and_the_repairs() -> None:
    row = _row(
        "mac-hosted-melious-glm-5.3-flash",
        reader="hosted_melious",
        contract={"rejections": 3, "repairs": 2},
    )
    assert "| 3/2 |" in build_markdown(_summary([_row(REFERENCE_CELL), row]))


def test_a_reader_that_left_no_transcripts_says_so_rather_than_claiming_zero() -> None:
    summary = _summary([_row(REFERENCE_CELL, reader="local_model")])
    assert any("contract health" in line for line in summary["unmeasured"])


def test_a_rules_cell_is_not_asked_about_contracts_it_never_had() -> None:
    assert not any(
        "contract health" in line for line in _summary([_row(REFERENCE_CELL)])["unmeasured"]
    )


def test_a_seeded_cell_points_at_the_cell_that_measured_its_preparation() -> None:
    """Preparation is a fact about the host and the tier, and six rows of it is six copies."""
    row = _row(
        "mac-local-gemma4",
        reader="local_model",
        seeded_from="mac-local",
        prepare_cache_primed="seeded from mac-local",
        measurement_notes={
            "prepare_cold_s": "preparation. This cell's bank was seeded from `mac-local`.",
            "prepare_warm_s": "preparation. This cell's bank was seeded from `mac-local`.",
        },
        contract={"rejections": 0, "repairs": 0},
    )
    row["timing"]["prepare_cold_s"] = None
    row["timing"]["prepare_warm_s"] = None
    summary = _summary([_row(REFERENCE_CELL), row])

    assert "| = mac-local |" in build_markdown(summary)
    assert any("seeded from `mac-local`" in line for line in summary["unmeasured"])
    assert not any("a true cold preparation" in line for line in summary["unmeasured"])
