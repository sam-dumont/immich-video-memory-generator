"""Pass 0 reads the exact prepared visual corpus before making any cut."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from immich_memories.analysis.editorial_contracts import SourceEvidence
from immich_memories.analysis.selection_flow import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), colour).save(output, "JPEG")
    return output.getvalue()


def _episode_pack_answer(prompt: str, *, summary: str = "Visible stages develop.") -> str:
    scopes = re.findall(
        r"episode=(\d+) page=(\d+) tiles=\[([^\]]+)\]",
        prompt,
    )
    assert scopes
    readings = []
    for episode_alias, page_alias, displayed in scopes:
        numbers = [int(value) for value in displayed.split(",")]
        readings.append(
            {
                "episode": int(episode_alias),
                "page": int(page_alias),
                "visual_summary": summary,
                "representative_tiles": numbers[:3],
                "representative_reason": "These visible stages distinguish the episode.",
            }
        )
    return json.dumps(
        {
            "schema_version": "episode-scan-v2",
            "pack": 1,
            "episode_readings": readings,
        },
        separators=(",", ":"),
    )


def test_source_preparation_preserves_real_visual_sources_in_candidate_order(
    tmp_path: Path,
) -> None:
    """Only source-eligible, coalesced assets acquire previews and retain local motion."""
    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    photo = make_asset("photo", file_created_at=noon)
    photo.type = AssetType.IMAGE
    video_asset = make_asset("video", file_created_at=noon + timedelta(minutes=1))
    video = VideoClipInfo(
        asset=video_asset,
        local_path=str(tmp_path / "video.mp4"),
        duration_seconds=4.0,
        width=1920,
        height=1080,
    )
    excluded = make_asset("excluded", file_created_at=noon + timedelta(minutes=2))
    preview_calls: list[str] = []

    def preview_jpeg(asset) -> bytes:
        preview_calls.append(asset.id)
        return _jpeg("red" if asset.id == "photo" else "blue")

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            scope=SourceScope(),
            owner_excluded_asset_ids=("excluded",),
        ),
        EditorialDependencies(
            source_fetcher=lambda _scope: (excluded, video_asset, photo, video),
            preview_jpeg=preview_jpeg,
        ),
    )

    assert prepared.candidate_ids == ("photo", "video")
    assert tuple(source.asset.id for source in prepared.visual_sources) == prepared.candidate_ids
    assert preview_calls == ["photo", "video"]
    assert prepared.visual_sources[0].preview_jpeg == _jpeg("red")
    assert prepared.visual_sources[1].motion_path == tmp_path / "video.mp4"


def test_compact_episode_v2_request_cannot_reuse_a_legacy_v1_bank(
    tmp_path: Path,
) -> None:
    """Changing the wire aliases abandons the old answer even with identical visual evidence."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (make_asset("asset", file_created_at=when),),
            preview_jpeg=lambda _asset: _jpeg("red"),
        ),
    )

    class CaptureAndFail:
        request = None

        def ask(self, request):
            if self.request is None:
                self.request = request
            raise RuntimeError("capture only")

    capture = CaptureAndFail()
    run_period_insight(
        prepared,
        requester=capture,
        sheet_output_dir=tmp_path / "capture-sheets",
        frame_cache_dir=None,
    )
    current = capture.request
    assert current is not None
    assert (
        current.pass_version,
        current.prompt_version,
        current.schema_version,
    ) == ("episode-scan-v2", "episode-scan-prompt-v2", "episode-scan-v2")
    legacy = replace(
        current,
        pass_version="episode-scan-v1",  # noqa: S106 - historical pass identity fixture
        prompt_version="episode-scan-prompt-v1",
        schema_version="episode-scan-v1",
    )
    calls: list[str] = []

    async def answer(_prompt, _config, **kwargs):
        calls.append("physical")
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"legacy-or-current":"banked"}'

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; the cache and exact generated page stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=answer):
        legacy_answer = gateway.ask(legacy)
        calls.clear()
        current_answer = gateway.ask(current)
        reused_current = gateway.ask(current)

    assert calls == ["physical"]
    assert current_answer.provenance.cache_hit is False
    assert current_answer.request_trace.actual_calls == 1
    assert current_answer.provenance.request_key != legacy_answer.provenance.request_key
    assert reused_current.provenance.cache_hit is True
    assert reused_current.request_trace.actual_calls == 0


