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


def _assert_pixel_near(
    image: Image.Image,
    point: tuple[int, int],
    expected: tuple[int, int, int],
    *,
    tolerance: int = 35,
) -> None:
    actual = image.convert("RGB").getpixel(point)
    assert all(
        abs(channel - target) <= tolerance for channel, target in zip(actual, expected, strict=True)
    )


def _pass_zero_for(tmp_path: Path, *, assets: tuple[str, ...], when, favourites: tuple = ()):
    """A prepared corpus and a Pass 0 whose model answered, culling nothing."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    built = tuple(
        make_asset(
            asset_id,
            file_created_at=when + timedelta(seconds=index),
            is_favorite=asset_id in favourites,
        )
        for index, asset_id in enumerate(assets)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: built,
            preview_jpeg=lambda _asset: _jpeg("slate"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return json.dumps(
                {
                    "schema_version": "episode-scan-v4",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": 1,
                            "page": 1,
                            "visual_summary": "Generated frames.",
                            "representative_tiles": [1],
                            "representative_reason": "The first tile stands for the page.",
                        }
                    ],
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
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
                    "unavailable_reason": "Too little for a thesis.",
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; source, atlas and packing stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )
    return prepared, pass_zero


def _run_single_protection_case(tmp_path: Path, *, favourite: bool):
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    asset = make_asset(
        "asset",
        file_created_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
        is_favorite=favourite,
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (asset,),
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return json.dumps(
                {
                    "schema_version": "episode-scan-v4",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": 1,
                            "page": 1,
                            "visual_summary": "One generated visual.",
                            "representative_tiles": [1],
                            "representative_reason": "The tile is the complete page.",
                        }
                    ],
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": [1]}],
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
                    "unavailable_reason": "One visual has no period thesis.",
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider boundary; protection and review stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )
    return run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")


def _run_unstarred_stitch_case(tmp_path: Path):
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull
    from immich_memories.api.models import AssetType, VideoClipInfo

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    noncarrier = make_asset("noncarrier", file_created_at=when)
    noncarrier.type = AssetType.IMAGE
    noncarrier.live_photo_video_id = "noncarrier-motion"
    carrier = make_asset("carrier", file_created_at=when + timedelta(seconds=1))
    carrier.type = AssetType.IMAGE
    carrier.live_photo_video_id = "carrier-motion"
    enriched = VideoClipInfo(
        asset=carrier,
        duration_seconds=4.0,
        width=1920,
        height=1080,
        live_burst_still_ids=["noncarrier", "carrier"],
        live_burst_video_ids=["noncarrier-motion", "carrier-motion"],
        live_burst_trim_points=[(0.0, 1.0), (0.5, 1.5)],
        live_burst_shutter_timestamps=[
            noncarrier.file_created_at.timestamp(),
            carrier.file_created_at.timestamp(),
        ],
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
        return json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "Two unstarred members share a motion option.",
                        "representative_tiles": [1],
                        "representative_reason": "The first tile remains on visible merit.",
                    }
                ],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [2]}],
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; stitch membership and Cull stay production-real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        return run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (noncarrier, enriched),
                preview_jpeg=lambda _asset: _jpeg("violet"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )


def test_a_favourite_survives_a_cull_that_named_it(tmp_path: Path) -> None:
    """The star settles it here as it settles every other hard gate."""
    result = _run_single_protection_case(tmp_path, favourite=True)

    assert _survivor_ids(result) == ("asset",)
    assert result.rejected == ()
    assert result.warnings == ("!! cull reject conflicted with protected favourite: asset",)
    assert result.review.entries[0].favourite is True
    assert result.review.entries[0].status == "KEEP"


def test_an_unstarred_failed_picture_is_culled(tmp_path: Path) -> None:
    """Protection is the exception; a picture that did not come out still goes."""
    result = _run_single_protection_case(tmp_path, favourite=False)

    assert _survivor_ids(result) == ()
    assert tuple(decision.asset_id for decision in result.rejected) == ("asset",)
    assert tuple(decision.bucket for decision in result.rejected) == ("failed",)
    assert result.warnings == ("!! possible over-cull",)
    assert result.review.entries[0].status == "CULL"


def test_live_photo_favourite_shields_only_its_exact_candidate_not_stitch_sibling(
    tmp_path: Path,
) -> None:
    """Cull preserves the star without choosing its later still-or-motion rendering."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull
    from immich_memories.api.models import AssetType, VideoClipInfo

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    favourite = make_asset("favourite", file_created_at=when, is_favorite=True)
    favourite.type = AssetType.IMAGE
    favourite.live_photo_video_id = "favourite-motion"
    sibling = make_asset("sibling", file_created_at=when + timedelta(seconds=1))
    sibling.type = AssetType.IMAGE
    sibling.live_photo_video_id = "sibling-motion"
    enriched = VideoClipInfo(
        asset=sibling,
        duration_seconds=4.0,
        width=1920,
        height=1080,
        live_burst_still_ids=["favourite", "sibling"],
        live_burst_video_ids=["favourite-motion", "sibling-motion"],
        live_burst_trim_points=[(0.0, 1.0), (0.5, 1.5)],
        live_burst_shutter_timestamps=[
            favourite.file_created_at.timestamp(),
            sibling.file_created_at.timestamp(),
        ],
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
        return json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "Two still candidates share a later motion option.",
                        "representative_tiles": [1],
                        "representative_reason": "The first tile identifies the moment.",
                    }
                ],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [1, 2]}],
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; exact favourite protection stays production-real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (favourite, enriched),
                preview_jpeg=lambda _asset: _jpeg("purple"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert result.prepared.candidate_ids == ("favourite", "sibling")
    assert tuple(
        candidate.live_photo_stitch_member_ids for candidate in result.prepared.candidates
    ) == (
        ("favourite", "sibling"),
        ("favourite", "sibling"),
    )
    assert len(result.prepared.rendering_families) == 1
    family_id = result.prepared.rendering_families[0].family_id
    assert {candidate.rendering_family_id for candidate in result.prepared.candidates} == {
        family_id
    }
    assert _survivor_ids(result.pass_one) == ("favourite",)
    assert tuple(decision.asset_id for decision in result.pass_one.rejected) == ("sibling",)
    assert tuple(entry.status for entry in result.pass_one.review.entries) == ("KEEP", "CULL")
    assert tuple(entry.favourite for entry in result.pass_one.review.entries) == (True, False)


def test_unstarred_noncarrier_keeps_stitch_family_on_merit_when_carrier_is_culled(
    tmp_path: Path,
) -> None:
    """Legacy carrier identity cannot own the later motion option."""
    result = _run_unstarred_stitch_case(tmp_path)

    assert _survivor_ids(result.pass_one) == ("noncarrier",)
    assert tuple(decision.asset_id for decision in result.pass_one.rejected) == ("carrier",)
    survivor = result.pass_one.survivors[0]
    assert survivor.favourite is False
    assert survivor.live_photo_stitch_member_ids == ("noncarrier", "carrier")
    assert survivor.rendering_family_id == result.prepared.rendering_families[0].family_id
    assert result.pass_one.warnings == ()


def test_cull_reparses_one_bank_and_never_asks_again(
    tmp_path: Path,
) -> None:
    """Cull is a logical pass, not a request: it costs no model call of its own."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    test = make_asset("test", file_created_at=when, is_favorite=True)
    blur = make_asset("blur", file_created_at=when + timedelta(seconds=1))
    neutral = make_asset(
        "neutral",
        file_created_at=when + timedelta(seconds=2),
        is_favorite=True,
    )
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
                    "schema_version": "episode-scan-v4",
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
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": [1, 2, 3]}],
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
    assert tuple(decision.asset_id for decision in result.rejected) == ("blur",)
    assert before == (calls, len(prepared.trace.requests))
    assert result.actual_calls == 0
    assert result.warnings == (
        "!! cull reject conflicted with protected favourite: test",
        "!! cull reject conflicted with protected favourite: neutral",
    )
    pass_one = prepared.trace.editorial_passes[-1]
    assert pass_one.name == "pass-1-cull"
    assert sum(request.actual_calls for request in pass_one.request_traces) == 0
    assert tuple(entry.asset_id for entry in result.review.entries) == prepared.candidate_ids
    assert tuple(entry.number for entry in result.review.entries) == (1, 2, 3)
    assert tuple(entry.status for entry in result.review.entries) == ("KEEP", "CULL", "KEEP")
    assert tuple(entry.favourite for entry in result.review.entries) == (True, False, True)
    assert tuple(entry.source_tile_sha256 for entry in result.review.entries) == tuple(
        pass_zero.atlas.tile_for(asset_id).sha256 for asset_id in prepared.candidate_ids
    )
    manifest = json.loads(result.review.manifest_path.read_text())
    assert manifest["warnings"] == [
        "!! cull reject conflicted with protected favourite: test",
        "!! cull reject conflicted with protected favourite: neutral",
    ]
    assert [entry["asset_id"] for entry in manifest["entries"]] == list(prepared.candidate_ids)
    assert manifest["entries"][2]["status"] == "KEEP"
    assert manifest["entries"][2]["favourite"] is True
    with Image.open(BytesIO(result.review.pages[0].jpeg_bytes)) as review_page:
        red, green, blue = review_page.convert("RGB").getpixel((2, 2))
    assert red > 120 and green < 80 and blue < 80


def test_unavailable_pixels_cannot_actuate_record_or_cull_but_visible_sibling_still_culls(
    tmp_path: Path,
) -> None:
    """A visual nobody can see is never asked about, and never blocks its sibling.

    It leaves before the sheet is built, so it occupies no numbered square and
    the tiles renumber around it. It survives Pass 1 unjudged rather than being
    removed on evidence that does not exist.
    """
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    unavailable = make_asset("unavailable", file_created_at=when)
    visible = make_asset("visible", file_created_at=when + timedelta(seconds=1))

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "One placeholder and one visible frame.",
                        "representative_tiles": [1],
                        "representative_reason": "The only tile with source pixels.",
                    }
                ],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [1]}],
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; source, atlas, bank, Cull, and review stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (unavailable, visible),
                preview_jpeg=lambda asset: None if asset.id == "unavailable" else _jpeg("green"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert _survivor_ids(result.pass_one) == ("unavailable",)
    assert tuple(decision.asset_id for decision in result.pass_one.rejected) == ("visible",)
    # It is reported as unseeable, not as a decision that had to be thrown away:
    # Cull was never shown it and so could not name it.
    assert any("visual unavailable: unavailable" in item for item in result.pass_one.warnings)
    assert not any("unavailable Cull decision" in item for item in result.pass_one.warnings)
    assert tuple(entry.status for entry in result.pass_one.review.entries) == ("KEEP", "CULL")
    assert result.pass_one.review.warnings == result.pass_one.warnings
    with Image.open(BytesIO(result.pass_one.review.pages[0].jpeg_bytes)) as review_page:
        red, green, blue = review_page.convert("RGB").getpixel((2, 2))
    assert red > 120 and green < 80 and blue < 80


def test_preview_exception_is_one_unavailable_tile_and_does_not_abort_visible_sibling(
    tmp_path: Path,
) -> None:
    """A failed preview read stays local to its asset and remains owner-visible."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    broken = make_asset("broken", file_created_at=when)
    visible = make_asset("visible", file_created_at=when + timedelta(seconds=1))
    attached_images: list[tuple[bytes, ...]] = []

    def preview(asset):
        if asset.id == "broken":
            raise RuntimeError("generated preview failure")
        return _jpeg("blue")

    async def _answer(_prompt, _config, **kwargs):
        attached_images.append(tuple(kwargs["images"]))
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "A placeholder beside one visible frame.",
                        "representative_tiles": [2],
                        "representative_reason": "The second tile retains source pixels.",
                    }
                ],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; preview containment and the rest of the flow are real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (broken, visible),
                preview_jpeg=preview,
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert result.prepared.candidate_ids == ("broken", "visible")
    assert _survivor_ids(result.pass_one) == ("broken", "visible")
    assert result.pass_zero.atlas.tile_for("broken").kind == "unavailable"
    assert result.pass_zero.atlas.tile_for("broken").unavailable_reason == (
        "preview provider raised RuntimeError and no usable motion frames"
    )
    assert result.pass_zero.atlas.tile_for("visible").kind == "photo"
    assert len(attached_images) == 1
    assert attached_images[0]
    assert any("visual unavailable: broken" in warning for warning in result.pass_one.warnings)
    assert result.pass_one.review.warnings == result.pass_one.warnings
    with Image.open(BytesIO(result.pass_one.review.pages[0].jpeg_bytes)) as review_page:
        red, green, blue = review_page.convert("RGB").getpixel((2, 2))
    assert red > 120 and green < 80 and blue < 80


