"""Tests for pipeline efficiency: density budget tightening + unified photo budget."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from immich_memories.cache.versions import ANALYSIS_VERSION
from immich_memories.config_loader import Config


class TestDensityBudgetCap:
    """Phase 2 filtering should cap analysis candidates at 1.5x target_clips."""

    def _make_clip(
        self, asset_id: str, is_favorite: bool = False, width: int = 1920, height: int = 1080
    ):
        from immich_memories.api.models import Asset, VideoClipInfo

        now = datetime.now(tz=UTC)
        asset = Asset(
            id=asset_id,
            type="VIDEO",
            fileCreatedAt=now,
            fileModifiedAt=now,
            updatedAt=now,
            isFavorite=is_favorite,
            exifInfo={"make": "Apple", "model": "iPhone"},
        )
        return VideoClipInfo(
            asset=asset,
            duration_seconds=5.0,
            width=width,
            height=height,
        )

    def test_caps_at_1_5x_target_clips(self):
        """With 200 clips and target_clips=40, returns at most 60."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=40)
        # WHY: mock services — we're testing _phase_filter logic, not Immich
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(200)]
        result = pipeline._phase_filter(clips)
        assert len(result) <= 60  # 1.5x cap

    def test_favorites_preserved_when_cap_hit(self):
        """All favorites should survive the cap."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=20)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        favorites = [self._make_clip(f"fav{i}", is_favorite=True) for i in range(15)]
        non_favorites = [self._make_clip(f"nonfav{i}") for i in range(100)]
        clips = favorites + non_favorites

        result = pipeline._phase_filter(clips)
        fav_ids = {c.asset.id for c in result if c.asset.is_favorite}
        assert len(fav_ids) == 15  # All favorites kept

    def test_analyze_all_mode_bypasses_cap(self):
        """analyze_all=True should return all clips, no cap."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=20, analyze_all=True)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(100)]
        result = pipeline._phase_filter(clips)
        assert len(result) == 100

    def test_reduced_multiplier_selects_fewer(self):
        """With raw_multiplier=1.3, fewer clips selected than with 2.0."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=40)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(200)]
        result = pipeline._phase_filter(clips)
        # With 1.3x multiplier + 1.5x cap, should be well under 200
        assert len(result) < 100

    def test_short_target_logs_the_effective_positive_raw_budget(self):
        """The diagnostic must report the calculator's capped-overhead budget."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=10, target_duration_seconds=48.5)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )
        clips = [self._make_clip(f"clip{i}") for i in range(20)]

        with patch("immich_memories.analysis.density_budget.log_budget_summary") as summary:
            pipeline._phase_filter(clips)

        buckets, logged_budget = summary.call_args.args
        assert logged_budget > 0
        assert logged_budget == pytest.approx(sum(bucket.quota_seconds for bucket in buckets))

    def test_density_budget_counts_usable_excerpt_not_full_source_duration(self):
        """A five-minute source consumes one planned excerpt in the analysis budget."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )
        clip = self._make_clip("five-minute-source")
        clip.duration_seconds = 300.0

        with patch(
            "immich_memories.analysis.density_budget.compute_density_budget",
            return_value=[],
        ) as compute:
            pipeline._phase_filter([clip])

        assert compute.call_args.kwargs["assets"][0].duration == 5.0


class TestAnalysisEligibility:
    def _pipeline(self, tmp_path: Path, **config_kwargs):
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        analysis_cache = MagicMock()
        analysis_cache.get_analysis.return_value = None
        return SmartPipeline(
            client=MagicMock(),
            analysis_cache=analysis_cache,
            thumbnail_cache=MagicMock(),
            config=PipelineConfig(**config_kwargs),
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=Config(
                cache={"directory": str(tmp_path / "cache")},
                llm={"model": "qwen-3.6"},
                content_analysis={"enabled": True},
            ),
        )

    def test_auto_analyzes_every_cache_miss_in_a_manageable_pool(self, tmp_path: Path) -> None:
        pipeline = self._pipeline(tmp_path, analysis_depth="auto")
        clips = [TestDensityBudgetCap()._make_clip(f"clip-{index}") for index in range(41)]
        pipeline._phase_cluster = MagicMock(return_value=clips)
        pipeline._phase_filter = MagicMock(side_effect=AssertionError("must not shortlist"))
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[])
        pipeline.analyzer.plan_cached_or_metadata = MagicMock(return_value=[])

        pipeline.run_analysis(clips)

        pipeline._analyze_with_cache_batch.assert_called_once_with(clips)
        assert pipeline.last_deep_analysis_count == 41
        assert pipeline.config.analysis_depth == "thorough"

    def test_auto_shortlists_large_cache_miss_pools_but_uses_llm_for_shortlist(
        self, tmp_path: Path
    ) -> None:
        pipeline = self._pipeline(tmp_path, analysis_depth="auto")
        clips = [TestDensityBudgetCap()._make_clip(f"clip-{index}") for index in range(100)]
        shortlisted = clips[:30]
        pipeline._phase_cluster = MagicMock(return_value=clips)
        pipeline._phase_filter = MagicMock(return_value=shortlisted)
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[])
        pipeline.analyzer.plan_cached_or_metadata = MagicMock(return_value=[])

        pipeline.run_analysis(clips)

        pipeline._phase_filter.assert_called_once_with(clips, hard_filtered=True)
        pipeline._analyze_with_cache_batch.assert_called_once_with(shortlisted)
        assert pipeline.last_deep_analysis_count == 30
        assert pipeline.config.analysis_depth == "thorough"

    def test_auto_budgets_current_model_cache_misses_not_total_assets(self, tmp_path: Path) -> None:
        pipeline = self._pipeline(tmp_path, analysis_depth="auto")
        clips = [TestDensityBudgetCap()._make_clip(f"clip-{index}") for index in range(100)]

        def cached_or_missing(asset_id: str):
            if int(asset_id.removeprefix("clip-")) < 50:
                return MagicMock(
                    model_version="qwen-3.6",
                    analysis_version=ANALYSIS_VERSION,
                    segments=[MagicMock()],
                )
            return None

        pipeline.analysis_cache.get_analysis.side_effect = cached_or_missing
        pipeline._phase_cluster = MagicMock(return_value=clips)
        pipeline._phase_filter = MagicMock(side_effect=AssertionError("must not shortlist"))
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[])
        pipeline.analyzer.plan_cached_or_metadata = MagicMock(return_value=[])

        pipeline.run_analysis(clips)

        pipeline._analyze_with_cache_batch.assert_called_once_with(clips)
        assert pipeline.last_deep_analysis_count == 100

    def test_thorough_analyzes_every_eligible_clip_unconditionally(self, tmp_path: Path) -> None:
        pipeline = self._pipeline(tmp_path, analysis_depth="thorough")
        clips = [TestDensityBudgetCap()._make_clip(f"clip-{index}") for index in range(20)]
        pipeline._phase_cluster = MagicMock(return_value=clips)
        pipeline._phase_filter = MagicMock(side_effect=AssertionError("must not shortlist"))
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[])
        pipeline.analyzer.plan_cached_or_metadata = MagicMock(return_value=[])

        pipeline.run_analysis(clips)

        pipeline._analyze_with_cache_batch.assert_called_once_with(clips)
        assert pipeline.last_deep_analysis_count == 20

    def test_fast_analysis_shortlist_does_not_delete_eligible_leftovers(self, tmp_path: Path):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        pipeline = self._pipeline(
            tmp_path,
            target_clips=1,
            avg_clip_duration=5.0,
            analysis_depth="fast",
        )
        clips = [
            TestDensityBudgetCap()._make_clip("analyzed"),
            TestDensityBudgetCap()._make_clip("leftover-1"),
            TestDensityBudgetCap()._make_clip("leftover-2"),
        ]
        analyzed = ClipWithSegment(clips[0], 1.0, 5.0, 0.9)
        fallbacks = [
            ClipWithSegment(clips[1], 0.0, 5.0, 0.2),
            ClipWithSegment(clips[2], 0.0, 5.0, 0.1),
        ]
        pipeline._phase_cluster = MagicMock(return_value=clips)
        pipeline._phase_filter = MagicMock(return_value=[clips[0]])
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[analyzed])
        pipeline.analyzer.plan_cached_or_metadata = MagicMock(return_value=fallbacks)

        result = pipeline.run_analysis(clips)

        assert [item.clip.asset.id for item in result] == [
            "analyzed",
            "leftover-1",
            "leftover-2",
        ]
        assert pipeline.last_deep_analysis_count == 1
        pipeline.analyzer.plan_cached_or_metadata.assert_called_once_with(clips[1:])

    def test_doorbell_footage_never_reaches_selection(self, tmp_path: Path):
        """A real year recap shipped two clips of somebody's front door.

        A doorbell writes into the same timeline as the phone, carries no
        make or model, and reuses one filename across every export — so
        nothing downstream can tell it from footage somebody chose to shoot.
        """
        from immich_memories.config_models import AnalysisConfig

        pipeline = self._pipeline(tmp_path)
        pipeline._analysis_config = AnalysisConfig(exclude_filename_patterns=["RingVideo_*"])
        doorbell = TestDensityBudgetCap()._make_clip("doorbell")
        doorbell.asset.original_file_name = "RingVideo_6763648097558121116.mp4"
        shot = TestDensityBudgetCap()._make_clip("shot")
        shot.asset.original_file_name = "IMG_0809.MP4"

        result = pipeline._hard_eligible_clips([doorbell, shot])

        assert [clip.asset.id for clip in result] == ["shot"]

    def test_the_default_list_covers_what_the_camera_roll_did_not_shoot(self, tmp_path: Path):
        """Doorbells, screen recordings and forwarded video share the timeline.

        None of them was shot as a memory, and the filename is the only thing
        that says so before analysis has looked at anything — which is also
        what keeps them out of the analysis budget.
        """
        from immich_memories.config_models import AnalysisConfig

        pipeline = self._pipeline(tmp_path)
        pipeline._analysis_config = AnalysisConfig()
        names = [
            "RingVideo_6763648097558121116.mp4",
            "RPReplay_Final1560343200.mp4",
            "VID-20190612-WA0003.mp4",
            "Screen Recording 2019-06-12 at 10.00.00.mov",
            "IMG_0809.MP4",
        ]
        clips = []
        for index, name in enumerate(names):
            clip = TestDensityBudgetCap()._make_clip(f"clip-{index}")
            clip.asset.original_file_name = name
            clips.append(clip)

        result = pipeline._hard_eligible_clips(clips)

        assert [clip.asset.original_file_name for clip in result] == ["IMG_0809.MP4"]

    def test_the_source_filter_does_not_care_about_case(self, tmp_path: Path):
        """Exports differ in casing between platforms; the rule should not."""
        from immich_memories.config_models import AnalysisConfig

        pipeline = self._pipeline(tmp_path)
        pipeline._analysis_config = AnalysisConfig(exclude_filename_patterns=["ringvideo_*"])
        doorbell = TestDensityBudgetCap()._make_clip("doorbell")
        doorbell.asset.original_file_name = "RingVideo_1.MP4"

        assert pipeline._hard_eligible_clips([doorbell]) == []

    def test_hdr_only_is_a_hard_eligibility_rule_even_for_favorites(self, tmp_path: Path):
        pipeline = self._pipeline(tmp_path, hdr_only=True)
        hdr = TestDensityBudgetCap()._make_clip("hdr")
        hdr.color_transfer = "smpte2084"
        favorite_sdr = TestDensityBudgetCap()._make_clip("favorite-sdr", is_favorite=True)

        result = pipeline._hard_eligible_clips([hdr, favorite_sdr])

        assert [clip.asset.id for clip in result] == ["hdr"]


class TestSharedVideoCacheBatch:
    def test_pipeline_owns_and_injects_one_cache_batch(self, tmp_path: Path):
        """All analysis downloads share the pipeline's one active batch."""
        from immich_memories.analysis.smart_pipeline import SmartPipeline

        cache = MagicMock()
        batch = MagicMock()
        cache.begin_batch.return_value.__enter__.return_value = batch
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            analysis_config=MagicMock(),
            app_config=Config(cache={"directory": str(tmp_path / "cache")}),
        )
        pipeline._video_cache = cache
        pipeline.analyzer._video_cache = cache
        pipeline.previewer._video_cache = cache
        pipeline.analyzer.bind_cache_batch = MagicMock()
        pipeline.previewer.bind_cache_batch = MagicMock()
        pipeline.analyzer.phase_analyze = MagicMock(return_value=[])

        result = pipeline._analyze_with_cache_batch([])

        assert result == []
        cache.begin_batch.assert_called_once()
        pipeline.analyzer.bind_cache_batch.assert_has_calls([call(batch), call(None)])
        pipeline.previewer.bind_cache_batch.assert_has_calls([call(batch), call(None)])

    def test_pipeline_releases_shared_batch_after_analysis_failure(self, tmp_path: Path):
        """A failed item cannot strand the active cache batch on the pipeline."""
        from immich_memories.analysis.smart_pipeline import SmartPipeline

        cache = MagicMock()
        batch = MagicMock()
        cache.begin_batch.return_value.__enter__.return_value = batch
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            analysis_config=MagicMock(),
            app_config=Config(cache={"directory": str(tmp_path / "cache")}),
        )
        pipeline._video_cache = cache
        pipeline.analyzer.bind_cache_batch = MagicMock()
        pipeline.previewer.bind_cache_batch = MagicMock()
        pipeline.analyzer.phase_analyze = MagicMock(side_effect=RuntimeError("analysis failed"))

        with pytest.raises(RuntimeError, match="analysis failed"):
            pipeline._analyze_with_cache_batch([])

        cache.begin_batch.return_value.__exit__.assert_called_once()
        pipeline.analyzer.bind_cache_batch.assert_has_calls([call(batch), call(None)])
        pipeline.previewer.bind_cache_batch.assert_has_calls([call(batch), call(None)])

    def test_pipeline_closes_analysis_services_without_cache_after_failure(self, tmp_path: Path):
        """No-cache analysis failures still release reusable native/model services."""
        from immich_memories.analysis.smart_pipeline import SmartPipeline

        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            analysis_config=MagicMock(),
            app_config=Config(cache={"directory": str(tmp_path / "cache")}),
        )
        pipeline._phase_cluster = MagicMock(return_value=[])
        pipeline._phase_filter = MagicMock(return_value=[])
        pipeline._analyze_with_cache_batch = MagicMock(side_effect=RuntimeError("analysis failed"))
        pipeline.analyzer.close = MagicMock()
        pipeline.previewer.close = MagicMock()

        with pytest.raises(RuntimeError, match="analysis failed"):
            pipeline.run_analysis([])

        pipeline.analyzer.close.assert_called_once()
        pipeline.previewer.close.assert_called_once()

    def test_analysis_failure_retains_analysis_phase(self, tmp_path: Path):
        """A failed analysis must not be reported as completed selection work."""
        from immich_memories.analysis.progress import PipelinePhase
        from immich_memories.analysis.smart_pipeline import SmartPipeline
        from immich_memories.operations.phases import OperationalPhase

        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            analysis_config=MagicMock(),
            app_config=Config(cache={"directory": str(tmp_path / "cache")}),
        )
        pipeline._phase_cluster = MagicMock(return_value=[])
        pipeline._phase_filter = MagicMock(return_value=[])

        def fail_during_analysis(_candidates):
            pipeline.tracker.start_phase(PipelinePhase.ANALYZING, 1)
            raise RuntimeError("analysis failed")

        pipeline._analyze_with_cache_batch = MagicMock(side_effect=fail_during_analysis)

        with pytest.raises(RuntimeError, match="analysis failed"):
            pipeline.run_analysis([])

        assert pipeline.tracker.progress.phase is PipelinePhase.ANALYZING
        assert pipeline.tracker.progress.operational_event is not None
        assert pipeline.tracker.progress.operational_event.phase is OperationalPhase.ANALYSIS

    def test_pipeline_closes_analysis_services_once_after_success(self, tmp_path: Path):
        """The successful no-cache path has the same one-batch teardown ownership."""
        from immich_memories.analysis.smart_pipeline import SmartPipeline

        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            analysis_config=MagicMock(),
            app_config=Config(cache={"directory": str(tmp_path / "cache")}),
        )
        pipeline._phase_cluster = MagicMock(return_value=[])
        pipeline._phase_filter = MagicMock(return_value=[])
        pipeline._analyze_with_cache_batch = MagicMock(return_value=[])
        pipeline.analyzer.close = MagicMock()
        pipeline.previewer.close = MagicMock()

        assert pipeline.run_analysis([]) == []

        pipeline.analyzer.close.assert_called_once()
        pipeline.previewer.close.assert_called_once()