def test_task5_provider_prompt_maps_source_evidence_to_compact_visual_aliases(
    tmp_path: Path,
) -> None:
    """The model sees the same concise tile evidence that keys the visual request."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    stable_asset_id = "asset-" + "a" * 64
    photo = make_asset(
        stable_asset_id,
        is_favorite=True,
        file_created_at=when,
        duration=None,
    )
    photo.type = AssetType.IMAGE
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (photo,),
            preview_jpeg=lambda _asset: _jpeg("blue"),
            source_evidence=lambda _source: SourceEvidence(
                blur=0.125,
                similarity="source-one",
            ),
        ),
    )
    prompts: list[str] = []

    async def answer(prompt, _config, **kwargs):
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return _episode_pack_answer(prompt)
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": None,
                    "evidence": [],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": "One visual does not support a period thesis.",
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; source prep, atlas, sheets, and gateway stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=answer):
        run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert len(prompts) == 2
    episode_grounding = prompts[0].split("Grounded annotations (ordered JSON):\n", 1)[1]
    assert episode_grounding == (
        '["tile:1 | episode:1 | taken:2026-08-25T12:00:00+00:00 | media:photo | '
        'favourite:true | blur:0.125 | similarity:source-one"]'
    )
    assert stable_asset_id not in episode_grounding
    assert "Grounded annotations (ordered JSON):" in prompts[1]
    assert "representatives:" in prompts[1]


def test_incomplete_121_tile_episode_banks_valid_page_but_blocks_period_thesis(
    tmp_path: Path,
) -> None:
    """Page-one success cannot conceal an unread continuation or remove its assets."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    captured_images: list[tuple[bytes, ...]] = []
    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(make_asset(f"asset-{number:03d}", file_created_at=when) for number in range(121))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("purple"),
        ),
    )
    trace = prepared.trace

    async def _answer(prompt, _config, **kwargs):
        captured_images.append(kwargs["images"])
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        complete = _episode_pack_answer(prompt, summary="A dense day continues.")
        return complete if len(captured_images) == 1 else complete[:-1]

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=trace,
    )
    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway, and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
            limits=VisionRequestLimits(max_output_tokens=2400, timeout_seconds=90),
        )

    episode = result.episode_sheets[0]
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [120, 1]
    assert tuple(ref.number for ref in episode.pages[1].tile_refs) == (121,)
    assert captured_images == [(episode.pages[0].jpeg_bytes,), (episode.pages[1].jpeg_bytes,)]
    assert [observation.reading is not None for observation in result.page_observations] == [
        True,
        False,
    ]
    assert result.retained_ids == prepared.candidate_ids
    assert result.insight.thesis is None
    assert result.period_pages == ()
    assert len(result.banked_scans) == 2
    assert sum(request.actual_calls for request in trace.requests) == 2
    assert len([item for item in trace.editorial_passes if item.name == "pass-0"]) == 1
    assert trace.editorial_passes[-1].provenance.sheet_hashes == tuple(
        page.sha256 for page in episode.pages
    )
    assert any(
        warning.startswith("!! Pass 0 incomplete visual evidence") for warning in result.warnings
    )
    assert trace.as_dict()["warnings"] == [
        "!! Pass 0 incomplete visual evidence; period thesis unavailable"
    ]
    assert trace.report().count("!! Pass 0 incomplete visual evidence") == 1


