"""Pass 3 — which moments does the story need? (#764, slice 2)

Selection narrowed 204 candidates to 13 by counting: a per-day photo cap, a
spread across dates, a fit to the runtime, two ratio caps. Not one of those
stages ever looked at what a moment was OF, and the measured funnel showed
them deleting a 0.80 and shipping a 0.36.

This pass asks one question at moment granularity instead, and these tests pin
what may and may not decide a kill. Numbers order the table and measure the
envelope; the model decides what goes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from immich_memories.analysis import selection_trace
from immich_memories.analysis.selection_structure import StructurePass
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_models_llm import LLMConfig

JUNE = datetime(2023, 6, 1, 12, tzinfo=UTC)
MOMENT_WINDOW = 10.0


def _clip(
    asset_id: str,
    *,
    days: float = 0.0,
    minutes: float = 0.0,
    starred: bool = False,
    score: float = 0.5,
    seconds: float = 5.0,
) -> ClipWithSegment:
    when = JUNE + timedelta(days=days, minutes=minutes)
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=starred,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=seconds)
    clip.llm_description = f"a shot called {asset_id}"
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=seconds, score=score)


def _pool(
    moments: int,
    per_moment: int = 2,
    scores: dict[int, float] | None = None,
) -> list[ClipWithSegment]:
    """`moments` days, each holding `per_moment` clips two minutes apart."""
    by_day = scores or {}
    return [
        _clip(f"m{day}c{index}", days=day, minutes=2 * index, score=by_day.get(day, 0.5))
        for day in range(1, moments + 1)
        for index in range(per_moment)
    ]


def _answer(keep: list[int], cut: list[int], release_order: list[int] | None = None) -> str:
    payload = {
        "keep": keep,
        "cut": [{"index": i, "reason": f"M{i} is not needed"} for i in cut],
        "release_order": keep[::-1] if release_order is None else release_order,
    }
    return json.dumps(payload)


def _choose(pool: list[ClipWithSegment], raw: str, *, target: float = 10.0):
    pass_ = StructurePass(LLMConfig())
    # WHY: the model. Everything under test is what we do with its answer.
    with patch("immich_memories.analysis.selection_structure._ask", return_value=raw):
        return pass_.choose(pool, target_duration=target, moment_window=MOMENT_WINDOW)


class TestTheStoryChoosesItsMoments:
    def test_a_cut_moment_takes_all_of_its_members_with_it(self):
        pool = _pool(moments=4)

        cut = _choose(pool, _answer(keep=[1, 2], cut=[3, 4]))

        assert [c.clip.asset.id for c in cut.kept] == ["m1c0", "m1c1", "m2c0", "m2c1"]
        assert cut.dropped == frozenset({"m3c0", "m3c1", "m4c0", "m4c1"})

    def test_every_member_of_a_cut_moment_carries_the_moment_reason(self):
        """The account answers "why is that not in there" for free from notes."""
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _choose(pool, _answer(keep=[1, 2], cut=[3, 4]))

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.notes["m3c0"] == "M3 is not needed"
        assert stage.notes["m3c1"] == "M3 is not needed"
        assert stage.notes["m4c0"] == "M4 is not needed"


class TestTheEnvelopeShrinksByTheEditorsOwnOrder:
    """A number may order the release; it may never pick the victim."""

    def test_an_over_budget_keep_set_releases_what_the_model_ranked_expendable(self):
        # The releases score high and the survivors score low: a shrink by
        # score would keep the exact opposite pair.
        pool = _pool(moments=5, scores={1: 0.9, 3: 0.9, 5: 0.9, 2: 0.1, 4: 0.1})

        cut = _choose(
            pool,
            _answer(keep=[1, 2, 3, 4, 5], cut=[], release_order=[3, 1, 5, 4, 2]),
            target=10.0,
        )

        assert {c.clip.asset.id for c in cut.kept} == {"m2c0", "m2c1", "m4c0", "m4c1"}

    def test_a_released_moment_says_which_budget_took_it(self):
        pool = _pool(moments=5)

        with selection_trace.tracing() as recorded:
            _choose(
                pool,
                _answer(keep=[1, 2, 3, 4, 5], cut=[], release_order=[3, 1, 5, 4, 2]),
                target=10.0,
            )

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.notes["m3c0"] == (
            "released to fit the 10s budget (the editor's own release order)"
        )

    def test_no_release_order_falls_back_to_the_keep_list_reversed(self):
        """The model listed its keeps in story order, so later is looser."""
        pool = _pool(moments=5)
        answered = json.dumps({"keep": [1, 2, 3, 4, 5], "cut": []})

        cut = _choose(pool, answered, target=10.0)

        assert {c.clip.asset.id for c in cut.kept} == {"m1c0", "m1c1", "m2c0", "m2c1"}

    def test_a_release_order_that_names_something_uncut_is_not_an_order(self):
        """Half a ranking spliced onto a fallback is two rankings, not one."""
        pool = _pool(moments=5)

        cut = _choose(
            pool,
            _answer(keep=[1, 2, 3, 4, 5], cut=[], release_order=[3, 9]),
            target=10.0,
        )

        assert {c.clip.asset.id for c in cut.kept} == {"m1c0", "m1c1", "m2c0", "m2c1"}

    def test_an_answer_inside_the_envelope_releases_nothing(self):
        pool = _pool(moments=5)

        cut = _choose(pool, _answer(keep=[1, 2], cut=[3, 4, 5]), target=10.0)

        assert {c.clip.asset.id for c in cut.kept} == {"m1c0", "m1c1", "m2c0", "m2c1"}


def _episode_pool(starred: set[str]) -> list[ClipWithSegment]:
    """Two moments half an hour apart — one occasion — then two lone days."""
    return [
        _clip("e1a", days=1, minutes=0, starred="e1a" in starred),
        _clip("e1b", days=1, minutes=2, starred="e1b" in starred),
        _clip("e1late", days=1, minutes=30, starred="e1late" in starred),
        _clip("day2", days=2, starred="day2" in starred),
        _clip("day3", days=3, starred="day3" in starred),
    ]


class TestTheOwnersMarksSurviveTheirOccasion:
    """A star loses its place only to another star of the same occasion."""

    def test_cutting_an_occasions_only_starred_moment_is_vetoed(self):
        pool = _episode_pool(starred={"e1late"})

        cut = _choose(pool, _answer(keep=[1, 3], cut=[2, 4]), target=15.0)

        assert "e1late" in {c.clip.asset.id for c in cut.kept}
        assert cut.dropped == frozenset({"day3"})

    def test_the_veto_is_on_the_record(self):
        pool = _episode_pool(starred={"e1late"})

        with selection_trace.tracing() as recorded:
            _choose(pool, _answer(keep=[1, 3], cut=[2, 4]), target=15.0)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert any("E1" in reason and "M2" in reason for reason in stage.reasons)

    def test_a_star_may_lose_to_a_star_of_the_same_occasion(self):
        pool = _episode_pool(starred={"e1a", "e1late"})

        cut = _choose(pool, _answer(keep=[1, 3], cut=[2, 4]), target=15.0)

        assert "e1late" in cut.dropped

    def test_the_envelope_walk_will_not_release_an_occasions_last_star(self):
        pool = _episode_pool(starred={"e1late"})

        cut = _choose(
            pool,
            _answer(keep=[1, 2, 3, 4], cut=[], release_order=[2, 4, 3, 1]),
            target=10.0,
        )

        assert "e1late" in {c.clip.asset.id for c in cut.kept}
        assert cut.dropped == frozenset({"day2", "day3"})


def _wired(*, target_clips: int = 2, dedup_window: float = MOMENT_WINDOW):
    """A refiner with the structure pass injected, and its progress tracker."""
    from unittest.mock import MagicMock

    from immich_memories.analysis.clip_refiner import ClipRefiner
    from immich_memories.analysis.clip_scaler import ClipScaler
    from immich_memories.analysis.smart_pipeline import PipelineConfig

    # WHY: ProgressTracker is the terminal/UI progress boundary; refinement
    # only reports into it and reads back the error list it collected.
    tracker = MagicMock()
    tracker.progress = MagicMock()
    tracker.progress.errors = []
    refiner = ClipRefiner(
        PipelineConfig(target_clips=target_clips, temporal_dedup_window_minutes=dedup_window),
        ClipScaler(),
        structure=StructurePass(LLMConfig()),
    )
    return refiner, tracker


def _refine(pool: list[ClipWithSegment], raw: str, *, target_clips: int = 2):
    """Run the refinement phase with the structure pass wired in."""
    refiner, tracker = _wired(target_clips=target_clips)
    # WHY: the model. Everything under test is what the pipeline does with it.
    with patch("immich_memories.analysis.selection_structure._ask", return_value=raw):
        return refiner.phase_refine(pool, tracker)


class TestAnUnreadableAnswerHandsTheCutBack:
    def test_an_answer_that_leaves_moments_unaccounted_for_is_refused_wholesale(self):
        """Silence is a kill under keep-semantics, so a partial answer is none."""
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _refine(pool, '{"keep": [1], "cut": []}')

        assert not any(stage.name == "structure" for stage in recorded.stages)
        assert any(stage.name == "per-day photo cap" for stage in recorded.stages)

    def test_the_trace_says_the_funnel_made_this_cut(self):
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _refine(pool, '{"keep": [1], "cut": []}')

        assert "!! the structure pass never ran" in recorded.report()

    def test_a_decided_structure_replaces_the_counting_stages(self):
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _refine(pool, _answer(keep=[1, 2], cut=[3, 4]))

        names = [stage.name for stage in recorded.stages]
        assert "structure" in names
        assert "per-day photo cap" not in names
        assert "distribute by date" not in names
        assert "photo ratio cap" not in names


class TestTheQuestionIsAskedOnce:
    """The verify/judge loop re-enters selection; the story does not change."""

    def test_a_second_refinement_asks_nothing_and_applies_the_same_fates(self):
        pool = _pool(moments=4)
        refiner, tracker = _wired()

        # WHY: the model. What is under test is that it is consulted once.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            return_value=_answer(keep=[1, 2], cut=[3, 4]),
        ) as asked:
            refiner.phase_refine(pool, tracker)
            thinner = [c for c in pool if c.clip.asset.id != "m1c1"]
            second = refiner.phase_refine(thinner, tracker)

        assert asked.call_count == 1
        shipped = {c.asset.id for c in second.selected_clips}
        assert shipped and not shipped & {"m3c0", "m3c1", "m4c0", "m4c1"}


class TestACondemnedMomentStaysCondemned:
    def test_backfill_will_not_spend_freed_seconds_on_a_cut_moment(self):
        """The relaxation ladder reaches far. It may not reach in here.

        The kept moments are two seconds each and the cut ones five, so the
        cut finishes well short of its runtime and backfill goes looking —
        which is exactly the state that used to rebuild what a stage refused.
        """
        short = [_clip(f"keep{i}", days=1 + i, seconds=2.0) for i in range(4)]
        long = [_clip(f"gone{i}", days=5 + i, seconds=5.0) for i in range(4)]
        refiner, tracker = _wired(target_clips=4, dedup_window=0.0)

        # WHY: the model. What is under test is what backfill does afterwards.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            return_value=_answer(keep=[1, 2, 3, 4], cut=[5, 6, 7, 8]),
        ):
            result = refiner.phase_refine([*short, *long], tracker)

        shipped = {c.asset.id for c in result.selected_clips}
        assert shipped == {"keep0", "keep1", "keep2", "keep3"}


def _choose_without_asking(pool: list[ClipWithSegment], *, target: float):
    pass_ = StructurePass(LLMConfig())
    # WHY: the model — and here the point is that it is never reached.
    with patch("immich_memories.analysis.selection_structure._ask") as asked:
        cut = pass_.choose(pool, target_duration=target, moment_window=MOMENT_WINDOW)
    return cut, asked


class TestNothingToStructure:
    def test_a_single_moment_is_not_a_structure_to_decide(self):
        pool = _pool(moments=1, per_moment=3)

        cut, asked = _choose_without_asking(pool, target=10.0)

        assert asked.call_count == 0
        assert cut.kept == pool
        assert cut.dropped == frozenset()

    def test_a_pool_that_already_fits_the_budget_is_left_alone(self):
        pool = _pool(moments=2)

        cut, asked = _choose_without_asking(pool, target=20.0)

        assert asked.call_count == 0
        assert cut.kept == pool

    def test_the_trace_says_why_it_was_not_asked(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            _choose_without_asking(pool, target=20.0)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.dropped == 0
        assert any("already fits" in reason for reason in stage.reasons)

    def test_a_pass_through_leaves_the_question_open_for_the_next_round(self):
        """No memo: a later round with more material may still need asking."""
        pool = _pool(moments=2)
        pass_ = StructurePass(LLMConfig())

        # WHY: the model. The first call must not reach it, the second must.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            return_value=_answer(keep=[1, 2], cut=[3, 4]),
        ) as asked:
            pass_.choose(pool, target_duration=20.0, moment_window=MOMENT_WINDOW)
            pass_.choose(_pool(moments=4), target_duration=10.0, moment_window=MOMENT_WINDOW)

        assert asked.call_count == 1


class TestWhoGetsAnEditor:
    """The same gate the fine cut answers to — one LLM switch, both passes."""

    def test_a_run_that_reads_no_content_gets_none(self):
        from immich_memories.analysis.selection_structure import structure_pass_for
        from immich_memories.config_loader import Config

        assert structure_pass_for(Config()) is None

    def test_a_content_analysis_run_gets_one(self):
        from immich_memories.analysis.selection_structure import structure_pass_for
        from immich_memories.config_loader import Config

        editor = structure_pass_for(Config(content_analysis={"enabled": True}))

        assert isinstance(editor, StructurePass)
