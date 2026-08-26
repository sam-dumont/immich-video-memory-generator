"""Pass 1 reuses banked episode pixels to reject only clear defects."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from immich_memories.analysis.selection_flow import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), colour).save(output, "JPEG")
    return output.getvalue()


def _survivor_ids(result) -> tuple[str, ...]:
    return tuple(candidate.asset_id for candidate in result.survivors)


def test_record_collision_keeps_the_record_and_applies_a_valid_sibling_reject(
    tmp_path: Path,
) -> None:
    """Pass 1 reparses one bank and never asks again to protect a record shot."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    test = make_asset("test", file_created_at=when, is_favorite=True)
    blur = make_asset("blur", file_created_at=when + timedelta(seconds=1))
    neutral = make_asset("neutral", file_created_at=when + timedelta(seconds=2))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (test, blur, neutral),
            preview_jpeg=lambda asset: _jpeg(
                "white" if asset.id == "test" else "grey" if asset.id == "blur" else "blue"
            ),
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return json.dumps(
                {
                    "schema_version": "episode-scan-v3",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": 1,
                            "page": 1,
                            "visual_summary": "A result record beside an unreadable frame.",
                            "representative_tiles": [1, 2, 3],
                            "representative_reason": "Both visible functions describe the episode.",
                        }
                    ],
                    "record_shots": [
                        {
                            "tile": 1,
                            "function": "result proof",
                            "reason": "Records the result.",
                        }
                    ],
                    "cull_rejects": [
                        {
                            "tile": 1,
                            "defect": "unusable_exposure",
                            "reason": "The pixels are blown out.",
                        },
                        {
                            "tile": 2,
                            "defect": "unusable_motion_blur",
                            "reason": "The subject is unreadable through motion blur.",
                        },
                    ],
                }
            )
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": None,
                    "evidence": [],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": "One episode does not support a period thesis.",
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the sole provider boundary; source, atlas, bank, and Pass 1 stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )
    before = (calls, len(prepared.trace.requests))

    result = run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")

    assert _survivor_ids(result) == ("test", "neutral")
    assert tuple(mark.asset_id for mark in result.record_shots) == ("test",)
    assert tuple(decision.asset_id for decision in result.rejected) == ("blur",)
    assert before == (calls, len(prepared.trace.requests))
    assert result.actual_calls == 0
    assert result.warnings == ("!! cull reject conflicted with record-shot mark: test",)
    pass_one = prepared.trace.editorial_passes[-1]
    assert pass_one.name == "pass-1-cull"
    assert pass_one.record_shots == result.record_shots
    assert sum(request.actual_calls for request in pass_one.request_traces) == 0
    assert tuple(entry.asset_id for entry in result.review.entries) == prepared.candidate_ids
    assert tuple(entry.number for entry in result.review.entries) == (1, 2, 3)
    assert tuple(entry.status for entry in result.review.entries) == ("RECORD", "CULL", "KEEP")
    assert tuple(entry.favourite for entry in result.review.entries) == (True, False, False)
    assert tuple(entry.source_tile_sha256 for entry in result.review.entries) == tuple(
        pass_zero.atlas.tile_for(asset_id).sha256 for asset_id in prepared.candidate_ids
    )
    manifest = json.loads(result.review.manifest_path.read_text())
    assert manifest["warnings"] == ["!! cull reject conflicted with record-shot mark: test"]
    assert [entry["asset_id"] for entry in manifest["entries"]] == list(prepared.candidate_ids)
    with Image.open(BytesIO(result.review.pages[0].jpeg_bytes)) as review_page:
        red, green, blue = review_page.convert("RGB").getpixel((2, 2))
    assert red > 120 and green < 80 and blue < 80