class TestUnifiedPhotoBudget:
    """Photo rendering should always use unified budget, never legacy render-all."""

    def test_add_photos_without_target_duration_uses_unified(self):
        """Even without target_duration_seconds, unified budget is used."""
        from immich_memories.generate import _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path="/fake/clip.mp4",
                duration=5.0,
                asset_id="a1",
                date="2025-01-15",
            ),
        ]

        params = MagicMock()
        params.include_photos = True
        params.photo_assets = [MagicMock()]
        params.target_duration_seconds = None  # Not set (UI path before fix)
        params.selected_photo_ids = None  # No pre-selection → fallback path
        params.progress_callback = None

        with patch(
            "immich_memories.generate_photos._apply_unified_budget",
            return_value=(clips, []),
        ) as mock_unified:
            _add_photos_if_enabled(clips, params, MagicMock())

        mock_unified.assert_called_once()

    def test_add_photos_with_target_duration_uses_unified(self):
        """With target_duration_seconds set, unified budget is used."""
        from immich_memories.generate import _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path="/fake/clip.mp4",
                duration=5.0,
                asset_id="a1",
                date="2025-01-15",
            ),
        ]

        params = MagicMock()
        params.include_photos = True
        params.photo_assets = [MagicMock()]
        params.target_duration_seconds = 120.0
        params.selected_photo_ids = None  # No pre-selection → fallback path
        params.progress_callback = None

        with patch(
            "immich_memories.generate_photos._apply_unified_budget",
            return_value=(clips, []),
        ) as mock_unified:
            _add_photos_if_enabled(clips, params, MagicMock())

        mock_unified.assert_called_once()

    def test_ui_sets_target_duration_seconds(self):
        """UI generation receives total runtime and the exact Step 2 plan."""
        from immich_memories.processing.timeline_budget import TimelinePlan
        from immich_memories.ui.pages._step4_generate import _build_generation_params

        state = MagicMock()
        state.target_duration = 5  # 5 minutes
        state.target_duration_seconds = 300.0
        state.timeline_plan = TimelinePlan(
            target_duration=300.0,
            content_budget=285.0,
            title_budget=15.0,
            title_duration=3.5,
            ending_duration=7.0,
            divider_duration=2.0,
            max_dividers=2,
        )
        state.generation_options = {}
        state.selected_person = None
        state.date_range = None
        state.memory_type = None
        state.memory_preset_params = {}
        state.title_suggestion_title = None
        state.title_suggestion_subtitle = None
        state.clip_segments = {}
        state.clip_rotations = {}
        state.include_photos = False
        state.photo_assets = None
        state.photo_duration = 4.0
        state.demo_mode = False
        state.immich_url = "http://fake:2283"
        state.immich_api_key = "fake-key"
        state.config = MagicMock()

        with patch("immich_memories.api.immich.SyncImmichClient"):
            params = _build_generation_params(state, [], MagicMock())

        assert params.target_duration_seconds == 300  # 5 min * 60
        assert params.timeline_plan is state.timeline_plan


