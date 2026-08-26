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

import pytest
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
            "schema_version": "episode-scan-v4",
            "pack": 1,
            "episode_readings": readings,
            "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
        },
        separators=(",", ":"),
    )


# v4's envelope is a fraction of v3's per tile, so the split boundaries these
# tests exist to prove need a proportionally tighter budget to stay reachable.
_TIGHT_BUDGET = 1100
_SINGLE_EPISODE_BUDGET = 250


def _maximum_fused_response_for(
    displayed_by_episode: tuple[tuple[int, ...], ...],
) -> str:
    readings = [
        {
            "episode": episode,
            "page": 1,
            "visual_summary": "s" * 64,
            "representative_tiles": list(displayed),
            "representative_reason": "r" * 96,
        }
        for episode, displayed in enumerate(displayed_by_episode, start=1)
    ]
    # Worst case is every tile named once: a tile cannot sit in both buckets.
    cull_rejects = [
        {"episode": episode, "notes": list(displayed), "failed": []}
        for episode, displayed in enumerate(displayed_by_episode, start=1)
    ]
    return json.dumps(
        {
            "schema_version": "episode-scan-v4",
            "pack": 1,
            "episode_readings": readings,
            "cull_rejects": cull_rejects,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _maximum_fused_response(pack) -> str:
    return _maximum_fused_response_for(
        tuple(tuple(ref.number for ref in scope.tile_refs) for scope in pack.scopes)
    )


def _assert_next_episode_would_overflow(packs, *, max_output_tokens: int = 4000) -> None:
    """Prove each greedy pack stops for a reason: the budget, or the episode cap."""
    from immich_memories.analysis.period_insight import MAX_EPISODES_PER_PACK

    for pack, next_pack in zip(packs, packs[1:], strict=False):
        if len(pack.scopes) >= MAX_EPISODES_PER_PACK:
            continue
        displayed = [tuple(ref.number for ref in scope.tile_refs) for scope in pack.scopes]
        next_size = len(next_pack.scopes[0].tile_refs)
        first_new_number = len(pack.page.tile_refs) + 1
        displayed.append(tuple(range(first_new_number, first_new_number + next_size)))
        assert len(_maximum_fused_response_for(tuple(displayed))) > max_output_tokens * 3


def _assert_next_continuation_tile_would_overflow(packs, *, max_output_tokens: int = 4000) -> None:
    """Prove each non-final continuation is maximal under the fused response budget."""
    for pack in packs[:-1]:
        displayed = tuple(ref.number for ref in pack.scopes[0].tile_refs)
        assert (
            len(_maximum_fused_response_for(((*displayed, displayed[-1] + 1),)))
            > max_output_tokens * 3
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


def test_fused_episode_v4_request_cannot_reuse_a_legacy_v3_bank(
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
    ) == ("episode-scan-v4", "episode-scan-prompt-v4", "episode-scan-v4")
    legacy = replace(
        current,
        pass_version="episode-scan-v2",  # noqa: S106 - historical pass identity fixture
        prompt_version="episode-scan-prompt-v2",
        schema_version="episode-scan-v2",
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


def test_v4_episode_packs_budget_every_possible_cull_member(
    tmp_path: Path,
) -> None:
    """Maximum valid fused output, rather than an average reply, bounds every pack."""
    from immich_memories.analysis.episode_scan_request import build_episode_request
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index:03d}", file_created_at=start + timedelta(hours=index * 2))
        for index in range(120)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )

    class NoProvider:
        def ask(self, _request):
            raise TimeoutError("generated timeout")

    result = run_period_insight(
        prepared,
        requester=NoProvider(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )

    planned_ids = tuple(
        ref.entity_id for pack in result.episode_packs for ref in pack.page.tile_refs
    )
    assert planned_ids == prepared.candidate_ids
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [14] * 8 + [8]
    for pack in result.episode_packs:
        request = build_episode_request(pack, limits=VisionRequestLimits())
        assert request.pages == (pack.page,)
        assert len({ref.number for ref in request.pages[0].tile_refs}) == len(
            request.pages[0].tile_refs
        )
        assert len(_maximum_fused_response(pack)) <= 4000 * 3
    _assert_next_episode_would_overflow(result.episode_packs)


def test_v3_episode_request_rejects_duplicate_pack_local_tile_aliases(tmp_path: Path) -> None:
    """A future multi-page/alias layout must bump the v3 wire contract first."""
    from immich_memories.analysis.episode_scan_request import build_episode_request
    from immich_memories.analysis.period_insight import run_period_insight

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (make_asset("asset"),),
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )

    class NoProvider:
        def ask(self, _request):
            raise TimeoutError("generated timeout")

    result = run_period_insight(
        prepared,
        requester=NoProvider(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )
    pack = result.episode_packs[0]
    duplicate_page = replace(pack.page, tile_refs=(pack.page.tile_refs[0],) * 2)

    with pytest.raises(ValueError, match="unique pack-local tile numbers"):
        build_episode_request(
            replace(pack, page=duplicate_page),
            limits=VisionRequestLimits(),
        )


def test_one_sub_120_episode_response_splits_into_bounded_continuations(
    tmp_path: Path,
) -> None:
    """One large episode continues by response capacity even below the visual tile limit."""
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index:02d}", file_created_at=when + timedelta(seconds=index))
        for index in range(80)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("orange"),
        ),
    )

    class NoProvider:
        def ask(self, _request):
            raise TimeoutError("generated timeout")

    result = run_period_insight(
        prepared,
        requester=NoProvider(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
        limits=VisionRequestLimits(max_output_tokens=_SINGLE_EPISODE_BUDGET),
    )

    assert len(result.episode_sheets) == 1
    assert len(result.episode_packs) > 1
    assert tuple(
        ref.number for pack in result.episode_packs for ref in pack.page.tile_refs
    ) == tuple(range(1, 81))
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [65, 15]
    assert all(len(_maximum_fused_response(pack)) <= 4000 * 3 for pack in result.episode_packs)
    _assert_next_continuation_tile_would_overflow(
        result.episode_packs, max_output_tokens=_SINGLE_EPISODE_BUDGET
    )
    assert {pack.continuation_count for pack in result.episode_packs} == {len(result.episode_packs)}


def test_pass_zero_retains_the_atlas_and_failed_pack_provenance(tmp_path: Path) -> None:
    """A timeout keeps both reusable pixels and the exact failed request association."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    asset = make_asset("asset", file_created_at=datetime(2026, 8, 25, 12, tzinfo=UTC))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (asset,),
            preview_jpeg=lambda _asset: _jpeg("purple"),
        ),
    )
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )

    async def _timeout(*_args, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "timeout", None))
        raise TimeoutError("generated timeout")

    # WHY: query_llm is the provider boundary; atlas construction and trace association stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_timeout):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert result.atlas.tile_for("asset").jpeg_bytes == _jpeg("purple")
    assert len(result.scan_attempts) == 1
    assert result.scan_attempts[0].answer is None
    assert result.scan_attempts[0].request_trace is prepared.trace.requests[0]
    assert result.scan_attempts[0].pack_id == result.episode_packs[0].pack_id
    assert result.scan_attempts[0].page_id == result.episode_packs[0].page.sheet_id


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
        "favourite:true | subject-evidence:unknown | blur:0.125 | "
        'similarity:source-one"]'
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
            limits=VisionRequestLimits(
                max_output_tokens=_SINGLE_EPISODE_BUDGET, timeout_seconds=90
            ),
        )

    episode = result.episode_sheets[0]
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [65, 55, 1]
    _assert_next_continuation_tile_would_overflow(
        result.episode_packs, max_output_tokens=_SINGLE_EPISODE_BUDGET
    )
    assert tuple(ref.number for ref in episode.pages[-1].tile_refs) == (121,)
    assert captured_images == [(page.jpeg_bytes,) for page in episode.pages]
    assert [observation.reading is not None for observation in result.page_observations] == [
        True,
        False,
        False,
    ]
    assert result.retained_ids == prepared.candidate_ids
    assert result.insight.thesis is None
    assert result.period_pages == ()
    assert len(result.banked_scans) == 3
    assert sum(request.actual_calls for request in trace.requests) == 3
    assert len([item for item in trace.editorial_passes if item.name == "pass-0"]) == 1
    assert trace.editorial_passes[-1].provenance.sheet_hashes == tuple(
        page.sha256 for page in episode.pages
    )
    # its only episode is a continuation whose page is invalid, so nothing read
    assert any("no episode could be read" in warning for warning in result.warnings)
    assert trace.as_dict()["warnings"] == [
        "!! Pass 0 no episode could be read; period thesis unavailable"
    ]
    assert trace.report().count("!! Pass 0 no episode could be read") == 1


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


def test_a_period_wall_is_capped_rather_than_split(tmp_path: Path) -> None:
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
            limits=VisionRequestLimits(max_output_tokens=_TIGHT_BUDGET),
        )

    # four episode packs, a fifth, and the wall -- which is now capped to one
    # page rather than refused for needing two
    assert calls == 6
    assert [len(pack.scopes) for pack in result.episode_packs] == [10, 10, 10, 10, 1]
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [30, 30, 30, 30, 3]
    _assert_next_episode_would_overflow(result.episode_packs, max_output_tokens=_TIGHT_BUDGET)
    # capped to a single readable page, so the wall is asked rather than refused
    assert len(result.period_pages) == 1
    # this fixture answers only episode packs, so the wall has nothing to parse
    assert result.insight.thesis is None
    assert result.retained_ids == prepared.candidate_ids
    assert result.warnings == (
        "!! Pass 0 wall reads 60 of 123 representatives, spread across the period",
        "!! Pass 0 period synthesis unreadable; thesis unavailable",
    )


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


def test_packer_keeps_a_four_tile_episode_whole_at_the_v4_response_boundary(
    tmp_path: Path,
) -> None:
    """Response-bounded packs still keep the following four-tile episode whole."""
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
            limits=VisionRequestLimits(max_output_tokens=_TIGHT_BUDGET),
        )

    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [30, 30, 30, 31]
    assert [len(pack.scopes) for pack in result.episode_packs] == [10, 10, 10, 10]
    _assert_next_episode_would_overflow(result.episode_packs, max_output_tokens=_TIGHT_BUDGET)
    assert tuple(ref.entity_id for ref in result.episode_packs[-1].scopes[-1].tile_refs) == (
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
        == 4
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
        rejects = [
            {
                "episode": episode_alias,
                "notes": [int(value) for value in displayed.split(",")],
                "failed": [],
            }
            for episode_alias, _page_alias, displayed in scopes
        ]
        raw = json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "episode_readings": readings,
                "cull_rejects": rejects,
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
            limits=VisionRequestLimits(max_output_tokens=_TIGHT_BUDGET),
        )

    assert [item[0] for item in response_envelopes] == [10] * 12
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [10] * 12
    _assert_next_episode_would_overflow(result.episode_packs, max_output_tokens=_TIGHT_BUDGET)
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
                "schema_version": "episode-scan-v4",
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
    # the valid sibling still carries the wall, so the thesis is attempted
    assert len(prepared.trace.requests) == 2


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
            limits=VisionRequestLimits(max_output_tokens=_SINGLE_EPISODE_BUDGET),
        )

    reading = result.episode_readings[0]
    assert [len(pack.page.tile_refs) for pack in result.episode_packs] == [65, 55, 1]
    _assert_next_continuation_tile_would_overflow(
        result.episode_packs, max_output_tokens=_SINGLE_EPISODE_BUDGET
    )
    assert len(reading.page_provenances) == 3
    assert tuple(item.sheet_hashes for item in reading.page_provenances) == tuple(
        (pack.page.sha256,) for pack in result.episode_packs
    )
    assert tuple(item.request_key for item in reading.page_provenances) == tuple(
        scan.answer.request_trace.provenance.request_key for scan in result.banked_scans
    )
    assert len({item.request_key for item in reading.page_provenances}) == 3
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


def test_a_pack_holds_no_more_episodes_than_the_model_will_answer_about(
    tmp_path: Path,
) -> None:
    """Too many episodes on one sheet and the second half of the question dies.

    Measured on two real months at temperature 0. A 36-episode pack returned a
    complete, valid answer whose Cull lists were all empty -- silently, with no
    warning -- while the same month split into six smaller packs culled 4.6% and
    a dense month whose packs held 4 to 14 episodes culled 4.4%. The token
    budget alone does not bound this, so the pack bounds it directly.
    """
    from immich_memories.analysis.period_insight import (
        MAX_EPISODES_PER_PACK,
        run_period_insight,
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    # one asset per hour: every asset lands in its own episode
    assets = tuple(
        make_asset(f"asset-{index:03d}", file_created_at=start + timedelta(days=index))
        for index in range(MAX_EPISODES_PER_PACK * 2 + 3)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("teal"),
        ),
    )

    class NoProvider:
        def ask(self, _request):
            raise TimeoutError("packs only")

    result = run_period_insight(
        prepared,
        requester=NoProvider(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )

    assert len(prepared.episode_groups) == len(assets)
    assert all(len(pack.scopes) <= MAX_EPISODES_PER_PACK for pack in result.episode_packs)
    # still greedy: only the last pack is allowed to be short
    assert all(len(pack.scopes) == MAX_EPISODES_PER_PACK for pack in result.episode_packs[:-1])


def test_a_visual_nobody_can_see_is_not_asked_about(tmp_path: Path) -> None:
    """No pixels, no tile, no question — and it never reaches a sheet.

    An asset with no preview and no usable frames was carried onto the sheet as
    a numbered placeholder, which the model could then promote as a
    representative and which voided its whole episode's reading. Seventeen such
    assets in a real dense month cost the month its thesis. Nothing showable is
    a fact about the file, so it leaves before any pass is asked anything.
    """
    from immich_memories.analysis.period_insight import run_period_insight

    when = datetime(2026, 8, 25, 12, tzinfo=UTC)
    seen = make_asset("seen", file_created_at=when)
    blind = make_asset("blind", file_created_at=when + timedelta(seconds=30))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (seen, blind),
            # WHY: the preview provider is the external boundary; one asset has none.
            preview_jpeg=lambda asset: None if asset.id == "blind" else _jpeg("olive"),
        ),
    )

    class NoProvider:
        def ask(self, _request):
            raise TimeoutError("no model needed to build the sheets")

    result = run_period_insight(
        prepared,
        requester=NoProvider(),
        sheet_output_dir=tmp_path / "sheets",
        frame_cache_dir=None,
    )

    assert result.retained_ids == ("seen",)
    shown = {ref.entity_id for pack in result.episode_packs for ref in pack.page.tile_refs}
    assert shown == {"seen"}
    assert not any(
        scope.unavailable_asset_ids for pack in result.episode_packs for scope in pack.scopes
    )
    assert any("blind" in warning for warning in result.warnings)


def test_an_unread_episode_costs_its_own_pictures_not_the_month(tmp_path: Path) -> None:
    """A pack that failed hands its pictures on; it does not veto the period.

    Measured on a real dense month: 100 of 101 episodes read, and the thesis was
    refused because of the hundred-and-first. The wall is built from the
    episodes that could be read, the trace names the one that could not, and its
    pictures stay in the corpus for the passes that come after.
    """
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import run_period_insight

    start = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index:02d}", file_created_at=start + timedelta(days=index))
        for index in range(16)  # one per day: 16 episodes, so more than one pack
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("sienna"),
        ),
    )
    calls = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            # the first pack refuses; the rest answer
            if calls == 1:
                return "I cannot help with that."
            return _episode_pack_answer(prompt)
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": "Built from the episodes that could be read.",
                    "evidence": [
                        {"observation": "What tile 1 shows.", "representative_tiles": [1]}
                    ],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": None,
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; packing, reading and synthesis stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert len(result.episode_readings) < len(result.episode_sheets)
    assert result.insight is not None
    assert result.insight.thesis == "Built from the episodes that could be read."
    assert any("episodes could not be read" in warning for warning in result.warnings)
    # the unread episode's pictures are still in the corpus for later passes
    assert result.retained_ids == prepared.candidate_ids


def test_a_wall_holds_no_more_representatives_than_it_can_be_read_from(
    tmp_path: Path,
) -> None:
    """A wall too big is not refused, it is answered wrongly.

    Measured at temperature 0 on a real dense month's own representatives:
    walls of 10, 20, 40 and 60 produced theses that matched the month, and a
    wall of 100 fixated on a single tile and invented a fact about the period.
    The failure is a confident wrong answer, so the bound has to be structural.
    """
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt
    from immich_memories.analysis.period_insight import MAX_WALL_TILES, run_period_insight

    start = datetime(2026, 1, 1, tzinfo=UTC)
    assets = tuple(
        make_asset(f"asset-{index:03d}", file_created_at=start + timedelta(days=index))
        for index in range(MAX_WALL_TILES + 20)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("indigo"),
        ),
    )

    async def _answer(prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "chronological episode pack" in prompt:
            return _episode_pack_answer(prompt)
        return json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": "A period read from a wall it could hold.",
                    "evidence": [
                        {"observation": "What tile 1 shows.", "representative_tiles": [1]}
                    ],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": None,
                },
            }
        )

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=prepared.trace,
    )
    # WHY: query_llm is the provider boundary; the wall is built for real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_period_insight(
            prepared,
            requester=gateway,
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=None,
        )

    assert len(result.episode_readings) > MAX_WALL_TILES
    assert result.period_pages
    shown = sum(len(page.tile_refs) for page in result.period_pages)
    assert shown <= MAX_WALL_TILES
    assert result.insight is not None
    assert result.insight.thesis == "A period read from a wall it could hold."
    assert any("representatives" in warning for warning in result.warnings)