def test_failed_middle_pack_does_not_shift_later_bank_or_reused_wire_alias(
    tmp_path: Path,
) -> None:
    """Exact pack keys isolate three identical wire aliases around one timeout."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull
    from immich_memories.analysis.visual_request_planner import VisionRequestLimits

    start = datetime(2026, 8, 25, 8, tzinfo=UTC)
    assets = tuple(
        make_asset(asset_id, file_created_at=start + timedelta(hours=index * 2))
        for index, asset_id in enumerate(("first", "middle", "third"))
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("blue"),
        ),
    )
    episode_asks = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal episode_asks
        assert "chronological episode pack" in prompt
        episode_asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if episode_asks == 2:
            raise TimeoutError("generated middle timeout")
        return json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "One generated visual.",
                        "representative_tiles": [1],
                        "representative_reason": "It is the only visible stage.",
                    }
                ],
                "record_shots": [],
                "cull_rejects": [
                    {
                        "tile": 1,
                        "defect": "unusable_exposure",
                        "reason": "The pixels are completely unreadable.",
                    }
                ],
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; packing, failure provenance, and replay stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            limits=VisionRequestLimits(max_output_tokens=250, timeout_seconds=30),
        )

    assert len(pass_zero.episode_packs) == 3
    assert [attempt.answer is not None for attempt in pass_zero.scan_attempts] == [
        True,
        False,
        True,
    ]
    original_attempts = pass_zero.scan_attempts
    with pytest.raises(ValueError, match="attempt identities"):
        replace(pass_zero, scan_attempts=(original_attempts[0], original_attempts[0]))
    assert original_attempts[1].request_trace is prepared.trace.requests[1]
    assert original_attempts[1].request_trace.actual_calls == 1
    swapped = replace(
        pass_zero,
        scan_attempts=(
            replace(original_attempts[0], answer=original_attempts[2].answer),
            original_attempts[1],
            replace(original_attempts[2], answer=original_attempts[0].answer),
        ),
    )
    swapped_result = run_cull(
        prepared,
        swapped,
        review_output_dir=tmp_path / "review-swapped",
    )
    assert _survivor_ids(swapped_result) == ("first", "middle", "third")
    assert (
        sum("mismatched episode scan provenance" in item for item in swapped_result.warnings) == 2
    )

    pass_zero = replace(pass_zero, scan_attempts=tuple(reversed(pass_zero.scan_attempts)))
    result = run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")

    assert _survivor_ids(result) == ("middle",)
    assert tuple(decision.asset_id for decision in result.rejected) == ("first", "third")
    assert result.warnings == (
        "!! Pass 0 incomplete visual evidence; period thesis unavailable",
        f"!! Pass 1 failed episode scan: {pass_zero.episode_packs[1].page.sheet_id}",
    )
    assert len(prepared.trace.requests) == 3
    assert sum(request.actual_calls for request in result.trace.request_traces) == 0
    assert result.trace.request_traces[1].provenance.request_key == (
        original_attempts[1].request_trace.provenance.request_key
    )


def test_malformed_episode_reading_does_not_erase_valid_pass_one_namespaces(
    tmp_path: Path,
) -> None:
    """Pass 0 can fail open while an independently valid record and Cull still actuate."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    record = make_asset("record", file_created_at=when)
    bad = make_asset("bad", file_created_at=when + timedelta(seconds=1))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (record, bad),
            preview_jpeg=lambda _asset: _jpeg("green"),
        ),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "episode_readings": [{"episode": 1, "page": 1, "visual_summary": "missing"}],
                "record_shots": [
                    {
                        "tile": 1,
                        "function": "scoreboard result",
                        "reason": "Records an arbitrary scoreboard result.",
                    }
                ],
                "cull_rejects": [
                    {
                        "tile": 2,
                        "defect": "accidental_capture",
                        "reason": "The lens is fully obscured by a finger.",
                    }
                ],
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; namespace isolation is exercised end to end.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert pass_zero.episode_readings == ()
    assert pass_zero.insight.thesis is None
    result = run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")

    assert _survivor_ids(result) == ("record",)
    assert tuple(mark.asset_id for mark in result.record_shots) == ("record",)
    assert tuple(decision.asset_id for decision in result.rejected) == ("bad",)