class TestNothingIsJudgedBlind:
    """The review is told never to drop a clip for missing information.

    That rule is right — a third of a real pool has no analysis yet, and
    treating silence as a verdict would gut the memory. It also means an
    unanalysed clip is immune to the only quality judgment in the pipeline,
    so the answer is to leave nothing unanalysed rather than to loosen it.
    """

    def _pipeline(self, tmp_path: Path):
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
        from immich_memories.config_models import AnalysisConfig

        analysis_cache = MagicMock()
        analysis_cache.get_analysis.return_value = None
        return SmartPipeline(
            client=MagicMock(),
            analysis_cache=analysis_cache,
            thumbnail_cache=MagicMock(),
            config=PipelineConfig(),
            analysis_config=AnalysisConfig(),
            app_config=Config(
                cache={"directory": str(tmp_path / "cache")},
                llm={"model": "qwen-3.6"},
                content_analysis={"enabled": True},
            ),
        )

    def test_the_last_review_sees_every_clip_it_is_asked_to_judge(self, tmp_path: Path) -> None:
        """The refinement loop stops on its budget with its last refill unjudged.

        Those clips arrived at the final review with a bare line — date,
        place, score and nothing else — and survived on the very rule that
        protects genuinely unanalysed material.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult

        unseen = TestDensityBudgetCap()._make_clip("unseen")
        member = ClipWithSegment(clip=unseen, start_time=0.0, end_time=4.0, score=0.6)
        result = PipelineResult(
            selected_clips=[unseen], clip_segments={"unseen": (0.0, 4.0)}, errors=[]
        )

        pipeline = self._pipeline(tmp_path)
        looked_at = TestDensityBudgetCap()._make_clip("unseen")
        looked_at.llm_description = "a whiteboard covered in sticky notes"
        # WHY: analysis downloads and decodes video; this stands in for the look.
        pipeline.analyzer.phase_analyze = MagicMock(
            return_value=[ClipWithSegment(clip=looked_at, start_time=0.0, end_time=4.0, score=0.6)]
        )
        pipeline.refiner.phase_refine = MagicMock(return_value=result)

        judged: list = []

        def _capture(selected, _llm_config, **_kwargs):
            judged.extend(selected)
            return []

        # WHY: the review is an LLM call; what it is handed is the subject here.
        with patch("immich_memories.analysis.selection_review.review_selection", _capture):
            pipeline.quality.final_review_drop([member], result)

        assert [c.clip.llm_description for c in judged] == [
            "a whiteboard covered in sticky notes"
        ], "the review was handed a clip nobody had looked at"

    def test_a_photo_is_never_sent_to_the_video_analyzer(self, tmp_path: Path) -> None:
        """A photograph's real look is the photo scorer.

        Running the video analyzer over a still fails and writes back a zero,
        so a photo it could not read was not merely unseen — it was ranked
        last. The still still gets looked at; it just gets looked at by
        something that can see it.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult
        from immich_memories.api.models import AssetType

        still = TestDensityBudgetCap()._make_clip("still")
        still.asset.type = AssetType.IMAGE
        member = ClipWithSegment(clip=still, start_time=0.0, end_time=4.0, score=0.6)
        result = PipelineResult(
            selected_clips=[still], clip_segments={"still": (0.0, 4.0)}, errors=[]
        )

        pipeline = self._pipeline(tmp_path)
        pipeline.analyzer.phase_analyze = MagicMock(
            side_effect=AssertionError("a still reached the video analyzer")
        )
        # WHY: the VLM is the network boundary; this stands in for its look.
        with patch(
            "immich_memories.photos.photo_pipeline.look_at_selected_photos",
            return_value={"still": (0.44, {"description": "a plant against a wall"})},
        ):
            pipeline.quality.verify([member], result)

        assert still.llm_description == "a plant against a wall"

    def test_a_clip_that_failed_analysis_is_not_queued_again(self, tmp_path: Path) -> None:
        """The attempted set was local to one call, and the method is re-entered.

        Each stabilize pass and the final review each start a fresh set, so a
        clip whose analysis fails is downloaded and decoded again on every
        entry, for the same failure — and it can never come back with a
        description, so it is queued again for as long as it is selected.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult

        unseen = TestDensityBudgetCap()._make_clip("unseen")
        member = ClipWithSegment(clip=unseen, start_time=0.0, end_time=4.0, score=0.6)
        result = PipelineResult(
            selected_clips=[unseen], clip_segments={"unseen": (0.0, 4.0)}, errors=[]
        )

        pipeline = self._pipeline(tmp_path)
        # WHY: analysis downloads and decodes video; here it comes back blind.
        pipeline.analyzer.phase_analyze = MagicMock(return_value=[member])
        pipeline.refiner.phase_refine = MagicMock(return_value=result)

        pipeline.quality.verify([member], result)
        pipeline.quality.verify([member], result)

        assert pipeline.analyzer.phase_analyze.call_count == 1

    def test_a_selected_photo_nobody_looked_at_is_looked_at(self, tmp_path: Path) -> None:
        """Thirty of nearly two thousand photos reach the VLM shortlist.

        Selection picks from all of them, so most stills in a finished cut
        arrive at the review with no description — and the review is told
        never to drop a clip for missing information. A beer tap, a plant on a
        wall and an empty floor shipped in a year recap on exactly that.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineResult
        from immich_memories.api.models import AssetType

        still = TestDensityBudgetCap()._make_clip("unseen-still")
        still.asset.type = AssetType.IMAGE
        member = ClipWithSegment(clip=still, start_time=0.0, end_time=4.0, score=0.4)
        result = PipelineResult(
            selected_clips=[still], clip_segments={"unseen-still": (0.0, 4.0)}, errors=[]
        )

        pipeline = self._pipeline(tmp_path)
        # WHY: the VLM is the network boundary; this stands in for its look.
        with patch(
            "immich_memories.photos.photo_pipeline.look_at_selected_photos",
            return_value={"unseen-still": (0.31, {"description": "a beer tap on a bar"})},
        ):
            pipeline.quality.verify([member], result)

        assert still.llm_description == "a beer tap on a bar"
