"""Heavy scopes narrow before descriptions without pretending metadata has taste."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np
import probe_description_moment_cut as prototype
import probe_occasion_facts as occasion_facts
import probe_smart_edit_matrix as matrix
from probe_description_allocation import build_description_workprint
from probe_smart_edit_matrix import (
    _needs_optional_asset_cut,
    _required_fine_cut_ids,
    _resolved_card_mode,
    _restore_required_fine_cut_candidates,
)

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_source import EditorialGroup
from immich_memories.analysis.selection_structure import StructureMoment, StructureWorkprint
from immich_memories.api.models import Person
from tests.conftest import make_asset

START = datetime(2020, 1, 1, tzinfo=UTC)


def test_chapter_thesis_can_be_texture_without_a_sustained_thread() -> None:
    payload = {
        "schema_version": prototype.THESIS_SCHEMA,
        "thesis": "A quiet chapter of ordinary daily life.",
        "sustained_threads": [],
        "turning_points": [],
        "ordinary_texture": ["Routine scenes establish the period baseline."],
    }

    parsed = prototype._read_thesis(
        json.dumps(payload),
        frozenset({"M001"}),
        require_sustained=False,
    )

    assert parsed["sustained_threads"] == []


def test_chapter_selection_can_reject_every_moment_without_a_fake_comparison() -> None:
    payload = {
        "schema_version": prototype.SELECTION_SCHEMA,
        "keep": [],
        "audit_summary": "None of the available moments earns scarce runtime.",
        "comparisons": [],
        "overall_reason": "The chapter adds no necessary beat to the memory.",
    }

    parsed = prototype._read_selection(
        json.dumps(payload),
        frozenset({"M001", "M002"}),
        1,
    )

    assert parsed["keep"] == []


def test_chapter_allocation_scales_model_weights_to_runtime_capacity() -> None:
    def chapter(chapter_id: str, start: int, count: int) -> matrix.Chapter:
        cards = []
        for index in range(start, start + count):
            source = _moment(index)
            moment = prototype.Moment(
                alias=f"M{index:03d}",
                group=EditorialGroup(source.moment_id, source.candidates),
                descriptions=(),
            )
            cards.append(prototype.MomentCard(moment, "Literal scene.", None))
        return matrix.Chapter(chapter_id, chapter_id, tuple(cards))

    chapters = (
        chapter("C001", 1, 10),
        chapter("C002", 20, 5),
        chapter("C003", 40, 2),
    )
    payload = {
        "schema_version": matrix.ALLOCATION_SCHEMA,
        "allocations": [
            {"chapter_id": "C001", "slots": 10, "reason": "Primary thread."},
            {"chapter_id": "C002", "slots": 5, "reason": "Secondary thread."},
            {"chapter_id": "C003", "slots": 1, "reason": "Required onset."},
        ],
        "overall_reason": "Relative room before the runtime applies scarcity.",
    }

    allocation = matrix._read_allocation(
        json.dumps(payload),
        chapters,
        capacity=8,
        minimum_slots={"C003": 1},
    )

    assert [row["slots"] for row in allocation["allocations"]] == [5, 2, 1]
    assert allocation["slot_normalization"] == {
        "applied": True,
        "model_total": 16,
        "runtime_total": 8,
        "capacity": 8,
    }


def test_chapter_allocation_caps_impossible_slots_without_filling_elsewhere() -> None:
    def chapter(chapter_id: str, start: int, count: int) -> matrix.Chapter:
        cards = []
        for index in range(start, start + count):
            source = _moment(index)
            moment = prototype.Moment(
                alias=f"M{index:03d}",
                group=EditorialGroup(source.moment_id, source.candidates),
                descriptions=(),
            )
            cards.append(prototype.MomentCard(moment, "Literal scene.", None))
        return matrix.Chapter(chapter_id, chapter_id, tuple(cards))

    chapters = (chapter("C001", 1, 3), chapter("C002", 10, 2))
    payload = {
        "schema_version": matrix.ALLOCATION_SCHEMA,
        "allocations": [
            {"chapter_id": "C001", "slots": 5, "reason": "Primary thread."},
            {"chapter_id": "C002", "slots": 1, "reason": "Quiet contrast."},
        ],
        "overall_reason": "Use only the evidence each chapter actually contains.",
    }

    allocation = matrix._read_allocation(
        json.dumps(payload),
        chapters,
        capacity=10,
        minimum_slots={},
    )

    assert [row["slots"] for row in allocation["allocations"]] == [3, 1]
    assert allocation["slot_normalization"] == {
        "applied": True,
        "model_total": 6,
        "runtime_total": 4,
        "capacity": 10,
    }


def test_flat_editorial_wall_routes_by_serialized_size_not_only_card_count() -> None:
    source = _moment(1)
    moment = prototype.Moment(
        alias="M001",
        group=EditorialGroup(source.moment_id, source.candidates),
        descriptions=(),
    )
    compact = (prototype.MomentCard(moment, "One compact lived scene.", None),)
    oversized = (
        prototype.MomentCard(
            moment,
            "x" * (matrix.EDITORIAL_WALL_MAX_CHARS + 1),
            None,
        ),
    )

    assert matrix._use_flat_editorial_wall(compact, facts={})
    assert not matrix._use_flat_editorial_wall(oversized, facts={})


def test_chapter_parts_obey_the_same_serialized_wall_budget() -> None:
    cards = []
    for index in range(1, 4):
        source = _moment(index)
        moment = prototype.Moment(
            alias=f"M{index:03d}",
            group=EditorialGroup(source.moment_id, source.candidates),
            descriptions=(),
        )
        cards.append(
            prototype.MomentCard(
                moment,
                "x" * (matrix.EDITORIAL_WALL_MAX_CHARS // 2),
                None,
            )
        )
    case = matrix.Case(
        key="case",
        label="A year",
        product="monthly_highlights",
        ranges=(),
        target_seconds=600.0,
        brief="Make a truthful memory.",
    )

    chapters = matrix._chapters(case, tuple(cards), facts={})

    assert [len(chapter.cards) for chapter in chapters] == [1, 1, 1]
    assert all(
        matrix._editorial_wall_chars(chapter.cards, facts={})
        <= matrix.EDITORIAL_WALL_MAX_CHARS
        for chapter in chapters
    )


def test_selection_reorders_a_grounded_keep_set_chronologically() -> None:
    payload = {
        "schema_version": prototype.SELECTION_SCHEMA,
        "keep": [
            {"moment_id": "M002", "reason": "A later necessary beat."},
            {"moment_id": "M001", "reason": "The earlier necessary beat."},
        ],
        "audit_summary": "The two retained beats displace the weaker alternative.",
        "comparisons": [
            {
                "kept_moment_id": "M001",
                "rejected_moment_id": "M003",
                "reason": "It carries more visible meaning.",
            }
        ],
        "overall_reason": "The cut keeps the coherent sequence.",
    }

    parsed = matrix._read_selection_with_comparison_repair(
        json.dumps(payload),
        frozenset({"M001", "M002", "M003"}),
        2,
    )

    assert [row["moment_id"] for row in parsed["keep"]] == ["M001", "M002"]
    assert parsed["chronological_keep_repair"] is True


def test_selection_discards_optional_rows_for_runtime_admitted_anchors() -> None:
    payload = {
        "schema_version": prototype.SELECTION_SCHEMA,
        "keep": [
            {"moment_id": "M001", "reason": "A grounded optional beat."},
            {"moment_id": "M002", "reason": "The runtime already admits this anchor."},
        ],
        "audit_summary": "The optional beat carries the visible chapter change.",
        "comparisons": [
            {
                "kept_moment_id": "M001",
                "rejected_moment_id": "M003",
                "reason": "It carries more visible meaning.",
            }
        ],
        "overall_reason": "The cut keeps the grounded optional beat.",
    }

    parsed = matrix._read_selection_with_comparison_repair(
        json.dumps(payload),
        frozenset({"M001", "M002", "M003"}),
        2,
        excluded_ids=frozenset({"M002"}),
    )

    assert [row["moment_id"] for row in parsed["keep"]] == ["M001"]
    assert parsed["discarded_runtime_anchor_rows"] == 1


def _one_fake_page(*_args, **_kwargs):
    return (object(),)


def _described_moment(count: int = 2):
    candidates = tuple(_moment(index).candidates[0] for index in range(1, count + 1))
    return matrix.prototype.Moment(
        alias="M001",
        group=EditorialGroup("group-1", candidates),
        descriptions=tuple(
            matrix.prototype.Description(candidate.asset_id, f"Literal scene {index}")
            for index, candidate in enumerate(candidates, start=1)
        ),
    )


def _moment(
    index: int,
    *,
    year: int = 2020,
    favourite: bool = False,
    people: tuple[str, ...] = (),
) -> StructureMoment:
    asset = make_asset(
        f"asset-{index}",
        file_created_at=START.replace(year=year) + timedelta(days=index),
    )
    asset.is_favorite = favourite
    asset.people = [Person(id=f"person-{name}", name=name) for name in people]
    candidate = EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=favourite,
        source=asset,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )
    return StructureMoment(f"moment-{index}", (candidate,), candidate)


def _allocate(*moments: StructureMoment, relationships: tuple[str, ...] = ()):
    return build_description_workprint(
        StructureWorkprint(tuple(moments)),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        relationship_names=relationships,
        reduce_above_moments=0,
    )


def _combined_moment(*moments: StructureMoment) -> StructureMoment:
    candidates = tuple(moment.candidates[0] for moment in moments)
    return StructureMoment("combined", candidates, candidates[0])


def test_every_favourite_moment_enters_without_collapsing_same_chapter_favourites() -> None:
    first = _moment(1, favourite=True)
    second = _moment(2, favourite=True)

    allocated = _allocate(first, second)

    assert allocated.moments == (first, second)


def test_favourite_chapter_projects_exact_assets_without_reopening_full_moment() -> None:
    ordinary = _moment(1)
    favourite = _moment(2, favourite=True)
    combined = _combined_moment(ordinary, favourite)

    allocated = _allocate(combined)

    assert allocated.candidates == favourite.candidates
    assert allocated.moments[0].candidates == favourite.candidates
    assert allocated.reservoir_moments == (combined,)


def test_complete_favourite_chapter_coverage_uses_banked_descriptions() -> None:
    allocated = _allocate(_moment(1, favourite=True), _moment(2, favourite=True))

    assert _resolved_card_mode("auto", allocated) == "model"


def test_an_unstarred_chapter_switches_auto_to_complete_fused_moments() -> None:
    allocated = _allocate(_moment(1, year=2020, favourite=True), _moment(2, year=2021))

    assert _resolved_card_mode("auto", allocated) == "fused-vision"


def test_description_card_retries_one_incomplete_answer(monkeypatch, tmp_path: Path) -> None:
    attempts = 0

    async def answer(prompt, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("LLM returned incomplete content")
        return matrix.TextCall(
            prompt,
            json.dumps(
                {
                    "schema_version": matrix.prototype.CARD_SCHEMA,
                    "summary": "Two distinct literal scenes.",
                }
            ),
            0.1,
            False,
            False,
        )

    monkeypatch.setattr(matrix, "_ask_text", answer)
    cards, calls = asyncio.run(
        matrix._build_cards(
            (_described_moment(),),
            facts={},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
            card_mode="model",
        )
    )

    assert cards[0].summary == "Two distinct literal scenes."
    assert attempts == 2
    assert calls[0].warning == "initial description card failed: ValueError"


def test_description_card_falls_back_to_banked_literal_text(monkeypatch, tmp_path: Path) -> None:
    async def incomplete(*_args, **_kwargs):
        raise ValueError("LLM returned incomplete content")

    monkeypatch.setattr(matrix, "_ask_text", incomplete)
    cards, calls = asyncio.run(
        matrix._build_cards(
            (_described_moment(),),
            facts={},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
            card_mode="model",
        )
    )

    assert cards[0].summary == "Literal scene 1 ; Literal scene 2"
    assert "used deterministic description fallback" in calls[0].warning


def test_fused_card_retries_an_incomplete_visual_answer(monkeypatch, tmp_path: Path) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def __init__(self) -> None:
            self.requests = []

        def ask(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise ValueError("LLM returned incomplete content")
            return SimpleNamespace(
                raw_text=json.dumps(
                    {
                        "schema_version": matrix.prototype.CARD_SCHEMA,
                        "summary": "A compact literal scene.",
                    }
                ),
                provenance=SimpleNamespace(cache_hit=False),
            )

    requester = Requester()
    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=requester,
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary == "A compact literal scene."
    assert calls[0].warning == "initial fused card call failed: ValueError"
    assert requester.requests[1].pass_version == matrix.FUSED_CARD_RETRY_PASS_VERSION
    assert requester.requests[1].limits.max_output_tokens == 1200


def test_fused_card_uses_honest_fallback_after_two_incomplete_answers(
    monkeypatch, tmp_path: Path
) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def ask(self, _request):
            raise ValueError("LLM returned incomplete content")

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary.startswith(
        "Visual contents unavailable after two incomplete card calls"
    )
    assert "used metadata-only fallback" in calls[0].warning


def test_fused_card_mechanically_repairs_schema_and_unsafe_punctuation(
    monkeypatch, tmp_path: Path
) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def ask(self, _request):
            return SimpleNamespace(
                raw_text=json.dumps(
                    {
                        "schema_version": "model-echoed-the-wrong-schema",
                        "moment_id": "M001",
                        "confidence": 0.99,
                        "summary": 'A sign reads "unsafe quoted text" beside the group\\path.',
                    }
                ),
                provenance=SimpleNamespace(cache_hit=False),
            )

    async def compact_repair(*_args, **_kwargs):
        raise AssertionError("mechanical envelope hygiene must not call the model")

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    monkeypatch.setattr(matrix, "_ask_text", compact_repair)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary == "A sign reads ”unsafe quoted text” beside the group∖path."
    assert calls[0].warning == (
        "fused card content needed repair: ValueError; "
        "repaired schema/display punctuation mechanically"
    )


def test_graph_grounded_asset_is_restored_when_selects_absorbs_it() -> None:
    tagged = _moment(1, people=("Casey",))
    untagged = _moment(2)
    reservoir = _combined_moment(tagged, untagged)
    text_result = {
        "moment_alias_by_group": {reservoir.moment_id: "M001"},
        "lifecycle_requirements": [{"anchor_id": "M001", "person_name": "Casey"}],
    }
    required = _required_fine_cut_ids(
        reservoir.candidates,
        reservoirs=(reservoir,),
        text_result=text_result,
    )

    restored = _restore_required_fine_cut_candidates(
        untagged.candidates,
        reservoir_candidates=reservoir.candidates,
        required_asset_ids=required,
    )

    assert required == (tagged.candidates[0].asset_id,)
    assert {candidate.asset_id for candidate in restored} == {
        tagged.candidates[0].asset_id,
        untagged.candidates[0].asset_id,
    }


def test_fine_cut_wall_uses_stable_compact_people_and_episode_context() -> None:
    first = _moment(1, people=("Casey",))
    second = _moment(2, people=("Casey",))
    candidates = first.candidates + second.candidates
    descriptions = {candidate.asset_id: "A distinct lived scene." for candidate in candidates}
    fact = matrix.PersonFact(
        name="Casey",
        relationship="friend of library owner",
        relationship_source="confirmed",
        birth_date=None,
        first_month="2020-01",
        onset="2020-01",
        tier="recurring",
        relationship_current=True,
    )

    wall = matrix._fine_cut_candidates(
        candidates,
        reservoirs=(first, second),
        descriptions=descriptions,
        text_result={"moment_alias_by_group": {first.moment_id: "M001", second.moment_id: "M002"}},
        facts={"Casey": fact},
    )

    assert wall[0].people_context == wall[1].people_context
    assert wall[0].people_context == (
        "P01:tier=recurring;relationship=friend of library owner;source=confirmed",
    )
    assert wall[0].episode_id is not None
    assert "Casey" not in wall[0].wall_line()


def test_fine_cut_calls_a_live_photo_motion_only_when_it_visibly_adds_value() -> None:
    reservoir = _moment(1)
    live_candidate = replace(reservoir.candidates[0], media_kind="live_photo")
    descriptions = {live_candidate.asset_id: "A person turns toward the camera."}
    common = {
        "reservoirs": (replace(reservoir, candidates=(live_candidate,)),),
        "descriptions": descriptions,
        "text_result": {"moment_alias_by_group": {reservoir.moment_id: "M001"}},
        "facts": {},
    }

    still_wall = matrix._fine_cut_candidates(
        (live_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="photo")),
        motion_contributions={live_candidate.asset_id: "meaningful"},
        motion_reasons={live_candidate.asset_id: "A turn becomes a laugh."},
        **common,
    )
    weak_motion_wall = matrix._fine_cut_candidates(
        (live_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip")),
        motion_contributions={live_candidate.asset_id: "still_sufficient"},
        motion_reasons={live_candidate.asset_id: "The frames repeat the same pose."},
        **common,
    )
    useful_motion_wall = matrix._fine_cut_candidates(
        (live_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip")),
        motion_contributions={live_candidate.asset_id: "meaningful"},
        motion_reasons={live_candidate.asset_id: "A turn becomes a laugh."},
        **common,
    )

    assert still_wall[0].media_kind == "photo"
    assert weak_motion_wall[0].media_kind == "photo"
    assert useful_motion_wall[0].media_kind == "live-motion"
    assert useful_motion_wall[0].motion_reason == "A turn becomes a laugh."
    assert "motion meaningful: A turn becomes a laugh." in useful_motion_wall[0].wall_line()
    assert "live_photo" not in still_wall[0].wall_line()


def test_fine_cut_prefers_video_only_when_observed_motion_adds_value() -> None:
    reservoir = _moment(1)
    video_candidate = replace(reservoir.candidates[0], media_kind="video")
    common = {
        "reservoirs": (replace(reservoir, candidates=(video_candidate,)),),
        "descriptions": {video_candidate.asset_id: "A person crosses a stream."},
        "text_result": {"moment_alias_by_group": {reservoir.moment_id: "M001"}},
        "facts": {},
    }

    unobserved = matrix._fine_cut_candidates(
        (video_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="photo")),
        **common,
    )
    still_sufficient = matrix._fine_cut_candidates(
        (video_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip")),
        motion_contributions={video_candidate.asset_id: "still_sufficient"},
        motion_reasons={video_candidate.asset_id: "The framing barely changes."},
        **common,
    )
    meaningful = matrix._fine_cut_candidates(
        (video_candidate,),
        atlas=SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip")),
        motion_contributions={video_candidate.asset_id: "meaningful"},
        motion_reasons={video_candidate.asset_id: "The crossing unfolds across the frames."},
        **common,
    )

    assert unobserved[0].media_kind == "photo"
    assert unobserved[0].source_media_kind == "video"
    assert unobserved[0].motion_observed is False
    assert still_sufficient[0].media_kind == "photo"
    assert still_sufficient[0].motion_observed is True
    assert still_sufficient[0].render_mode == "still"
    assert still_sufficient[0].render_frame_seconds is not None
    assert meaningful[0].media_kind == "video"
    assert meaningful[0].motion_observed is True
    assert meaningful[0].render_mode == "motion"
    assert meaningful[0].render_frame_seconds is None


def test_final_refinement_uses_480p_analysis_motion_only_for_selected_reservoirs(
    monkeypatch, tmp_path: Path
) -> None:
    from immich_memories.analysis.visual_atlas import AtlasSource

    reservoir = _moment(1)
    asset = reservoir.candidates[0].source
    asset.live_photo_video_id = "motion-component"
    live_candidate = replace(reservoir.candidates[0], media_kind="live_photo")
    outside_asset = _moment(2).candidates[0].source
    prepared = SimpleNamespace(
        visual_sources=(
            AtlasSource(asset=asset, preview_jpeg=b"still-preview"),
            AtlasSource(asset=outside_asset, preview_jpeg=b"outside-preview"),
        )
    )
    original_path = tmp_path / "motion.mov"
    original_path.write_bytes(b"local motion")
    analysis_path = tmp_path / "motion_480p.mov"
    analysis_path.write_bytes(b"analysis motion")
    downloaded: list[str] = []
    analysis_requests = []
    built_sources = []
    atlas = SimpleNamespace(tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip"))

    class _Batch:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_analysis_video(
            self,
            _client,
            requested_asset,
            *,
            target_height,
            enable_downscaling,
            gpu_decode,
        ):
            downloaded.append(requested_asset.id)
            analysis_requests.append((target_height, enable_downscaling, gpu_decode))
            return analysis_path, original_path

    class _Cache:
        def __init__(self, **_kwargs):
            pass

        def begin_batch(self):
            return _Batch()

    def _build(sources, **_kwargs):
        built_sources.extend(sources)
        return atlas

    monkeypatch.setattr(matrix, "VideoDownloadCache", _Cache)
    monkeypatch.setattr(matrix, "build_visual_atlas", _build)
    config = SimpleNamespace(
        cache=SimpleNamespace(
            video_cache_path=tmp_path / "video-cache",
            video_cache_max_size_gb=1.0,
            video_cache_max_age_days=7,
        )
    )

    result, stats = matrix._final_refinement_atlas(
        client=object(),
        config=config,
        prepared=prepared,
        candidates=(live_candidate,),
        frame_cache_dir=tmp_path / "frames",
    )

    assert result is atlas
    assert downloaded == [asset.id]
    assert analysis_requests == [(480, True, True)]
    assert len(built_sources) == 1
    assert built_sources[0].asset.id == asset.id
    assert built_sources[0].motion_path == analysis_path
    assert stats == {
        "requested_motion_sources": 1,
        "downloaded_motion_sources": 1,
        "filmstrip_sources": 1,
    }


def test_final_refinement_reuses_an_existing_local_motion_path(
    monkeypatch, tmp_path: Path
) -> None:
    from immich_memories.analysis.visual_atlas import AtlasSource

    reservoir = _moment(1)
    candidate = replace(reservoir.candidates[0], media_kind="video")
    existing = tmp_path / "already-local.mov"
    existing.write_bytes(b"local motion")
    prepared = SimpleNamespace(
        visual_sources=(
            AtlasSource(
                asset=candidate.source,
                preview_jpeg=b"preview",
                motion_path=existing,
            ),
        )
    )
    built_sources = []

    class _Batch:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_analysis_video(self, _client, _asset, **_kwargs):
            raise ValueError("analysis rendition unavailable")

    class _Cache:
        def __init__(self, **_kwargs):
            pass

        def begin_batch(self):
            return _Batch()

    def _build(sources, **_kwargs):
        built_sources.extend(sources)
        return SimpleNamespace(
            tile_for=lambda _asset_id: SimpleNamespace(kind="filmstrip")
        )

    monkeypatch.setattr(matrix, "VideoDownloadCache", _Cache)
    monkeypatch.setattr(matrix, "build_visual_atlas", _build)
    config = SimpleNamespace(
        cache=SimpleNamespace(
            video_cache_path=tmp_path / "video-cache",
            video_cache_max_size_gb=1.0,
            video_cache_max_age_days=7,
        )
    )

    matrix._final_refinement_atlas(
        client=object(),
        config=config,
        prepared=prepared,
        candidates=(candidate,),
        frame_cache_dir=tmp_path / "frames",
    )

    assert built_sources[0].motion_path == existing


def test_only_observed_reservoir_motion_is_described_before_selects() -> None:
    photo = replace(_moment(1).candidates[0], media_kind="photo")
    video = replace(_moment(2).candidates[0], media_kind="video")
    live_photo = replace(_moment(3).candidates[0], media_kind="live_photo")
    unavailable_video = replace(_moment(4).candidates[0], media_kind="video")
    kinds = {
        photo.asset_id: "photo",
        video.asset_id: "filmstrip",
        live_photo.asset_id: "filmstrip",
        unavailable_video.asset_id: "unavailable",
    }
    atlas = SimpleNamespace(
        tile_for=lambda asset_id: SimpleNamespace(kind=kinds[asset_id])
    )

    candidates = matrix._observed_motion_candidates(
        (photo, video, live_photo, unavailable_video),
        atlas=atlas,
    )

    assert tuple(candidate.asset_id for candidate in candidates) == (
        video.asset_id,
        live_photo.asset_id,
    )


def test_abundant_duration_still_requires_the_optional_asset_quality_cut() -> None:
    candidates = tuple(
        matrix.FineCutCandidate(
            alias=f"A{index:03d}",
            asset_id=f"asset-{index}",
            moment_id="M001",
            taken_at=START + timedelta(minutes=index),
            media_kind="photo",
            favourite=False,
            description=f"Scene {index}",
        )
        for index in range(1, 4)
    )

    assert _needs_optional_asset_cut(candidates, required_aliases=(), capacity=143)
    assert not _needs_optional_asset_cut(candidates[:1], required_aliases=("A001",), capacity=143)


def test_abundant_duration_still_asks_for_moment_quality(monkeypatch, tmp_path: Path) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)
    card = matrix.prototype.MomentCard(
        matrix.prototype.Moment(alias="M001", group=group, descriptions=()),
        "One distinct lived scene.",
        None,
    )
    answer = json.dumps(
        {
            "schema_version": matrix.prototype.SELECTION_SCHEMA,
            "keep": [{"moment_id": "M001", "reason": "It earns runtime."}],
            "audit_summary": "The only moment is a lived scene.",
            "comparisons": [],
            "overall_reason": "The one-moment wall is coherent.",
        }
    )
    called = False

    async def select_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return matrix.TextCall("prompt", answer, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            (card,),
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "One grounded scene."},
            capacity=13,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert called
    assert len(calls) == 1
    assert [row["moment_id"] for row in selection["keep"]] == ["M001"]


def test_each_chapter_selection_receives_its_own_grounded_chapter_reading(
    monkeypatch, tmp_path: Path
) -> None:
    def chapter(chapter_id: str, start: int) -> matrix.Chapter:
        cards = []
        for index in range(start, start + 2):
            source = _moment(index)
            moment = prototype.Moment(
                alias=f"M{index:03d}",
                group=EditorialGroup(source.moment_id, source.candidates),
                descriptions=(),
            )
            cards.append(prototype.MomentCard(moment, f"Lived scene M{index:03d}.", None))
        return matrix.Chapter(chapter_id, chapter_id, tuple(cards))

    chapters = (chapter("C001", 101), chapter("C002", 203))
    readings = (
        (
            chapters[0],
            {
                "thesis": "A coordinated winter excursion anchors the month.",
                "sustained_threads": [
                    {
                        "summary": "Snow terrain and equipment carry the outing.",
                        "evidence_moment_ids": ["M101", "M203"],
                    }
                ],
                "turning_points": [],
                "ordinary_texture": [],
            },
        ),
        (
            chapters[1],
            {
                "thesis": "Live music performances define the festival days.",
                "sustained_threads": [
                    {
                        "summary": "Stage views and crowd energy recur.",
                        "evidence_moment_ids": ["M203"],
                    }
                ],
                "turning_points": [],
                "ordinary_texture": [],
            },
        ),
    )
    global_thesis = {
        "thesis": "A year moving from winter outings to summer festivals.",
        "sustained_threads": [
            {"summary": "Friends recur across seasons.", "evidence_moment_ids": ["M101", "M203"]}
        ],
        "turning_points": [],
        "ordinary_texture": [],
    }
    selection_prompts: list[str] = []

    async def scripted_call(prompt, **_kwargs):
        if "Allocate at most" in prompt:
            answer = json.dumps(
                {
                    "schema_version": matrix.ALLOCATION_SCHEMA,
                    "allocations": [
                        {"chapter_id": "C001", "slots": 1, "reason": "Winter carries a beat."},
                        {"chapter_id": "C002", "slots": 1, "reason": "The festival answers it."},
                    ],
                    "overall_reason": "Scarcity split across both seasons.",
                }
            )
            return matrix.TextCall(prompt, answer, 0.1, False, False)
        selection_prompts.append(prompt)
        kept, dropped = ("M101", "M102") if "Lived scene M101." in prompt else ("M203", "M204")
        answer = json.dumps(
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [{"moment_id": kept, "reason": "It carries the chapter beat."}],
                "audit_summary": "One moment earns the scarce slot.",
                "comparisons": [
                    {
                        "kept_moment_id": kept,
                        "rejected_moment_id": dropped,
                        "reason": "The kept scene shows the defining action on screen.",
                    }
                ],
                "overall_reason": "The chapter keeps its defining beat.",
            }
        )
        return matrix.TextCall(prompt, answer, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", scripted_call)
    selection, _allocation, _calls = asyncio.run(
        matrix._hierarchical_selection(
            chapters,
            readings,
            case=matrix.Case(
                key="case",
                label="A year memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis=global_thesis,
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
            thinking=False,
        )
    )

    prompt_a = next(p for p in selection_prompts if "Lived scene M101." in p)
    prompt_b = next(p for p in selection_prompts if "Lived scene M203." in p)
    assert "coordinated winter excursion" in prompt_a
    assert "Live music performances" in prompt_b
    assert "Live music performances" not in prompt_a
    assert "coordinated winter excursion" not in prompt_b
    assert global_thesis["thesis"] in prompt_a
    assert global_thesis["thesis"] in prompt_b
    assert "M203" not in prompt_a
    assert "M101" not in prompt_b
    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M203"]


def test_selection_repairs_comparisons_that_only_compare_kept_moments(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(
        matrix.prototype.MomentCard(
            matrix.prototype.Moment(
                alias=f"M00{index}",
                group=EditorialGroup(f"group-{index}", _moment(index).candidates),
                descriptions=(),
            ),
            f"Distinct lived scene {index}.",
            None,
        )
        for index in (1, 2, 3)
    )
    answers = iter(
        (
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the strongest beat."},
                    {"moment_id": "M002", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": "Two moments earn runtime.",
                "comparisons": [
                    {
                        "kept_moment_id": "M001",
                        "rejected_moment_id": "M002",
                        "reason": "An invalid comparison between two retained moments.",
                    }
                ],
                "overall_reason": "The cut keeps the useful beats.",
            },
            {
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the strongest beat."},
                    {"moment_id": "M002", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": {"main_tradeoff": "Two moments beat the weaker alternative."},
                "comparisons": [
                    {
                        "kept_moment_id": "M001",
                        "rejected_moment_id": "M003",
                        "reason": "Its visible action contributes more than the alternative.",
                    }
                ],
                "overall_reason": "The cut keeps the useful beats.",
            },
        )
    )

    async def select_call(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, json.dumps(next(answers)), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            cards,
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "Three grounded scenes."},
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert len(calls) == 2
    assert selection["comparisons"][0]["rejected_moment_id"] == "M003"


def test_selection_repairs_an_overfull_keep_against_exact_capacity(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(
        prototype.MomentCard(
            prototype.Moment(
                alias=f"M00{index}",
                group=EditorialGroup(f"group-{index}", _moment(index).candidates),
                descriptions=(),
            ),
            f"Distinct lived scene {index}.",
            None,
        )
        for index in (1, 2, 3)
    )
    answers = iter(
        (
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": f"M00{index}", "reason": "It contributes."} for index in (1, 2, 3)
                ],
                "audit_summary": "The first cut exceeded its actual capacity.",
                "comparisons": [],
                "overall_reason": "All three initially looked useful.",
            },
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the main beat."},
                    {"moment_id": "M003", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": "Two moments beat the weakest third choice.",
                "comparisons": [
                    {
                        "kept_moment_id": "M003",
                        "rejected_moment_id": "M002",
                        "reason": "Its visible contrast adds more to the sequence.",
                    }
                ],
                "overall_reason": "The repaired cut obeys the two-slot scarcity.",
            },
        )
    )

    prompts = []

    async def select_call(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, json.dumps(next(answers)), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            cards,
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "Three grounded scenes."},
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert len(calls) == 2
    assert [row["moment_id"] for row in selection["keep"]] == ["M001", "M003"]
    assert "previous keep has 3 rows" in prompts[1]
    assert "Return at most 2 keep rows" in prompts[1]


def test_a_chapter_with_no_favourites_stays_complete() -> None:
    old = (_moment(1, year=2010), _moment(2, year=2010))
    recent = (_moment(3, year=2020, favourite=True), _moment(4, year=2020))

    allocated = _allocate(*old, *recent)

    assert {moment.moment_id for moment in allocated.moments} == {
        old[0].moment_id,
        old[1].moment_id,
        recent[0].moment_id,
    }


def test_missing_relationship_context_gets_first_and_last_views_not_every_view() -> None:
    favourite = _moment(1, favourite=True, people=("Casey",))
    with_partner = tuple(_moment(index, people=("Morgan",)) for index in range(2, 6))

    allocated = _allocate(favourite, *with_partner, relationships=("Morgan",))

    assert allocated.moments == (favourite, with_partner[0], with_partner[-1])


def test_a_persons_first_appearance_and_return_after_two_years_enter() -> None:
    first = _moment(1, year=2010, people=("Seb",))
    ordinary = _moment(2, year=2010, people=("Seb",), favourite=True)
    returned = _moment(3, year=2013, people=("Seb",))

    allocated = _allocate(first, ordinary, returned)
    by_id = {item.moment.moment_id: item.reasons for item in allocated.admissions}

    assert "first-copresence:Seb" in by_id[first.moment_id]
    assert "resumption:Seb" in by_id[returned.moment_id]


def test_small_walls_pass_through_without_applying_the_heavy_scope_rules() -> None:
    moments = tuple(_moment(index) for index in range(3))

    allocated = build_description_workprint(
        StructureWorkprint(moments),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        reduce_above_moments=3,
    )

    assert allocated.moments == moments
    assert {item.reasons for item in allocated.admissions} == {("complete-wall",)}


def _dated_card(alias: str, when: datetime, *, favourite: bool = False) -> prototype.MomentCard:
    asset = make_asset(f"asset-{alias}", file_created_at=when)
    asset.is_favorite = favourite
    candidate = EditorialCandidate(
        asset_id=asset.id,
        taken_at=when,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=favourite,
        source=asset,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )
    return prototype.MomentCard(
        prototype.Moment(alias, EditorialGroup(f"group-{alias}", (candidate,)), ()),
        f"Lived scene {alias}.",
        None,
    )


def test_chapter_cut_caps_one_evening_and_surfaces_the_freed_slot() -> None:
    # One evening of 2007-10-11 held three separate moments; the per-moment cap
    # never saw them as one occasion. The freed slot is reported, never refilled.
    cards = (
        _dated_card("M106", datetime(2007, 10, 11, 19, 20, tzinfo=UTC)),
        _dated_card("M107", datetime(2007, 10, 11, 19, 54, tzinfo=UTC)),
        _dated_card("M110", datetime(2007, 10, 11, 23, 0, tzinfo=UTC)),
        _dated_card("M120", datetime(2007, 10, 14, 11, 0, tzinfo=UTC)),
        _dated_card("M130", datetime(2007, 10, 20, 9, 0, tzinfo=UTC)),
    )
    selection = {
        "keep": [
            {"moment_id": alias, "reason": "It contributes."}
            for alias in ("M106", "M107", "M110", "M120")
        ],
        "audit_summary": "Four moments looked necessary.",
        "comparisons": [
            {
                "kept_moment_id": "M110",
                "rejected_moment_id": "M130",
                "reason": "The late scene carries the visible action.",
            }
        ],
        "overall_reason": "The chapter keeps its beats.",
    }

    capped = matrix._apply_occasion_cap(selection, cards)

    # The rule: a moment the cut defended in a comparison outranks an undefended
    # one; undefended moments keep their chronological keep order, so the
    # evening's opening beat survives and its middle repeat is dropped.
    assert [row["moment_id"] for row in capped["keep"]] == ["M106", "M110", "M120"]
    assert capped["occasion_cap"]["freed_slots"] == 1
    assert capped["occasion_cap"]["removed_moment_ids"] == ["M107"]
    assert capped["occasion_cap"]["capped_occasions"] == [
        {"occasion_id": "2007-10-11", "kept_moments": 2, "removed_moments": 1}
    ]
    assert capped["occasion_cap"]["starved_occasions"] == [
        {"occasion_id": "2007-10-20", "kept_moments": 0, "rejected_moments": 1}
    ]


def test_hierarchical_selection_records_every_rejected_moment_with_its_reason(
    monkeypatch, tmp_path: Path
) -> None:
    chapters = (
        matrix.Chapter(
            "C001",
            "2007-10",
            (
                _dated_card("M101", datetime(2007, 10, 11, 19, 20, tzinfo=UTC)),
                _dated_card("M102", datetime(2007, 10, 11, 19, 54, tzinfo=UTC)),
                _dated_card("M103", datetime(2007, 10, 11, 23, 0, tzinfo=UTC)),
                _dated_card("M104", datetime(2007, 10, 14, 11, 0, tzinfo=UTC)),
                _dated_card("M105", datetime(2007, 10, 20, 9, 0, tzinfo=UTC)),
            ),
        ),
        matrix.Chapter(
            "C002",
            "2007-11",
            (_dated_card("M203", datetime(2007, 11, 3, 9, 0, tzinfo=UTC)),),
        ),
    )
    reading = {
        "thesis": "An autumn of ordinary evenings.",
        "sustained_threads": [],
        "turning_points": [],
        "ordinary_texture": [],
    }
    readings = ((chapters[0], dict(reading)), (chapters[1], dict(reading)))

    async def scripted_call(prompt, **_kwargs):
        if "Allocate at most" in prompt:
            answer = json.dumps(
                {
                    "schema_version": matrix.ALLOCATION_SCHEMA,
                    "allocations": [
                        {"chapter_id": "C001", "slots": 4, "reason": "October carries the year."},
                        {"chapter_id": "C002", "slots": 0, "reason": "November repeats it."},
                    ],
                    "overall_reason": "Scarcity sits in October.",
                }
            )
            return matrix.TextCall(prompt, answer, 0.1, False, False)
        answer = json.dumps(
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": alias, "reason": "It contributes."}
                    for alias in ("M101", "M102", "M103", "M104")
                ],
                "audit_summary": "Four moments looked necessary.",
                "comparisons": [
                    {
                        "kept_moment_id": "M103",
                        "rejected_moment_id": "M105",
                        "reason": "The late scene carries the visible action.",
                    }
                ],
                "overall_reason": "October keeps its beats.",
            }
        )
        return matrix.TextCall(prompt, answer, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", scripted_call)
    selection, _allocation, _calls = asyncio.run(
        matrix._hierarchical_selection(
            chapters,
            readings,
            case=matrix.Case(
                key="case",
                label="A year memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "A year of autumn evenings."},
            capacity=4,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M103", "M104"]
    assert selection["rejected"] == [
        {"moment_id": "M102", "reason": None},
        {"moment_id": "M105", "reason": "The late scene carries the visible action."},
        {"moment_id": "M203", "reason": None},
    ]
    assert selection["occasion_cap"] == [
        {
            "chapter_id": "C001",
            "max_per_occasion": 2,
            "freed_slots": 1,
            "removed_moment_ids": ["M102"],
            "capped_occasions": [
                {"occasion_id": "2007-10-11", "kept_moments": 2, "removed_moments": 1}
            ],
            "starved_occasions": [
                {"occasion_id": "2007-10-20", "kept_moments": 0, "rejected_moments": 1}
            ],
            # The freed slot was offered back; this replay repeats the first cut,
            # whose rows are all excluded, so the round returns nothing.
            "reallocation": {"slots": 1, "keep": [], "removed_moment_ids": []},
        }
    ]


_REALLOCATION_CARDS = (
    ("M101", datetime(2007, 10, 11, 19, 20, tzinfo=UTC)),
    ("M102", datetime(2007, 10, 11, 19, 54, tzinfo=UTC)),
    ("M103", datetime(2007, 10, 11, 23, 0, tzinfo=UTC)),
    ("M104", datetime(2007, 10, 14, 11, 0, tzinfo=UTC)),
    ("M105", datetime(2007, 10, 20, 9, 0, tzinfo=UTC)),
)


def _cut_answer(keep: tuple[str, ...], comparisons: list[dict[str, str]]) -> str:
    return json.dumps(
        {
            "schema_version": prototype.SELECTION_SCHEMA,
            "keep": [{"moment_id": alias, "reason": "It contributes."} for alias in keep],
            "audit_summary": "The chapter weighs its beats.",
            "comparisons": comparisons,
            "overall_reason": "The chapter keeps its beats.",
        }
    )


def _run_chapter_cut(
    cards: tuple[prototype.MomentCard, ...],
    answers: tuple[str, ...],
    capacity: int,
    monkeypatch,
    tmp_path: Path,
    *,
    reading: dict[str, Any] | None = None,
):
    replies = iter(answers)

    async def scripted_call(prompt, **_kwargs):
        return matrix.TextCall(prompt, next(replies), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", scripted_call)
    thesis: dict[str, Any] = {"thesis": "A year of autumn evenings."}
    return asyncio.run(
        matrix._select_cards(
            cards,
            case=matrix.Case(
                key="case",
                label="A year memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis=matrix._local_thesis(thesis, cards, reading=reading) if reading else thesis,
            reading=reading,
            capacity=capacity,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )


def test_capped_chapter_reasks_its_freed_slot_from_a_starved_occasion(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(_dated_card(alias, when) for alias, when in _REALLOCATION_CARDS)
    answers = (
        _cut_answer(
            ("M101", "M102", "M103", "M104"),
            [
                {
                    "kept_moment_id": "M103",
                    "rejected_moment_id": "M105",
                    "reason": "The late scene carries the visible action.",
                }
            ],
        ),
        _cut_answer(("M105",), []),
    )

    selection, calls = _run_chapter_cut(cards, answers, 4, monkeypatch, tmp_path)

    assert len(calls) == 2
    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M103", "M104", "M105"]
    assert selection["occasion_cap"]["freed_slots"] == 1
    assert selection["occasion_cap"]["removed_moment_ids"] == ["M102"]
    assert selection["occasion_cap"]["reallocation"] == {
        "slots": 1,
        "keep": [{"moment_id": "M105", "reason": "It contributes."}],
        "removed_moment_ids": [],
    }
    assert selection["rejected"] == [{"moment_id": "M102", "reason": None}]


def test_a_capped_chapter_with_no_starved_occasion_never_reasks(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(_dated_card(alias, when) for alias, when in _REALLOCATION_CARDS[:3])

    selection, calls = _run_chapter_cut(
        cards,
        (_cut_answer(("M101", "M102", "M103"), []),),
        3,
        monkeypatch,
        tmp_path,
    )

    assert len(calls) == 1
    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M102"]
    assert selection["occasion_cap"]["freed_slots"] == 1
    assert selection["occasion_cap"]["starved_occasions"] == []
    assert "reallocation" not in selection["occasion_cap"]


def test_occasion_cap_spares_a_moment_the_chapter_reading_cites_as_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    # The festival's only stage frame is unstarred and undefended, so the cap's
    # deterministic drop rule loses it to two same-day siblings — while the
    # chapter reading names it as the evidence for its sustained thread.
    cards = tuple(
        _dated_card(alias, datetime(2007, 7, 14, hour, minute, tzinfo=UTC))
        for alias, hour, minute in (
            ("M021", 19, 20),
            ("M022", 19, 54),
            ("M023", 21, 10),
            ("M025", 23, 0),
        )
    )
    reading = {
        "thesis": "A festival day that ends on the main stage.",
        "sustained_threads": [
            {"summary": "The crowd builds toward the stage.", "evidence_moment_ids": ["M025"]}
        ],
        "turning_points": [],
        "ordinary_texture": [],
    }

    selection, calls = _run_chapter_cut(
        cards,
        (_cut_answer(("M021", "M022", "M023", "M025"), []),),
        4,
        monkeypatch,
        tmp_path,
        reading=reading,
    )

    assert len(calls) == 1
    assert [row["moment_id"] for row in selection["keep"]] == ["M021", "M022", "M025"]
    assert selection["occasion_cap"]["freed_slots"] == 1
    assert selection["occasion_cap"]["removed_moment_ids"] == ["M023"]
    # The cited moment neither drops nor spends the occasion's two slots, exactly
    # as a favourite-bearing or lifecycle-anchored moment behaves.
    assert selection["occasion_cap"]["capped_occasions"] == [
        {"occasion_id": "2007-07-14", "kept_moments": 3, "removed_moments": 1}
    ]
    assert selection["occasion_cap"]["starved_occasions"] == []
    assert selection["rejected"] == [{"moment_id": "M023", "reason": None}]


def _chapter_reading(thesis_text: str, evidence: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "thesis": thesis_text,
        "sustained_threads": (
            [{"summary": "The thread runs on.", "evidence_moment_ids": list(evidence)}]
            if evidence
            else []
        ),
        "turning_points": [],
        "ordinary_texture": [],
    }


def _run_hierarchical(
    chapters: tuple[matrix.Chapter, ...],
    readings: tuple[tuple[matrix.Chapter, dict[str, Any]], ...],
    answer_for,
    capacity: int,
    monkeypatch,
    tmp_path: Path,
):
    async def scripted_call(prompt, **_kwargs):
        return matrix.TextCall(prompt, answer_for(prompt), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", scripted_call)
    return asyncio.run(
        matrix._hierarchical_selection(
            chapters,
            readings,
            case=matrix.Case(
                key="case",
                label="A year memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "A year of autumn evenings."},
            capacity=capacity,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
            thinking=False,
        )
    )


def _allocation_answer(*rows: tuple[str, int]) -> str:
    return json.dumps(
        {
            "schema_version": matrix.ALLOCATION_SCHEMA,
            "allocations": [
                {"chapter_id": chapter_id, "slots": slots, "reason": "Its weight in the year."}
                for chapter_id, slots in rows
            ],
            "overall_reason": "Scarcity sits where the year is dense.",
        }
    )


def test_freed_slots_cross_chapters_to_a_chapter_whose_reading_cites_a_rejected_moment(
    monkeypatch, tmp_path: Path
) -> None:
    # October's cap frees two slots its own round cannot spend: the chapter is one
    # long evening, so no starved occasion remains to re-ask. November argued its
    # reading from a moment its single slot could not admit, and never sees them.
    october = matrix.Chapter(
        "C001",
        "2007-10",
        tuple(
            _dated_card(alias, datetime(2007, 10, 11, hour, 0, tzinfo=UTC))
            for alias, hour in (("M101", 18), ("M102", 19), ("M103", 20), ("M104", 21))
        ),
    )
    november = matrix.Chapter(
        "C002",
        "2007-11",
        tuple(
            _dated_card(alias, datetime(2007, 11, day, 9, 0, tzinfo=UTC))
            for alias, day in (("M201", 3), ("M202", 10), ("M203", 17))
        ),
    )
    readings = (
        (october, _chapter_reading("An October of one long evening.")),
        (november, _chapter_reading("November turns on the walk.", ("M202",))),
    )
    november_prompts: list[str] = []

    def answer_for(prompt: str) -> str:
        if "Allocate at most" in prompt:
            return _allocation_answer(("C001", 4), ("C002", 1))
        if "M101" in prompt:
            return _cut_answer(("M101", "M102", "M103", "M104"), [])
        november_prompts.append(prompt)
        if len(november_prompts) == 1:
            return _cut_answer(
                ("M201",),
                [
                    {
                        "kept_moment_id": "M201",
                        "rejected_moment_id": "M203",
                        "reason": "The first walk shows the season turning.",
                    }
                ],
            )
        return _cut_answer(
            ("M202",),
            [
                {
                    "kept_moment_id": "M202",
                    "rejected_moment_id": "M203",
                    "reason": "The cited walk carries the thread the reading argues.",
                }
            ],
        )

    selection, _allocation, calls = _run_hierarchical(
        (october, november), readings, answer_for, 5, monkeypatch, tmp_path
    )

    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M102", "M201", "M202"]
    assert len(calls) == 4
    assert selection["global_reallocation"] == {
        "pooled": 2,
        "grants": [
            {
                "chapter_id": "C002",
                "reason": "reading_evidence",
                "unserved_moment_ids": ["M202"],
                "slots": 1,
            }
        ],
        "rounds": [
            {
                "chapter_id": "C002",
                "slots": 1,
                "keep": [{"moment_id": "M202", "reason": "It contributes."}],
                "removed_moment_ids": [],
            }
        ],
        "forfeits": [],
    }


def test_a_year_that_frees_nothing_never_opens_a_global_round(monkeypatch, tmp_path: Path) -> None:
    october = matrix.Chapter(
        "C001",
        "2007-10",
        tuple(
            _dated_card(alias, datetime(2007, 10, 11, hour, 0, tzinfo=UTC))
            for alias, hour in (("M101", 18), ("M102", 19))
        ),
    )
    november = matrix.Chapter(
        "C002",
        "2007-11",
        (_dated_card("M201", datetime(2007, 11, 3, 9, 0, tzinfo=UTC)),),
    )
    readings = (
        (october, _chapter_reading("An October evening.")),
        (november, _chapter_reading("One November walk.")),
    )

    def answer_for(prompt: str) -> str:
        if "Allocate at most" in prompt:
            return _allocation_answer(("C001", 2), ("C002", 1))
        if "M101" in prompt:
            return _cut_answer(("M101", "M102"), [])
        return _cut_answer(("M201",), [])

    selection, _allocation, calls = _run_hierarchical(
        (october, november), readings, answer_for, 3, monkeypatch, tmp_path
    )

    assert [row["moment_id"] for row in selection["keep"]] == ["M101", "M102", "M201"]
    # One allocation and two chapter cuts: nothing was capped, so nothing is pooled
    # and no chapter is re-asked.
    assert len(calls) == 3
    assert selection["global_reallocation"] == {"pooled": 0}


def test_one_pooled_slot_serves_the_cited_chapter_and_not_the_starved_one(
    monkeypatch, tmp_path: Path
) -> None:
    # Two chapters can both spend another slot: November's reading argues from two
    # rejected moments, December rejected a favourite in a starved occasion. One
    # slot was freed, so the cited chapter is served once and December not at all.
    october = matrix.Chapter(
        "C001",
        "2007-10",
        tuple(
            _dated_card(alias, datetime(2007, 10, 11, hour, 0, tzinfo=UTC))
            for alias, hour in (("M101", 18), ("M102", 19), ("M103", 20))
        ),
    )
    november = matrix.Chapter(
        "C002",
        "2007-11",
        tuple(
            _dated_card(alias, datetime(2007, 11, day, 9, 0, tzinfo=UTC))
            for alias, day in (("M201", 3), ("M202", 10), ("M203", 17))
        ),
    )
    december = matrix.Chapter(
        "C003",
        "2007-12",
        (
            _dated_card("M301", datetime(2007, 12, 1, 9, 0, tzinfo=UTC)),
            _dated_card("M302", datetime(2007, 12, 5, 9, 0, tzinfo=UTC), favourite=True),
        ),
    )
    readings = (
        (october, _chapter_reading("An October of one long evening.")),
        (november, _chapter_reading("November turns on the walk.", ("M202", "M203"))),
        (december, _chapter_reading("December closes the year.")),
    )
    november_prompts: list[str] = []

    def answer_for(prompt: str) -> str:
        if "Allocate at most" in prompt:
            return _allocation_answer(("C001", 3), ("C002", 1), ("C003", 1))
        if "M101" in prompt:
            return _cut_answer(("M101", "M102", "M103"), [])
        if "M301" in prompt:
            return _cut_answer(
                ("M301",),
                [
                    {
                        "kept_moment_id": "M301",
                        "rejected_moment_id": "M302",
                        "reason": "The first December scene carries the visible action.",
                    }
                ],
            )
        november_prompts.append(prompt)
        if len(november_prompts) == 1:
            return _cut_answer(
                ("M201",),
                [
                    {
                        "kept_moment_id": "M201",
                        "rejected_moment_id": "M203",
                        "reason": "The first walk shows the season turning.",
                    }
                ],
            )
        return _cut_answer(
            ("M202",),
            [
                {
                    "kept_moment_id": "M202",
                    "rejected_moment_id": "M203",
                    "reason": "The cited walk carries the thread the reading argues.",
                }
            ],
        )

    selection, _allocation, calls = _run_hierarchical(
        (october, november, december), readings, answer_for, 5, monkeypatch, tmp_path
    )

    assert [row["moment_id"] for row in selection["keep"]] == [
        "M101",
        "M102",
        "M201",
        "M202",
        "M301",
    ]
    # One allocation, three cuts, one granted re-ask: December is never asked.
    assert len(calls) == 5
    assert selection["global_reallocation"]["pooled"] == 1
    assert selection["global_reallocation"]["grants"] == [
        {
            "chapter_id": "C002",
            "reason": "reading_evidence",
            "unserved_moment_ids": ["M202", "M203"],
            "slots": 1,
        }
    ]
    assert [row["chapter_id"] for row in selection["global_reallocation"]["rounds"]] == ["C002"]
    assert selection["global_reallocation"]["forfeits"] == []


def test_fused_card_prompt_offers_hedged_people_relations_activity_and_setting_fields() -> None:
    prompt = matrix._fused_card_prompt(_described_moment(), facts={})

    assert '"people":"who is visible, or insufficient evidence"' in prompt
    assert '"relations":"how they are related, or insufficient evidence"' in prompt
    assert '"activity":"what they are doing, or insufficient evidence"' in prompt
    assert '"setting":"where this takes place, or insufficient evidence"' in prompt
    assert (
        "Write exactly insufficient evidence for people, relations, activity, "
        "or setting the visuals do not show." in prompt
    )
    assert matrix.FUSED_CARD_PASS_VERSION == "fused-moment-card-v3"  # noqa: S105
    assert matrix.FUSED_CARD_PROMPT_VERSION == "fused-moment-card-prompt-v3"


def _hedged_answer(schema_version: str, *, setting: str = "insufficient evidence") -> str:
    return json.dumps(
        {
            "schema_version": schema_version,
            "summary": "A compact literal scene.",
            "people": "insufficient evidence",
            "relations": "insufficient evidence",
            "activity": "A cyclist rides past a hedge.",
            "setting": setting,
        }
    )


def _fused_cards_for(answers: tuple[str, ...], monkeypatch, tmp_path: Path):
    """Run the real card builder over one canned answer per moment."""
    groups = tuple(
        EditorialGroup(f"group-{index}", _moment(index).candidates)
        for index in range(1, len(answers) + 1)
    )
    replies = iter(answers)

    class Requester:
        def ask(self, _request):
            return SimpleNamespace(
                raw_text=next(replies),
                provenance=SimpleNamespace(cache_hit=False),
            )

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    return asyncio.run(
        matrix._build_fused_cards(
            groups,
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )


def test_fused_card_reader_accepts_hedged_answers_in_both_schema_versions(
    monkeypatch, tmp_path: Path
) -> None:
    cards, calls = _fused_cards_for(
        (_hedged_answer(matrix.prototype.CARD_SCHEMA), _hedged_answer(matrix.RETIRED_CARD_SCHEMA)),
        monkeypatch,
        tmp_path,
    )

    assert [card.summary for card in cards] == ["A compact literal scene."] * 2
    assert calls[0].warning is None
    assert calls[1].warning == (
        "card answer echoed the retired schema version description-moment-card-v1"
    )


def test_a_named_card_setting_reaches_the_card_text_the_moment_cut_reads(
    monkeypatch, tmp_path: Path
) -> None:
    answer = _hedged_answer(matrix.prototype.CARD_SCHEMA, setting="a snow-covered ski station")

    cards, calls = _fused_cards_for((answer,), monkeypatch, tmp_path)

    assert cards[0].summary == "A compact literal scene. — setting: a snow-covered ski station"
    assert calls[0].warning is None


def test_a_five_key_card_without_the_setting_slot_is_read_with_a_warning(
    monkeypatch, tmp_path: Path
) -> None:
    retired = json.dumps(
        {
            "schema_version": matrix.prototype.CARD_SCHEMA,
            "summary": "A compact literal scene.",
            "people": "insufficient evidence",
            "relations": "insufficient evidence",
            "activity": "A cyclist rides past a hedge.",
        }
    )

    cards, calls = _fused_cards_for((retired,), monkeypatch, tmp_path)

    assert cards[0].summary == "A compact literal scene."
    assert calls[0].warning == "card answer used the retired setting-free card envelope"


def test_fused_card_banks_the_prompt_hash_and_version_tags_beside_the_card(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = replace(_moment(1).candidates[0], grounded_annotations=("place: a named park",))
    group = EditorialGroup("group-1", (candidate,))
    seen = []

    class Requester:
        def ask(self, request):
            seen.append(request)
            return SimpleNamespace(
                raw_text=_hedged_answer(matrix.prototype.CARD_SCHEMA),
                provenance=SimpleNamespace(cache_hit=False),
            )

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    _cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    provenance = matrix._call_record(calls[0])["prompt_provenance"]
    assert provenance == {
        "prompt_sha256": matrix._fused_card_provenance(seen[0])["prompt_sha256"],
        "pass_version": matrix.FUSED_CARD_PASS_VERSION,
        "prompt_version": matrix.FUSED_CARD_PROMPT_VERSION,
        "render_version": matrix.FUSED_CARD_RENDER_VERSION,
        "schema_version": matrix.prototype.CARD_SCHEMA,
        "grounded_annotations": ["place: a named park"],
    }
    assert provenance["prompt_sha256"] != matrix._fused_card_provenance(
        replace(seen[0], grounded_annotations=())
    )["prompt_sha256"]


def _occasion_candidate(
    asset_id: str,
    when: datetime,
    *,
    favourite: bool,
    person: str,
) -> EditorialCandidate:
    asset = make_asset(asset_id, file_created_at=when)
    asset.is_favorite = favourite
    asset.people = [Person(id=f"person-{person}", name=person)]
    return EditorialCandidate(
        asset_id=asset.id,
        taken_at=when,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=favourite,
        source=asset,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )


def _occasion_card(
    alias: str,
    candidates: tuple[EditorialCandidate, ...],
) -> prototype.MomentCard:
    return prototype.MomentCard(
        prototype.Moment(alias, EditorialGroup(f"group-{alias}", candidates), ()),
        f"Lived scene {alias}.",
        None,
    )


def _scene_chapter() -> tuple[tuple[prototype.MomentCard, ...], dict[str, int]]:
    """One chapter: ten photographed days of changing scenes, then one dense evening weeks later.

    Both runs hold twenty assets and four favourites, so only span, photographed days,
    moment count, people breadth, and scene diversity can separate them.
    """
    cards: list[prototype.MomentCard] = []
    scene_by_asset: dict[str, int] = {}
    names = ("Ada", "Bo", "Cy")
    for day in range(10):
        start = datetime(2021, 6, 1, 10, tzinfo=UTC) + timedelta(days=day)
        members = []
        for index in range(2):
            candidate = _occasion_candidate(
                f"long-{day}-{index}",
                start + timedelta(minutes=index),
                favourite=day < 4 and index == 0,
                person=names[day % len(names)],
            )
            scene_by_asset[candidate.asset_id] = day
            members.append(candidate)
        cards.append(_occasion_card(f"L{day:03d}", tuple(members)))
    for slot in range(4):
        start = datetime(2021, 7, 1, 20, tzinfo=UTC) + timedelta(minutes=20 * slot)
        members = []
        for index in range(5):
            candidate = _occasion_candidate(
                f"dense-{slot}-{index}",
                start + timedelta(seconds=10 * index),
                favourite=index == 0,
                person="Ada",
            )
            scene_by_asset[candidate.asset_id] = 10
            members.append(candidate)
        cards.append(_occasion_card(f"D{slot:03d}", tuple(members)))
    return tuple(cards), scene_by_asset


def _bank_scene_embeddings(directory: Path, scene_by_asset: dict[str, int]) -> None:
    asset_ids = sorted(scene_by_asset)
    basis = np.eye(max(scene_by_asset.values()) + 1, dtype=np.float32)
    np.save(directory / "embeddings.npy", np.stack([basis[scene_by_asset[a]] for a in asset_ids]))
    (directory / "ids.json").write_text(json.dumps(asset_ids))


def test_occasion_facts_separate_a_long_varied_run_from_one_equally_dense_day(
    monkeypatch, tmp_path: Path
) -> None:
    cards, scene_by_asset = _scene_chapter()
    _bank_scene_embeddings(tmp_path, scene_by_asset)
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path))

    rows = occasion_facts.chapter_occasions(cards)

    assert [row["first_day"] for row in rows] == ["2021-06-01", "2021-07-01"]
    assert [row["last_day"] for row in rows] == ["2021-06-10", "2021-07-01"]
    assert [row["span_days"] for row in rows] == [10, 1]
    assert [row["photographed_days"] for row in rows] == [10, 1]
    assert [row["moments"] for row in rows] == [10, 4]
    assert [row["assets"] for row in rows] == [20, 20]
    assert [row["favourites"] for row in rows] == [4, 4]
    assert [row["people_breadth"] for row in rows] == [3, 1]
    assert [row["scene_diversity"] for row in rows] == [
        "10 clusters/100% embedded",
        "1 clusters/100% embedded",
    ]


def _occasion_block_rows(prompt: str) -> Any:
    return json.loads(prompt.split("OCCASION FACTS\n", 1)[1].splitlines()[0])


def _plain_reading(thesis_text: str) -> dict[str, Any]:
    return {
        "thesis": thesis_text,
        "sustained_threads": [],
        "turning_points": [],
        "ordinary_texture": [],
    }


def test_both_editorial_prompts_carry_the_occasion_rows_and_stay_parseable(
    monkeypatch, tmp_path: Path
) -> None:
    cards, scene_by_asset = _scene_chapter()
    _bank_scene_embeddings(tmp_path, scene_by_asset)
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path))
    chapters = (
        matrix.Chapter("C001", "C001", cards[:10]),
        matrix.Chapter("C002", "C002", cards[10:]),
    )
    readings = (
        (chapters[0], _plain_reading("Days of changing ground.")),
        (chapters[1], _plain_reading("One evening indoors.")),
    )
    prompts: list[str] = []

    async def scripted_call(prompt, **_kwargs):
        prompts.append(prompt)
        if "Allocate at most" in prompt:
            return matrix.TextCall(
                prompt,
                json.dumps(
                    {
                        "schema_version": matrix.ALLOCATION_SCHEMA,
                        "allocations": [
                            {"chapter_id": "C001", "slots": 1, "reason": "The run carries most."},
                            {"chapter_id": "C002", "slots": 1, "reason": "The evening answers it."},
                        ],
                        "overall_reason": "Scarcity split across both runs.",
                    }
                ),
                0.1,
                False,
                False,
            )
        kept, dropped = ("L000", "L001") if "Lived scene L000." in prompt else ("D000", "D001")
        return matrix.TextCall(
            prompt,
            json.dumps(
                {
                    "schema_version": prototype.SELECTION_SCHEMA,
                    "keep": [{"moment_id": kept, "reason": "It carries the beat."}],
                    "audit_summary": "One moment earns the scarce slot.",
                    "comparisons": [
                        {
                            "kept_moment_id": kept,
                            "rejected_moment_id": dropped,
                            "reason": "The kept scene shows the action on screen.",
                        }
                    ],
                    "overall_reason": "The chapter keeps its defining beat.",
                }
            ),
            0.1,
            False,
            False,
        )

    monkeypatch.setattr(matrix, "_ask_text", scripted_call)
    selection, _allocation, _calls = asyncio.run(
        matrix._hierarchical_selection(
            chapters,
            readings,
            case=matrix.Case(
                key="case",
                label="A year memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis=_plain_reading("A year of one long run and one dense evening."),
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
            thinking=False,
        )
    )

    allocation_prompt = next(p for p in prompts if "Allocate at most" in p)
    long_prompt = next(p for p in prompts if "Lived scene L000." in p)
    dense_prompt = next(p for p in prompts if "Lived scene D000." in p)
    long_row = occasion_facts.chapter_occasions(cards[:10])[0]
    dense_row = occasion_facts.chapter_occasions(cards[10:])[0]

    assert _occasion_block_rows(allocation_prompt) == [
        {"chapter_id": "C001", "occasions": [long_row]},
        {"chapter_id": "C002", "occasions": [dense_row]},
    ]
    assert _occasion_block_rows(long_prompt) == [long_row]
    assert _occasion_block_rows(dense_prompt) == [dense_row]
    assert long_prompt.count("MOMENT WALL") == 1
    assert [row["moment_id"] for row in selection["keep"]] == ["L000", "D000"]


def test_both_editorial_prompts_state_the_duration_derived_ideal_asset_count(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path))
    cards, _scene_by_asset = _scene_chapter()
    chapters = (matrix.Chapter("C001", "C001", cards),)
    thesis = _plain_reading("One long run and one dense evening.")
    readings = ((chapters[0], thesis),)

    def rendered(target_seconds: float) -> tuple[str, str]:
        case = matrix.Case(
            key="case",
            label="A memory",
            product="year_in_review",
            ranges=(),
            target_seconds=target_seconds,
            brief="Make the strongest truthful memory.",
        )
        return (
            matrix._allocation_prompt(case, thesis, readings, 40, {}),
            matrix._selection_prompt(cards, case=case, facts={}, thesis=thesis, capacity=5),
        )

    ten_minutes = rendered(600.0)
    five_minutes = rendered(300.0)

    assert all('"ideal_assets":100' in prompt for prompt in ten_minutes)
    assert all('"ideal_assets":50' in prompt for prompt in five_minutes)


def test_an_unreadable_embedding_bank_omits_scene_diversity_without_failing(
    monkeypatch, tmp_path: Path
) -> None:
    cards, _scene_by_asset = _scene_chapter()
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path / "never-written"))

    rows = occasion_facts.chapter_occasions(cards)

    assert [row["photographed_days"] for row in rows] == [10, 1]
    assert [row["assets"] for row in rows] == [20, 20]
    assert all("scene_diversity" not in row for row in rows)


def test_assets_outside_the_bank_are_skipped_and_the_coverage_is_stated(
    monkeypatch, tmp_path: Path
) -> None:
    cards, scene_by_asset = _scene_chapter()
    banked = {
        asset_id: scene
        for asset_id, scene in scene_by_asset.items()
        if asset_id.startswith("dense-") and asset_id.endswith(("-0", "-1"))
    }
    _bank_scene_embeddings(tmp_path, {**banked, "unrelated-asset": 10})
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path))

    rows = occasion_facts.chapter_occasions(cards)

    assert [row["scene_diversity"] for row in rows] == [
        "0 clusters/0% embedded",
        "1 clusters/40% embedded",
    ]


def test_a_wall_row_shows_a_named_setting_and_drops_a_hedged_one() -> None:
    """The place inside a people-photo has to reach the row, or the wall stays all faces."""
    from probe_selection_final_cut import FineCutCandidate

    def row(setting: str) -> str:
        return FineCutCandidate(
            alias="A001",
            asset_id="private-1",
            moment_id="M001",
            taken_at=START,
            media_kind="photo",
            favourite=False,
            description=matrix._wall_description(
                SimpleNamespace(text="a man in a red and black jacket", setting=setting)
            ),
        ).wall_line()

    assert row("a snow-covered ski station").endswith(
        "| a man in a red and black jacket — setting: a snow-covered ski station"
    )
    assert row("insufficient evidence").endswith("| a man in a red and black jacket")