@pytest.mark.parametrize("missing_namespace", ("record_shots", "cull_rejects"))
def test_missing_pass_one_namespace_warns_and_preserves_its_valid_sibling(
    tmp_path: Path,
    missing_namespace: str,
) -> None:
    """A missing namespace rejects nothing of its own and makes the owner sheet invalid."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    record = make_asset("record", file_created_at=when)
    bad = make_asset("bad", file_created_at=when + timedelta(seconds=1))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (record, bad),
            preview_jpeg=lambda _asset: _jpeg("green"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" not in prompt:
            return json.dumps(
                {
                    "schema_version": "period-insight-v1",
                    "period_insight": {
                        "thesis": None,
                        "evidence": [],
                        "tensions": [],
                        "recurring_threads": [],
                        "unavailable_reason": "No thesis needed.",
                    },
                }
            )
        payload = {
            "schema_version": "episode-scan-v3",
            "pack": 1,
            "episode_readings": [
                {
                    "episode": 1,
                    "page": 1,
                    "visual_summary": "A record beside a broken image.",
                    "representative_tiles": [1],
                    "representative_reason": "The record is visible.",
                }
            ],
            "record_shots": [
                {
                    "tile": 1,
                    "function": "result proof",
                    "reason": "Records the result.",
                }
            ],
            "cull_rejects": [
                {
                    "tile": 2,
                    "defect": "unusable_exposure",
                    "reason": "The pixels are fully blown out.",
                }
            ],
        }
        del payload[missing_namespace]
        return json.dumps(payload)

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / f"judgments-{missing_namespace}.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; namespace parsing and owner warnings stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / f"sheets-{missing_namespace}",
            frame_cache_dir=None,
        )

    result = run_cull(
        prepared,
        pass_zero,
        review_output_dir=tmp_path / f"review-{missing_namespace}",
    )

    assert result.warnings == (
        f"!! Pass 1 invalid {('record-shot' if missing_namespace == 'record_shots' else 'Cull')} "
        f"namespace: {pass_zero.episode_packs[0].page.sheet_id}",
    )
    if missing_namespace == "record_shots":
        assert result.record_shots == ()
        assert tuple(item.asset_id for item in result.rejected) == ("bad",)
    else:
        assert tuple(item.asset_id for item in result.record_shots) == ("record",)
        assert result.rejected == ()


@pytest.mark.parametrize(
    ("reject_count", "expected_warning"),
    ((3, ()), (4, ("!! possible over-cull",))),
)
def test_over_cull_warns_only_above_seventy_five_percent_without_restoration(
    tmp_path: Path,
    reject_count: int,
    expected_warning: tuple[str, ...],
) -> None:
    """The over-cull guard is a diagnostic integer boundary, never a score repair."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(4)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("yellow"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return json.dumps(
                {
                    "schema_version": "episode-scan-v3",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": 1,
                            "page": 1,
                            "visual_summary": "Four generated frames.",
                            "representative_tiles": [1],
                            "representative_reason": "The first is a visible representative.",
                        }
                    ],
                    "record_shots": [],
                    "cull_rejects": [
                        {
                            "tile": number,
                            "defect": "unusable_exposure",
                            "reason": "The pixels are fully blown out.",
                        }
                        for number in range(1, reject_count + 1)
                    ],
                }
            )
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": None,
                    "evidence": [],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": "No period thesis is necessary.",
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / f"judgments-{reject_count}.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; threshold behavior and owner artifact stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / f"sheets-{reject_count}",
            frame_cache_dir=None,
        )
    result = run_cull(
        prepared,
        pass_zero,
        review_output_dir=tmp_path / f"review-{reject_count}",
    )

    assert len(result.rejected) == reject_count
    assert len(result.survivors) == 4 - reject_count
    assert result.warnings == expected_warning
    with Image.open(BytesIO(result.review.pages[0].jpeg_bytes)) as review_page:
        red, green, blue = review_page.convert("RGB").getpixel((2, 2))
    if expected_warning:
        assert red > 120 and green < 80 and blue < 80
    else:
        assert not (red > 120 and green < 80 and blue < 80)