def test_complete_episode_builds_one_pixel_bearing_period_wall_and_visual_trace(
    tmp_path: Path,
) -> None:
    """The period thesis sees saved representative pixels and records their exact bytes."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = (
        make_asset("effort", file_created_at=when),
        make_asset("medal", file_created_at=when + timedelta(minutes=1)),
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda asset: _jpeg("red" if asset.id == "effort" else "gold"),
        ),
    )
    captured_images: list[tuple[bytes, ...]] = []

    async def _answer(prompt, _config, **kwargs):
        captured_images.append(kwargs["images"])
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if len(captured_images) == 1:
            return _episode_pack_answer(prompt, summary="Effort leads to a medal.")
        return (
            '{"schema_version":"period-insight-v1","period_insight":{'
            '"thesis":"Effort becomes celebration.",'
            '"evidence":[{"observation":"Action resolves in a medal.",'
            '"representative_tiles":[1,2]}],'
            '"tensions":["effort versus reward"],'
            '"recurring_threads":["movement"],"unavailable_reason":null}}'
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the only external provider boundary; encoded visual evidence stays real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert result.insight.thesis == "Effort becomes celebration."
    assert result.warnings == ()
    assert len(result.period_pages) == 1
    period_page = result.period_pages[0]
    assert captured_images[-1] == (period_page.jpeg_bytes,)
    assert period_page.path.read_bytes() == period_page.jpeg_bytes
    assert prepared.trace.requests[-1].attached_sheet_hashes == (period_page.sha256,)
    assert result.insight.evidence[0].asset_ids == ("effort", "medal")
    pass_zero = [item for item in prepared.trace.editorial_passes if item.name == "pass-0"]
    assert len(pass_zero) == 1
    assert pass_zero[0].provenance.sheet_hashes == (
        result.episode_sheets[0].pages[0].sha256,
        period_page.sha256,
    )
    assert sum(item.actual_calls for item in pass_zero[0].request_traces) == 0
    assert sum(item.actual_calls for item in prepared.trace.requests) == 2
    assert prepared.trace.story_of("effort").first_pass == "pass-0"  # noqa: S105


def test_multi_page_period_wall_is_not_split_without_an_approved_limit(tmp_path: Path) -> None:
    """A default one-page budget yields no independent partial-period theses."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(
            f"visual-{episode:02d}-{member}",
            file_created_at=start + timedelta(hours=episode * 2, seconds=member),
        )
        for episode in range(41)
        for member in range(3)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("teal"),
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _episode_pack_answer(prompt, summary="Three related unfamiliar visuals.")

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the only external provider boundary; request planning remains real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert calls == 2
    assert [len(pack.scopes) for pack in result.episode_packs] == [40, 1]
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [120, 3]
    assert len(result.period_pages) == 2
    assert result.period_answer is None
    assert result.insight.thesis is None
    assert result.retained_ids == prepared.candidate_ids
    assert result.warnings == ("!! Pass 0 period synthesis unreadable; thesis unavailable",)