def test_owner_banner_uses_conservation_warning_recorded_during_pass_one(
    tmp_path: Path,
) -> None:
    """The review reads final trace warnings, including ones born while recording Cull."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    first = make_asset("first", file_created_at=when)
    second = make_asset("second", file_created_at=when + timedelta(seconds=1))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (first, second),
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
        return json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [
                    {
                        "episode": 1,
                        "page": 1,
                        "visual_summary": "Two generated frames.",
                        "representative_tiles": [2],
                        "representative_reason": "The second frame remains visible.",
                    }
                ],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [1]}],
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; the duplicated bank is injected local trace input.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )
    duplicated_pack = replace(
        pass_zero,
        episode_packs=(pass_zero.episode_packs[0], pass_zero.episode_packs[0]),
    )

    result = run_cull(prepared, duplicated_pack, review_output_dir=tmp_path / "review")

    assert result.trace.conservation is not None
    assert result.trace.conservation.valid is False
    assert "conservation failure in pass-1-cull" in prepared.trace.warnings
    assert "!! conservation failure in pass-1-cull" in result.warnings
    assert result.review.warnings == result.warnings
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
                "schema_version": "episode-scan-v4",
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
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [1]}],
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
            limits=VisionRequestLimits(max_output_tokens=170, timeout_seconds=30),
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
        "!! Pass 0 1 of 3 episodes could not be read",
        # the wall is now attempted from the two that read, and this fixture's
        # provider has no period answer for it
        "!! Pass 0 period synthesis unreadable; thesis unavailable",
        f"!! Pass 1 mismatched episode scan provenance: {pass_zero.episode_packs[0].page.sheet_id}",
        f"!! Pass 1 failed episode scan: {pass_zero.episode_packs[1].page.sheet_id}",
        f"!! Pass 1 mismatched episode scan provenance: {pass_zero.episode_packs[2].page.sheet_id}",
    )
    # three episode packs plus the period wall, which is now attempted
    assert len(prepared.trace.requests) == 4
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
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": [{"episode": 1, "page": 1, "visual_summary": "missing"}],
                "cull_rejects": [{"episode": 1, "notes": [], "failed": [2]}],
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
    assert tuple(decision.asset_id for decision in result.rejected) == ("bad",)


def test_a_missing_cull_namespace_warns_and_removes_nothing(
    tmp_path: Path,
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
            "schema_version": "episode-scan-v4",
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
            "cull_rejects": [{"episode": 1, "notes": [], "failed": [2]}],
        }
        del payload["cull_rejects"]
        return json.dumps(payload)

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; namespace parsing and owner warnings stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        pass_zero = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    result = run_cull(
        prepared,
        pass_zero,
        review_output_dir=tmp_path / "review",
    )

    assert result.warnings[0].startswith("!! Pass 1 invalid Cull namespace: ")
    assert result.rejected == ()
    assert _survivor_ids(result) == ("record", "bad")


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
                    "schema_version": "episode-scan-v4",
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
                    "cull_rejects": [
                        {
                            "episode": 1,
                            "notes": [],
                            "failed": list(range(1, reject_count + 1)),
                        }
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


def test_multi_page_review_reuses_one_atlas_across_every_page(
    tmp_path: Path,
) -> None:
    """Pregnancy proof and an arbitrary ticket survive by visible function, not keywords."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.selection_cull import run_cull

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(
            f"asset-{index:03d}",
            file_created_at=when + timedelta(seconds=index),
            is_favorite=index == 0,
        )
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
                "schema_version": "episode-scan-v4",
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
                    [{"episode": 1, "notes": [], "failed": [3]}] if first_page else []
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
    assert "asset-000" in _survivor_ids(result)
    assert "asset-001" in _survivor_ids(result)
    assert "asset-002" not in _survivor_ids(result)
    assert len(result.review.pages) == 2
    assert tuple(entry.asset_id for entry in result.review.entries) == prepared.candidate_ids
    assert tuple(entry.number for entry in result.review.entries) == tuple(range(1, 122))
    assert [result.review.entries[index].status for index in range(3)] == [
        "KEEP",
        "KEEP",
        "CULL",
    ]
    manifest = json.loads(result.review.manifest_path.read_text())
    # A kept visual carries no reason: Cull only explains what it removed.
    assert manifest["entries"][0]["reason"] is None
    assert manifest["entries"][2]["reason"] == "failed: the picture did not come out"
    assert manifest["warnings"] == []

    from immich_memories.analysis.contact_sheets import sheet_layout

    first_columns, first_tile = sheet_layout(120)
    first_grid_height = ((120 + first_columns - 1) // first_columns) * first_tile
    with Image.open(BytesIO(result.review.pages[0].jpeg_bytes)) as first_page:
        assert first_page.height > first_grid_height
        _assert_pixel_near(first_page, (4, 4), (0, 0, 0))
        _assert_pixel_near(first_page, (first_tile - 5, 5), (180, 130, 0))
        _assert_pixel_near(first_page, (5, first_tile - 5), (45, 65, 75))
        _assert_pixel_near(first_page, (first_tile + 5, first_tile - 5), (45, 65, 75))
        _assert_pixel_near(first_page, (2 * first_tile + 5, first_tile - 5), (170, 20, 20))
        _assert_pixel_near(first_page, (3 * first_tile + 5, first_tile - 5), (45, 65, 75))
        state_top = first_tile - max(18, min(26, first_tile // 6)) - 3
        assert state_top > 3 + 16
        footer = first_page.convert("RGB").crop(
            (0, first_grid_height, first_page.width, first_page.height)
        )
        assert footer.getbbox() is not None
        assert len(footer.getcolors(maxcolors=1_000_000) or ()) > 2

    second_columns, second_tile = sheet_layout(1)
    second_grid_height = ((1 + second_columns - 1) // second_columns) * second_tile
    with Image.open(BytesIO(result.review.pages[1].jpeg_bytes)) as second_page:
        assert second_page.height > second_grid_height
        _assert_pixel_near(second_page, (4, 4), (0, 0, 0))
        _assert_pixel_near(second_page, (5, second_tile - 5), (45, 65, 75))


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
                    "schema_version": "episode-scan-v4",
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
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
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


def test_arbitrary_unrelated_topics_share_the_same_production_flow_without_quotas(
    tmp_path: Path,
) -> None:
    """Topic labels remain evidence; they never select a branch or membership policy."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull
    from immich_memories.api.models import VideoClipInfo

    when = datetime(2026, 8, 25, 8, tzinfo=UTC)
    sources = (
        VideoClipInfo(
            asset=make_asset("glass", file_created_at=when),
            duration_seconds=2.0,
            width=1920,
            height=1080,
            llm_category="glassblowing",
        ),
        VideoClipInfo(
            asset=make_asset("birds", file_created_at=when + timedelta(hours=3)),
            duration_seconds=2.0,
            width=1920,
            height=1080,
            llm_category="birdwatching",
        ),
    )
    prompts: list[str] = []

    async def _answer(prompt, _config, **kwargs):
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            scopes = re.findall(r"episode=(\d+) page=(\d+) tiles=\[([^\]]+)\]", prompt)
            return json.dumps(
                {
                    "schema_version": "episode-scan-v4",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": int(episode),
                            "page": int(page),
                            "visual_summary": "An arbitrary visible activity.",
                            "representative_tiles": [int(tiles.split(",")[0])],
                            "representative_reason": "The first tile identifies its episode.",
                        }
                        for episode, page, tiles in scopes
                    ],
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
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
                    "unavailable_reason": "Unrelated activities support no single thesis.",
                },
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the sole provider boundary; legacy quota APIs are forbidden here.
    with (
        patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer),
        patch(
            "immich_memories.analysis.subject_policy.apply_subject_quotas",
            side_effect=AssertionError("legacy quota called"),
        ),
        patch(
            "immich_memories.analysis.subject_policy.filter_candidates_by_subject",
            side_effect=AssertionError("legacy filter called"),
        ),
    ):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: sources,
                preview_jpeg=lambda _asset: _jpeg("orange"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert result.prepared.candidate_ids == ("glass", "birds")
    assert _survivor_ids(result.pass_one) == ("glass", "birds")
    assert tuple(
        candidate.grounded_annotations[-1] for candidate in result.prepared.candidates
    ) == (
        "subject:glassblowing",
        "subject:birdwatching",
    )
    episode_prompt = next(prompt for prompt in prompts if "chronological episode pack" in prompt)
    assert "subject:glassblowing" in episode_prompt
    assert "subject:birdwatching" in episode_prompt
    assert [request.provenance.pass_name for request in result.prepared.trace.requests] == [
        "episode-scan",
        "period-insight",
    ]
    assert sum(request.actual_calls for request in result.prepared.trace.requests) == 2


def test_reencode_reaches_fused_visual_request_with_pixels_and_empty_cull_keeps_it(
    tmp_path: Path,
) -> None:
    """A weak metadata prior remains provider-visible evidence, never a source deletion."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull
    from immich_memories.api.models import VideoClipInfo

    clip = VideoClipInfo(
        asset=make_asset("reencode", exif_make=None, exif_model=None, duration="0:00:01.250"),
        duration_seconds=1.25,
        width=640,
        height=480,
    )
    preview_output = BytesIO()
    Image.new("RGB", (200, 150), "red").save(preview_output, "JPEG")
    preview_bytes = preview_output.getvalue()
    provider_prompts: list[str] = []
    attached_images: list[tuple[bytes, ...]] = []

    async def _answer(prompt, _config, **kwargs):
        provider_prompts.append(prompt)
        attached_images.append(tuple(kwargs["images"]))
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return json.dumps(
                {
                    "schema_version": "episode-scan-v4",
                    "pack": 1,
                    "episode_readings": [
                        {
                            "episode": 1,
                            "page": 1,
                            "visual_summary": "One visible generated clip frame.",
                            "representative_tiles": [1],
                            "representative_reason": "The source pixels are visible.",
                        }
                    ],
                    "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
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
                    "unavailable_reason": "One visual has no period thesis.",
                },
            }
        )

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; source annotations and attached sheets stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (clip,),
                preview_jpeg=lambda _asset: preview_bytes,
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert result.prepared.candidate_ids == ("reencode",)
    assert "reencode-suspected" in result.prepared.candidates[0].grounded_annotations
    assert _survivor_ids(result.pass_one) == ("reencode",)
    assert result.pass_one.rejected == ()
    episode_index = next(
        index
        for index, prompt in enumerate(provider_prompts)
        if "chronological episode pack" in prompt
    )
    assert "reencode-suspected" in provider_prompts[episode_index]
    assert len(attached_images[episode_index]) == 1
    with Image.open(BytesIO(attached_images[episode_index][0])) as sheet:
        red, _green, blue = sheet.convert("RGB").getpixel((100, 75))
    assert red > blue + 50


def test_explicit_provider_refusal_is_fail_open_and_owner_visible(tmp_path: Path) -> None:
    """A refusal is evidence failure, never permission to invent a Pass 1 decision."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.selection_flow import run_editorial_insight_cull

    asset = make_asset("safe")

    async def _refuse(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return "I cannot evaluate these visuals."

    def gateway_factory(trace):
        return VisualEditorialGateway(
            llm_config=LLMConfig(model="vision-test"),
            cache_path=tmp_path / "judgments.db",
            trace=trace,
        )

    # WHY: query_llm is the provider boundary; refusal handling and owner output stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_refuse):
        result = run_editorial_insight_cull(
            EditorialSelectionRequest(scope=SourceScope()),
            EditorialDependencies(
                source_fetcher=lambda _scope: (asset,),
                preview_jpeg=lambda _asset: _jpeg("green"),
            ),
            gateway_factory=gateway_factory,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            review_output_dir=tmp_path / "review",
        )

    assert _survivor_ids(result.pass_one) == ("safe",)
    assert result.pass_one.rejected == ()
    assert any("Pass 1 unreadable episode scan" in item for item in result.pass_one.warnings)
    assert result.pass_one.review.warnings == result.pass_one.warnings
    assert result.prepared.trace.warnings == list(result.pass_one.warnings)
    assert len(result.prepared.trace.requests) == 1
    assert result.prepared.trace.requests[0].actual_calls == 1