def test_multi_page_review_reuses_atlas_and_marks_generic_record_functions(
    tmp_path: Path,
) -> None:
    """Pregnancy proof and an arbitrary ticket survive by visible function, not keywords."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index:03d}", file_created_at=when + timedelta(seconds=index))
        for index in range(121)
    )
    preview_calls = 0

    def _preview(_asset):
        nonlocal preview_calls
        preview_calls += 1
        return _jpeg("purple")

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=_preview,
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" not in prompt:
            return json.dumps(
                {
                    "schema_version": "period-insight-v1",
                    "period_insight": {
                        "thesis": None,
                        "evidence": [],
                        "tensions": [],
                        "recurring_threads": [],
                        "unavailable_reason": "One episode does not support a thesis.",
                    },
                }
            )
        displayed = [
            int(value) for value in re.search(r"tiles=\[([^\]]+)\]", prompt).group(1).split(",")
        ]
        first_page = displayed[0] == 1
        return json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "Generated continuation.",
                        "representative_tiles": [displayed[0]],
                        "representative_reason": "The first visible tile identifies this page.",
                    }
                ],
                "record_shots": (
                    [
                        {
                            "tile": 1,
                            "function": "pregnancy result",
                            "reason": "Records a pregnancy-test result.",
                        },
                        {
                            "tile": 2,
                            "function": "admission proof",
                            "reason": "Records admission with a dated ticket.",
                        },
                    ]
                    if first_page
                    else []
                ),
                "cull_rejects": (
                    [
                        {
                            "tile": 1,
                            "defect": "unusable_exposure",
                            "reason": "The pixels are very bright.",
                        },
                        {
                            "tile": 3,
                            "defect": "corrupt_or_obscured_pixels",
                            "reason": "The pixels are fully obscured.",
                        },
                    ]
                    if first_page
                    else []
                ),
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; every visual artifact is generated locally.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )
    retained_atlas = pass_zero.atlas
    physical_before = (
        calls,
        preview_calls,
        json.dumps(prepared.trace.as_dict()["requests"], sort_keys=True),
    )

    # WHY: a second atlas build would hide an expensive preview/frame-sampling regression.
    with (
        patch(
            "immich_memories.analysis.visual_atlas.build_visual_atlas",
            side_effect=AssertionError("review must reuse retained atlas"),
        ),
        patch(
            "immich_memories.analysis.period_insight.build_visual_atlas",
            side_effect=AssertionError("review must not rebuild the atlas"),
        ),
    ):
        result = run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")

    assert pass_zero.atlas is retained_atlas
    assert physical_before == (
        calls,
        preview_calls,
        json.dumps(prepared.trace.as_dict()["requests"], sort_keys=True),
    )
    assert tuple(mark.asset_id for mark in result.record_shots) == ("asset-000", "asset-001")
    assert tuple(mark.function for mark in result.record_shots) == (
        "pregnancy result",
        "admission proof",
    )
    assert "asset-000" in _survivor_ids(result)
    assert "asset-001" in _survivor_ids(result)
    assert "asset-002" not in _survivor_ids(result)
    assert len(result.review.pages) == 2
    assert tuple(entry.asset_id for entry in result.review.entries) == prepared.candidate_ids
    assert tuple(entry.number for entry in result.review.entries) == tuple(range(1, 122))
    assert [result.review.entries[index].status for index in range(3)] == [
        "RECORD",
        "RECORD",
        "CULL",
    ]
    manifest = json.loads(result.review.manifest_path.read_text())
    assert manifest["entries"][0]["reason"] == (
        "pregnancy result: Records a pregnancy-test result."
    )
    assert (
        manifest["warnings"].count("!! cull reject conflicted with record-shot mark: asset-000")
        == 1
    )
    for page in result.review.pages:
        with Image.open(BytesIO(page.jpeg_bytes)) as image:
            red, green, blue = image.convert("RGB").getpixel((2, 2))
        assert red > 120 and green < 80 and blue < 80


def test_public_source_insight_cull_flow_uses_one_trace_and_never_subject_quotas(
    tmp_path: Path,
) -> None:
    """Every source kind reaches one fused visual request with subject evidence only."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull
    from immich_memories.api.models import AssetType, Person, VideoClipInfo

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    face = make_asset("face", file_created_at=when)
    face.people = [Person(id="person")]
    screen_asset = make_asset("screen", file_created_at=when + timedelta(seconds=1))
    object_asset = make_asset("object", file_created_at=when + timedelta(seconds=2))
    screenshot = make_asset(
        "screenshot",
        file_created_at=when + timedelta(seconds=3),
        original_file_name="Screenshot_20260825.png",
    )
    document = make_asset(
        "document",
        file_created_at=when + timedelta(seconds=4),
        original_file_name="ticket.jpg",
    )
    pregnancy = make_asset(
        "pregnancy",
        file_created_at=when + timedelta(seconds=5),
        original_file_name="IMG_0001.jpg",
    )
    for still in (screenshot, document, pregnancy):
        still.type = AssetType.IMAGE
    sources = (
        face,
        VideoClipInfo(asset=screen_asset, llm_category="screen", duration_seconds=1.0),
        VideoClipInfo(asset=object_asset, llm_category="object", duration_seconds=1.0),
        screenshot,
        document,
        pregnancy,
    )
    prompts: list[str] = []
    traces = []
    episode_asks = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal episode_asks
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            episode_asks += 1
            if episode_asks > 1:
                raise AssertionError("Pass 1 made a second episode ask")
            scopes = re.findall(r"episode=(\d+) page=(\d+) tiles=\[([^\]]+)\]", prompt)
            return json.dumps(
                {
                    "schema_version": "episode-scan-v3",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": int(episode),
                            "page": int(page),
                            "visual_summary": "Generated source types remain visible.",
                            "representative_tiles": [int(tiles.split(",")[0])],
                            "representative_reason": "The visible tile represents this episode.",
                        }
                        for episode, page, tiles in scopes
                    ],
                    "record_shots": [],
                    "cull_rejects": [],
                }
            )
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": None,
                    "evidence": [],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": "Generated evidence does not need a thesis.",
                },
            }
        )

    def gateway_factory(trace):
        traces.append(trace)
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the sole external provider; quota functions are forbidden on this path.
    with (
        patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer),
        patch(
            "immich_memories.analysis.subject_policy.apply_subject_quotas",
            side_effect=AssertionError("legacy quota called"),
        ),
        patch(
            "immich_memories.analysis.subject_policy.filter_candidates_by_subject",
            side_effect=AssertionError("legacy subject filter called"),
        ),
    ):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: sources,
                preview_jpeg=lambda _asset: _jpeg("teal"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert traces == [result.prepared.trace]
    assert _survivor_ids(result.pass_one) == result.prepared.candidate_ids
    assert result.prepared.candidate_ids == (
        "face",
        "screen",
        "object",
        "screenshot",
        "document",
        "pregnancy",
    )
    assert [item.name for item in result.prepared.trace.editorial_passes] == [
        "source-eligibility",
        "pass-0",
        "pass-1-cull",
    ]
    assert len(result.prepared.trace.requests) == 2
    assert episode_asks == 1
    assert result.pass_one.warnings == ()
    assert result.pass_one.review.warnings == ()
    episode_prompt = next(prompt for prompt in prompts if "chronological episode pack" in prompt)
    assert "subject-evidence:people" in episode_prompt
    assert "subject-evidence:screen" in episode_prompt
    assert "subject-evidence:object" in episode_prompt
    assert "subject-evidence:unknown" in episode_prompt