def test_approved_multi_page_period_wall_is_attached_as_one_holistic_request(
    tmp_path: Path,
) -> None:
    """A probed two-page limit changes packing, never the single-thesis boundary."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(
            f"visual-{episode:02d}-{member}",
            file_created_at=start + timedelta(hours=episode * 2, seconds=member),
        )
        for episode in range(41)
        for member in range(3)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )
    captured_images: list[tuple[bytes, ...]] = []

    async def _answer(prompt, _config, **kwargs):
        captured_images.append(kwargs["images"])
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "complete chronological period wall" in prompt:
            return (
                '{"schema_version":"period-insight-v1","period_insight":{'
                '"thesis":"Unfamiliar stages accumulate into a progression.",'
                '"evidence":[{"observation":"The beginning contrasts with the final stage.",'
                '"representative_tiles":[{"page_id":"period-wall-001","tile":1},'
                '{"page_id":"period-wall-002","tile":121}]}],'
                '"tensions":[],"recurring_threads":["progression"],'
                '"unavailable_reason":null}}'
            )
        return _episode_pack_answer(prompt, summary="Three visual stages.")

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the only external provider boundary; packing and encoded pages stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
            limits=VisionRequestLimits(
                max_pages_per_request=2,
                max_output_tokens=4000,
                timeout_seconds=120,
            ),
        )

    assert len(captured_images) == 3
    assert captured_images[-1] == tuple(page.jpeg_bytes for page in result.period_pages)
    assert result.insight.thesis == "Unfamiliar stages accumulate into a progression."
    assert result.insight.evidence[0].asset_ids == ("visual-00-0", "visual-40-0")
    assert prepared.trace.requests[-1].attached_sheet_hashes == tuple(
        page.sha256 for page in result.period_pages
    )
    assert result.warnings == ()


def test_interleaved_episodes_share_one_chronological_pack_with_explicit_membership(
    tmp_path: Path,
) -> None:
    """Parallel place threads render A/B/A while each complete episode stays identifiable."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    a_early = make_asset("a-early", file_created_at=noon)
    b = make_asset("b", file_created_at=noon + timedelta(minutes=3))
    a_late = make_asset("a-late", file_created_at=noon + timedelta(minutes=6))
    for asset, location in (
        (a_early, (50.437, 5.971)),
        (b, (50.878, 4.326)),
        (a_late, (50.437, 5.971)),
    ):
        assert asset.exif_info is not None
        asset.exif_info.latitude, asset.exif_info.longitude = location
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (a_early, b, a_late),
            preview_jpeg=lambda _asset: _jpeg("silver"),
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if calls == 1:
            return _episode_pack_answer(prompt)
        return (
            '{"schema_version":"period-insight-v1","period_insight":{'
            '"thesis":null,"evidence":[],"tensions":[],"recurring_threads":[],'
            '"unavailable_reason":"No credible period thesis from this small corpus."}}'
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the only external provider boundary; pack layout and mapping stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    pack = result.episode_packs[0]
    assert tuple(ref.entity_id for ref in pack.page.tile_refs) == ("a-early", "b", "a-late")
    assert tuple(tuple(ref.entity_id for ref in scope.tile_refs) for scope in pack.scopes) == (
        ("a-early", "a-late"),
        ("b",),
    )
    assert tuple(ref.entity_id for ref in result.period_pages[0].tile_refs) == (
        "a-early",
        "b",
        "a-late",
    )
    assert len(result.episode_readings) == 2
    assert result.insight.thesis is None
    assert result.warnings == ()


def test_packer_keeps_a_four_tile_episode_whole_at_the_120_tile_boundary(
    tmp_path: Path,
) -> None:
    """Thirty-nine triples fill 117 tiles; the following four stay together in pack two."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(
            f"triple-{episode:02d}-{member}",
            file_created_at=start + timedelta(hours=episode * 2, seconds=member),
        )
        for episode in range(39)
        for member in range(3)
    ) + tuple(
        make_asset(
            f"quad-{member}",
            file_created_at=start + timedelta(hours=78, seconds=member),
        )
        for member in range(4)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("olive"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "complete chronological period wall" in prompt:
            return (
                '{"schema_version":"period-insight-v1","period_insight":{'
                '"thesis":null,"evidence":[],"tensions":[],"recurring_threads":[],'
                '"unavailable_reason":"No single thesis."}}'
            )
        return _episode_pack_answer(prompt)

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; the real packer owns group boundaries.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [117, 4]
    assert tuple(ref.entity_id for ref in result.episode_packs[1].scopes[0].tile_refs) == (
        "quad-0",
        "quad-1",
        "quad-2",
        "quad-3",
    )
    mapped_ids = tuple(
        ref.entity_id
        for pack in result.episode_packs
        for scope in pack.scopes
        for ref in scope.tile_refs
    )
    assert Counter(mapped_ids) == Counter(prepared.candidate_ids)
    assert len(result.page_observations) == len(prepared.episode_groups) == 40
    assert (
        len(
            [
                request
                for request in prepared.trace.requests
                if request.provenance.pass_name == "episode-scan"  # noqa: S105
            ]
        )
        == 2
    )


def test_singleton_episode_packs_fit_their_complete_response_budget(tmp_path: Path) -> None:
    """Many tiny episodes split before their required JSON can exceed the token envelope."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(
            f"singleton-{number:03d}",
            file_created_at=start + timedelta(hours=number * 2),
        )
        for number in range(120)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("maroon"),
        ),
    )
    response_envelopes: list[tuple[int, int, int]] = []

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "complete chronological period wall" in prompt:
            return (
                '{"schema_version":"period-insight-v1","period_insight":{'
                '"thesis":null,"evidence":[],"tensions":[],"recurring_threads":[],'
                '"unavailable_reason":"No honest thesis."}}'
            )
        scopes = re.findall(
            r"episode=(\d+) page=(\d+) tiles=\[([^\]]+)\]",
            prompt,
        )
        assert scopes
        readings = []
        for episode_alias, page_alias, displayed in scopes:
            readings.append(
                {
                    "episode": int(episode_alias),
                    "page": int(page_alias),
                    "visual_summary": "s" * 64,
                    "representative_tiles": [int(value) for value in displayed.split(",")],
                    "representative_reason": "r" * 96,
                }
            )
        raw = json.dumps(
            {
                "schema_version": "episode-scan-v2",
                "pack": 1,
                "episode_readings": readings,
            },
            separators=(",", ":"),
        )
        response_envelopes.append((len(scopes), len(raw), kwargs["max_tokens"] * 3))
        return raw

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; source grouping, packing, pages, and parsing stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert [item[0] for item in response_envelopes] == [46, 46, 28]
    assert all(
        response_chars <= budget_chars for _, response_chars, budget_chars in response_envelopes
    )
    assert Counter(reading.episode_id for reading in result.episode_readings) == Counter(
        group.group_id for group in prepared.episode_groups
    )
    assert Counter(
        ref.entity_id
        for pack in result.episode_packs
        for scope in pack.scopes
        for ref in scope.tile_refs
    ) == Counter(prepared.candidate_ids)
    assert result.retained_ids == prepared.candidate_ids
    assert len(result.episode_readings) == 120


def test_unavailable_atlas_tile_blocks_a_claimed_episode_and_period_thesis(
    tmp_path: Path,
) -> None:
    """A numbered placeholder is not visual evidence the model may promote as a representative."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = (
        make_asset("visible", file_created_at=start),
        make_asset("missing-pixels", file_created_at=start + timedelta(seconds=1)),
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda asset: _jpeg("plum") if asset.id == "visible" else None,
        ),
    )
    calls = 0

    async def _claim(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if calls == 1:
            return _episode_pack_answer(prompt, summary="Both numbered tiles are visible.")
        return (
            '{"schema_version":"period-insight-v1","period_insight":{'
            '"thesis":"The missing tile proves a story.",'
            '"evidence":[{"observation":"Invented evidence.","representative_tiles":[2]}],'
            '"tensions":[],"recurring_threads":[],"unavailable_reason":null}}'
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; unavailable atlas evidence stays real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_claim):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert calls == 1
    assert result.episode_readings == ()
    assert result.insight.thesis is None
    assert result.retained_ids == prepared.candidate_ids
    assert result.warnings == ("!! Pass 0 incomplete visual evidence; period thesis unavailable",)


def test_empty_prepared_corpus_records_pass_zero_without_visual_calls(tmp_path: Path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.period_insight import run_period_insight

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: ()),
    )
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )

    result = run_period_insight(
        prepared,
        requester=gateway,
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )

    assert result.retained_ids == ()
    assert result.actual_calls == 0
    assert result.insight.thesis is None
    assert result.insight.unavailable_reason == "source corpus was empty"
    assert result.warnings == ()
    assert [item.name for item in prepared.trace.editorial_passes] == [
        "source-eligibility",
        "pass-0",
    ]


def test_one_packed_raw_answer_banks_valid_siblings_when_one_episode_is_invalid(
    tmp_path: Path,
) -> None:
    """Pack-level storage and episode-level completeness are separate contracts."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 8, 25, 8, tzinfo=UTC)
    assets = tuple(
        make_asset(label, file_created_at=start + timedelta(hours=index * 2))
        for index, label in enumerate(("asset-a", "asset-b", "asset-c"))
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("cyan"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        scopes = re.findall(r"episode=(\d+) page=(\d+)", prompt)
        assert len(scopes) == 3
        entries = [
            {
                "episode": int(episode_alias),
                "page": int(page_alias),
                "visual_summary": f"Visible episode {episode_alias}",
                "representative_tiles": [99 if index == 1 else index + 1],
                "representative_reason": "The named tile is visible.",
            }
            for index, (episode_alias, page_alias) in enumerate(scopes)
        ]
        return json.dumps(
            {
                "schema_version": "episode-scan-v2",
                "pack": 1,
                "episode_readings": entries,
                "future_namespace": {"tile": True},
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; packed parsing and banking stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert len(result.banked_scans) == 1
    assert [item.reading is not None for item in result.page_observations] == [True, False, True]
    assert tuple(item.episode_id for item in result.episode_readings) == (
        prepared.episode_groups[0].group_id,
        prepared.episode_groups[2].group_id,
    )
    assert result.insight.thesis is None
    assert len(prepared.trace.requests) == 1


def test_complete_121_tile_episode_combines_every_continuation_provenance(
    tmp_path: Path,
) -> None:
    """One episode conclusion carries both physical page hashes and request keys."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"continuation-{number:03d}", file_created_at=when) for number in range(121)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("coral"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "complete chronological period wall" in prompt:
            return (
                '{"schema_version":"period-insight-v1","period_insight":{'
                '"thesis":null,"evidence":[],"tensions":[],"recurring_threads":[],'
                '"unavailable_reason":"No honest thesis."}}'
            )
        return _episode_pack_answer(prompt, summary="One chronological continuation.")

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; continuation provenance stays real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    reading = result.episode_readings[0]
    assert len(reading.page_provenances) == 2
    assert tuple(item.sheet_hashes for item in reading.page_provenances) == tuple(
        (pack.page.sha256,) for pack in result.episode_packs
    )
    assert tuple(item.request_key for item in reading.page_provenances) == tuple(
        scan.answer.request_trace.provenance.request_key for scan in result.banked_scans
    )
    assert len({item.request_key for item in reading.page_provenances}) == 2
    assert result.insight.thesis is None
    assert result.warnings == ()


def test_banked_episode_scan_can_be_reparsed_after_a_later_physical_failure(
    tmp_path: Path,
) -> None:
    """Task 6 can consume the first raw envelope without issuing another model request."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight
    from immich_memories.analysis.period_insight_answer import read_episode_answers

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"replay-{number:03d}", file_created_at=when) for number in range(121)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("indigo"),
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            kwargs["transport_observer"](LLMTransportAttempt(1, "connection_error", None))
            raise ConnectionError("synthetic continuation failure")
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _episode_pack_answer(prompt)

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the external provider; gateway failure tracing and raw replay stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert calls == 2
    assert len(result.banked_scans) == 1
    first_pack = result.episode_packs[0]
    banked = result.banked_scans[0]
    before = (
        len(prepared.trace.requests),
        sum(item.actual_calls for item in prepared.trace.requests),
        banked.answer.request_trace.provenance.request_key,
    )
    replay = read_episode_answers(
        banked.answer.raw_text,
        pack_alias=1,
        expected_observations=tuple(
            (scope.episode_id, scope.page_id) for scope in first_pack.scopes
        ),
        observation_map={
            (scope.episode_alias, scope.page_alias): (scope.episode_id, scope.page_id)
            for scope in first_pack.scopes
        },
        tile_map={
            (scope.episode_alias, scope.page_alias, ref.number): ref.entity_id
            for scope in first_pack.scopes
            for ref in scope.tile_refs
        },
    )
    after = (
        len(prepared.trace.requests),
        sum(item.actual_calls for item in prepared.trace.requests),
        banked.answer.request_trace.provenance.request_key,
    )

    assert replay is not None and replay.invalid_observations == ()
    assert before == after == (2, 2, banked.answer.provenance.request_key)
