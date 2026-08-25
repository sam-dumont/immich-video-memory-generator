"""Pass 3 — which moments does the story need? (#764, slice 2)

Selection narrowed 204 candidates to 13 by counting: a per-day photo cap, a
spread across dates, a fit to the runtime, two ratio caps. Not one of those
stages ever looked at what a moment was OF, and the measured funnel showed
them deleting a 0.80 and shipping a 0.36.

This pass asks one question at moment granularity instead, and these tests pin
what may and may not decide a kill. Numbers order the table and measure the
envelope; the model decides what goes.

The question is asked REJECT-ONLY — name what the story does not need — so an
answer that stops early cuts less rather than more. Everything the model never
mentions is kept.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from immich_memories.analysis import selection_trace
from immich_memories.analysis.selection_structure import StructurePass
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, ExifInfo, VideoClipInfo
from immich_memories.config_models_llm import LLMConfig

JUNE = datetime(2023, 6, 1, 12, tzinfo=UTC)
MOMENT_WINDOW = 10.0

SUBJECTS = (
    "a harbour",
    "a kitchen",
    "a mountain",
    "a bicycle",
    "a birthday",
    "a rainstorm",
    "a garden",
    "a concert",
)


def _clip(
    asset_id: str,
    *,
    days: float = 0.0,
    minutes: float = 0.0,
    starred: bool = False,
    score: float = 0.5,
    seconds: float = 5.0,
    shows: str | None = None,
    where: tuple[float, float] | None = None,
) -> ClipWithSegment:
    when = JUNE + timedelta(days=days, minutes=minutes)
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=starred,
        exifInfo=ExifInfo(latitude=where[0], longitude=where[1]) if where else None,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=seconds)
    clip.llm_description = shows or f"a shot called {asset_id}"
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


def _answer(cut: list[int]) -> str:
    """A reject-only answer: every moment it does not name is kept."""
    return json.dumps({"cut": [{"index": i, "reason": f"M{i} is not needed"} for i in cut]})


def _priority(order: list[int]) -> str:
    """The surviving moments handed back, most essential first."""
    return json.dumps({"keep": order})


def _choose(
    pool: list[ClipWithSegment],
    raw: str,
    *,
    target: float = 10.0,
    target_clips: int = 2,
    moment_window: float = MOMENT_WINDOW,
):
    return _choose_answers(
        pool,
        [raw],
        target=target,
        target_clips=target_clips,
        moment_window=moment_window,
    )[0]


def _choose_answers(
    pool: list[ClipWithSegment],
    raws: list[str],
    *,
    target: float = 10.0,
    target_clips: int = 2,
    moment_window: float = MOMENT_WINDOW,
):
    pass_ = StructurePass(LLMConfig())
    # Under test is how often we go back to it, and what we do with the answer.
    # WHY: the model.
    with patch("immich_memories.analysis.selection_structure._ask", side_effect=raws * 4) as asked:
        cut = pass_.choose(
            pool,
            target_duration=target,
            moment_window=moment_window,
            target_clips=target_clips,
        )
    return cut, asked


def _choose_exactly(
    pool: list[ClipWithSegment],
    raws: list[str],
    *,
    target: float = 10.0,
    target_clips: int = 2,
    moment_window: float = MOMENT_WINDOW,
):
    """Like _choose_answers, but a call past the end of `raws` is an error."""
    pass_ = StructurePass(LLMConfig())
    # A third call raises StopIteration, which is the assertion: this pass
    # never asks more than twice.
    # WHY: the model.
    with patch("immich_memories.analysis.selection_structure._ask", side_effect=raws) as asked:
        cut = pass_.choose(
            pool,
            target_duration=target,
            moment_window=moment_window,
            target_clips=target_clips,
        )
    return cut, asked


class TestTheStoryChoosesItsMoments:
    def test_a_cut_moment_takes_all_of_its_members_with_it(self):
        pool = _pool(moments=4)

        cut = _choose(pool, _answer(cut=[3, 4]))

        assert [c.clip.asset.id for c in cut.kept] == ["m1c0", "m1c1", "m2c0", "m2c1"]
        assert cut.dropped == frozenset({"m3c0", "m3c1", "m4c0", "m4c1"})

    def test_every_member_of_a_cut_moment_carries_the_moment_reason(self):
        """The account answers "why is that not in there" for free from notes."""
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _choose(pool, _answer(cut=[3, 4]))

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.notes["m3c0"] == "M3 is not needed"
        assert stage.notes["m3c1"] == "M3 is not needed"
        assert stage.notes["m4c0"] == "M4 is not needed"


class TestStructureUsesTheSharedMomentMap:
    def test_parallel_places_are_separate_moments_and_occasions(self):
        from immich_memories.analysis.selection_structure import _moments_of

        brussels = (50.8466, 4.3528)
        spa = (50.4922, 5.8645)
        pool = [
            _clip("brussels-a", minutes=0, where=brussels),
            _clip("brussels-b", minutes=1, where=brussels),
            _clip("spa", minutes=1, where=spa),
        ]

        moments = _moments_of(pool, MOMENT_WINDOW)

        assert [[member.clip.asset.id for member in moment.members] for moment in moments] == [
            ["brussels-a", "brussels-b"],
            ["spa"],
        ]
        assert [moment.episode for moment in moments] == ["E1", "E2"]


class TestSilenceKeeps:
    """Reject-only, so a truncated answer costs coverage, never content.

    Keep-semantics made silence a kill: an answer that stopped after four
    moments cut the rest of the month. The strict partition existed to catch
    that. Naming only rejects gets the same protection by construction, and
    fails in the safe direction when it fails at all.
    """

    def test_an_answer_that_stops_early_keeps_everything_it_never_reached(self):
        pool = _pool(moments=5)
        stopped_early = json.dumps({"cut": [{"index": 5, "reason": "a quiet day"}]})

        cut = _choose(pool, stopped_early, target=20.0)

        assert cut.dropped == frozenset({"m5c0", "m5c1"})

    def test_an_empty_cut_list_is_an_answer_rather_than_a_refusal(self):
        """ "The month needs all of it" is a legitimate edit, and readable."""
        pool = _pool(moments=5)

        cut, _asked = _choose_exactly(
            pool, [_answer(cut=[]), _priority([1, 2, 3, 4, 5])], target=20.0
        )

        assert cut is not None and cut.narrowed

    def test_the_same_moment_named_twice_is_one_decision(self):
        pool = _pool(moments=5)
        twice = json.dumps(
            {"cut": [{"index": 5, "reason": "quiet"}, {"index": 5, "reason": "quiet again"}]}
        )

        cut = _choose(pool, twice, target=20.0)

        assert cut.dropped == frozenset({"m5c0", "m5c1"})

    def test_cutting_all_but_one_moment_is_not_an_edit(self):
        pool = _pool(moments=5)

        cut, _asked = _choose_answers(pool, [_answer(cut=[2, 3, 4, 5])])

        assert cut is None

    def test_the_question_says_that_at_least_two_moments_must_survive(self):
        pool = _pool(moments=5)

        _cut, asked = _choose_answers(pool, [_answer(cut=[2, 3, 4, 5])])

        prompt = asked.call_args_list[0].args[0]
        assert "Leave at least two moments in the month" in prompt


class TestAnUnusableAnswerHandsTheCutBack:
    def test_an_index_that_is_not_in_the_table_is_refused_after_one_retry(self):
        pool = _pool(moments=4)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        with selection_trace.tracing() as recorded:
            cut, asked = _choose_exactly(pool, [nonsense, nonsense])

        assert cut is None
        assert asked.call_count == 2
        assert any("the structure pass never ran" in w for w in recorded.warnings)

    def test_an_entry_with_no_reason_is_refused_after_one_retry(self):
        pool = _pool(moments=4)
        bare = json.dumps({"cut": [{"index": 3}]})

        cut, asked = _choose_exactly(pool, [bare, bare])

        assert cut is None
        assert asked.call_count == 2

    def test_a_retry_that_fixes_the_entry_is_carried_out(self):
        pool = _pool(moments=4)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        cut, asked = _choose_exactly(pool, [nonsense, _answer(cut=[3, 4])])

        assert asked.call_count == 2
        assert cut.dropped == frozenset({"m3c0", "m3c1", "m4c0", "m4c1"})

    def test_a_malformed_final_object_is_retried_instead_of_using_an_earlier_draft(self):
        pool = _pool(moments=4)
        draft = _answer(cut=[4])
        malformed_final = json.dumps({"cut": [{"index": "four", "reason": "quiet"}]})

        cut, asked = _choose_exactly(
            pool,
            [f"{draft}\n{malformed_final}", _answer(cut=[3, 4])],
        )

        assert asked.call_count == 2
        assert cut.dropped == frozenset({"m3c0", "m3c1", "m4c0", "m4c1"})

    def test_the_funnel_makes_the_cut_when_the_pass_refuses(self):
        pool = _pool(moments=4)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        with selection_trace.tracing() as recorded:
            _refine(pool, [nonsense, nonsense])

        names = [stage.name for stage in recorded.stages]
        assert "structure" not in names
        assert "per-day photo cap" in names

    def test_a_decided_structure_replaces_the_counting_stages(self):
        pool = _pool(moments=4)

        with selection_trace.tracing() as recorded:
            _refine(pool, _answer(cut=[3, 4]))

        names = [stage.name for stage in recorded.stages]
        assert "structure" in names
        assert "per-day photo cap" not in names
        assert "distribute by date" not in names
        assert "photo ratio cap" not in names


class TestTheReAskNamesTheDefect:
    """Vague revision requests are what corrupted the one revision measured.

    Told only that its answer did not work, the model rewrote from scratch and
    came back worse. Naming the entries is the difference between a correction
    and a re-roll.
    """

    def test_an_index_outside_the_table_is_named_back_to_the_model(self):
        pool = _pool(moments=4)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        _cut, asked = _choose_exactly(pool, [nonsense, _answer(cut=[3])])

        revision = asked.call_args_list[1].args[0]
        assert "M99, which is not in the table" in revision

    def test_an_entry_with_no_reason_is_named_back_to_the_model(self):
        pool = _pool(moments=4)
        bare = json.dumps({"cut": [{"index": 3}, {"index": 4, "reason": "fine"}]})

        _cut, asked = _choose_exactly(pool, [bare, _answer(cut=[3])])

        revision = asked.call_args_list[1].args[0]
        assert "M3 with no reason" in revision

    def test_the_revision_also_requires_two_surviving_moments(self):
        pool = _pool(moments=5)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        _cut, asked = _choose_exactly(pool, [nonsense, _answer(cut=[3, 4, 5])])

        revision = asked.call_args_list[1].args[0]
        assert "Leave at least two moments in the month" in revision

    def test_an_answer_with_nothing_nameable_wrong_is_not_re_asked(self):
        """Prose with no edit in it is not a defect anyone can correct."""
        pool = _pool(moments=4)

        cut, asked = _choose_exactly(pool, ["I would keep most of these, honestly."])

        assert cut is None
        assert asked.call_count == 1


class TestTheEnvelopeShrinksByTheEditorsOwnOrder:
    """A number may order the release; it may never pick the victim."""

    def test_an_over_budget_cut_releases_what_the_model_ranked_expendable(self):
        # The releases score high and the survivors score low: a shrink by
        # score would keep the exact opposite pair.
        pool = _pool(moments=5, scores={1: 0.9, 3: 0.9, 5: 0.9, 2: 0.1, 4: 0.1})

        cut, _asked = _choose_exactly(
            pool, [_answer(cut=[]), _priority([2, 4, 1, 5, 3])], target=10.0
        )

        assert {c.clip.asset.id for c in cut.kept} == {"m2c0", "m2c1", "m4c0", "m4c1"}

    def test_a_released_moment_says_which_budget_took_it(self):
        pool = _pool(moments=5)

        with selection_trace.tracing() as recorded:
            _choose_exactly(pool, [_answer(cut=[]), _priority([2, 4, 1, 5, 3])], target=10.0)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.notes["m3c0"] == (
            "released to fit the 10s budget (released last from the editor's priority order)"
        )

    def test_a_cut_inside_the_envelope_is_never_asked_to_rank(self):
        pool = _pool(moments=5)

        cut, asked = _choose_exactly(pool, [_answer(cut=[3, 4, 5])], target=10.0)

        assert asked.call_count == 1
        assert {c.clip.asset.id for c in cut.kept} == {"m1c0", "m1c1", "m2c0", "m2c1"}


class TestTheReorderQuestionShowsTheMoments:
    """Ranking opaque integers is not an editorial act.

    Handed "You kept: M1, M2, M3" and nothing else, the model has no basis to
    rank anything — and whatever permutation comes back would kill moments by
    it. The question shows the moments it is asking about.
    """

    def test_the_reorder_prompt_carries_the_surviving_moments(self):
        pool = _pool(moments=5)

        _cut, asked = _choose_exactly(
            pool, [_answer(cut=[4, 5]), _priority([1, 2, 3])], target=10.0
        )

        reorder = asked.call_args_list[1].args[0]
        assert "M1: 2023-06-02" in reorder
        assert "a shot called m1c0" in reorder

    def test_the_reorder_prompt_leaves_out_what_was_already_cut(self):
        pool = _pool(moments=5)

        _cut, asked = _choose_exactly(
            pool, [_answer(cut=[4, 5]), _priority([1, 2, 3])], target=10.0
        )

        reorder = asked.call_args_list[1].args[0]
        assert "M4:" not in reorder
        assert "a shot called m4c0" not in reorder

    def test_a_moment_the_starred_rule_kept_is_shown_and_listed(self):
        """The veto happens before the overshoot is measured, so it counts."""
        pool = [
            _clip("plain1", days=1, shows=SUBJECTS[0]),
            _clip("star2", days=2, starred=True, shows=SUBJECTS[1]),
            _clip("plain3", days=3, shows=SUBJECTS[2]),
        ]

        _cut, asked = _choose_exactly(pool, [_answer(cut=[2]), _priority([1, 2, 3])], target=5.0)

        reorder = asked.call_args_list[1].args[0]
        assert "You kept: M1, M2, M3" in reorder
        assert "M2: 2023-06-03" in reorder


class TestTheOwnersMarksSurviveTheirOccasion:
    """A star loses its place only to another star of the same occasion."""

    def _episode_pool(self, starred: set[str]) -> list[ClipWithSegment]:
        """Two moments half an hour apart — one occasion — then two lone days."""
        return [
            _clip("e1a", days=1, minutes=0, starred="e1a" in starred),
            _clip("e1b", days=1, minutes=2, starred="e1b" in starred),
            _clip("e1late", days=1, minutes=30, starred="e1late" in starred),
            _clip("day2", days=2, starred="day2" in starred),
            _clip("day3", days=3, starred="day3" in starred),
        ]

    def test_cutting_an_occasions_only_starred_moment_is_vetoed(self):
        pool = self._episode_pool(starred={"e1late"})

        cut = _choose(pool, _answer(cut=[2, 4]), target=15.0)

        assert "e1late" in {c.clip.asset.id for c in cut.kept}
        assert cut.dropped == frozenset({"day3"})

    def test_the_veto_is_on_the_record(self):
        pool = self._episode_pool(starred={"e1late"})

        with selection_trace.tracing() as recorded:
            _choose(pool, _answer(cut=[2, 4]), target=15.0)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert any("E1" in reason and "M2" in reason for reason in stage.reasons)

    def test_a_star_may_lose_to_a_star_of_the_same_occasion(self):
        pool = self._episode_pool(starred={"e1a", "e1late"})

        cut = _choose(pool, _answer(cut=[2, 4]), target=15.0)

        assert "e1late" in cut.dropped

    def test_the_envelope_walk_will_not_release_an_occasions_last_star(self):
        pool = self._episode_pool(starred={"e1late"})

        cut, _asked = _choose_exactly(pool, [_answer(cut=[]), _priority([1, 3, 4, 2])], target=10.0)

        assert "e1late" in {c.clip.asset.id for c in cut.kept}
        assert cut.dropped == frozenset({"day2", "day3"})


class TestTheEnvelopeMeasuresWhatWillShip:
    """A moment hands on ALL its members; dedup keeps up to three of them.

    Measured on one representative each, a keep-set that reads 1.0T can leave
    selection at two or three times that, and on the structure path nothing
    downstream bounds it.
    """

    def test_a_pool_that_fits_per_representative_but_not_as_it_ships_is_asked_about(self):
        pool = _pool(moments=2, per_moment=3)

        _cut, asked = _choose_answers(pool, [_answer(cut=[])], target=12.0, target_clips=8)

        assert asked.call_count >= 1

    def test_favourites_exempt_from_the_moment_cap_are_part_of_the_estimate(self):
        pool = [
            _clip("m1-star-a", days=1, starred=True, seconds=8.0),
            _clip("m1-star-b", days=1, minutes=2, starred=True, seconds=8.0),
            _clip("m2", days=2, seconds=1.0),
        ]

        _cut, asked = _choose_answers(pool, [_answer(cut=[])], target=10.0, target_clips=2)

        assert asked.call_count >= 1

    def test_disabling_the_moment_cap_counts_and_can_release_every_clip(self):
        pool = [_clip(f"m{day}c{index}", days=day) for day in range(1, 3) for index in range(4)]

        cut, asked = _choose_exactly(
            pool,
            [_answer(cut=[]), _priority(list(range(1, 9)))],
            target=10.0,
            target_clips=2,
            moment_window=0.0,
        )

        assert asked.call_count == 2
        assert sum(member.end_time - member.start_time for member in cut.kept) == 10.0

    def test_the_walk_releases_on_what_will_ship_not_on_one_clip_a_moment(self):
        pool = _pool(moments=4, per_moment=3)

        cut, _asked = _choose_exactly(
            pool,
            [_answer(cut=[]), _priority([1, 2, 3, 4])],
            target=20.0,
            target_clips=8,
        )

        assert {c.clip.asset.id for c in cut.kept} == {
            "m1c0",
            "m1c1",
            "m1c2",
            "m2c0",
            "m2c1",
            "m2c2",
        }

    def test_the_stage_states_both_numbers(self):
        pool = _pool(moments=4, per_moment=3)

        with selection_trace.tracing() as recorded:
            _choose_exactly(
                pool,
                [_answer(cut=[]), _priority([1, 2, 3, 4])],
                target=20.0,
                target_clips=8,
            )

        stage = next(s for s in recorded.stages if s.name == "structure")
        summary = stage.reasons[0]
        assert "10s of representatives" in summary
        assert "20s as it will ship" in summary


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


def _refine(pool: list[ClipWithSegment], raws: str | list[str], *, target_clips: int = 2):
    """Run the refinement phase with the structure pass wired in."""
    refiner, tracker = _wired(target_clips=target_clips)
    answers = [raws] if isinstance(raws, str) else raws
    # WHY: the model. Everything under test is what the pipeline does with it.
    with patch("immich_memories.analysis.selection_structure._ask", side_effect=answers * 4):
        return refiner.phase_refine(pool, tracker)


class TestNothingToStructure:
    def _choose_without_asking(self, pool: list[ClipWithSegment], *, target: float):
        pass_ = StructurePass(LLMConfig())
        # WHY: the model — and here the point is that it is never reached.
        with patch("immich_memories.analysis.selection_structure._ask") as asked:
            cut = pass_.choose(
                pool,
                target_duration=target,
                moment_window=MOMENT_WINDOW,
                target_clips=2,
            )
        return cut, asked

    def test_a_single_moment_is_not_a_structure_to_decide(self):
        pool = _pool(moments=1, per_moment=3)

        cut, asked = self._choose_without_asking(pool, target=10.0)

        assert asked.call_count == 0
        assert cut.kept == pool
        assert cut.dropped == frozenset()

    def test_a_pool_that_already_fits_the_budget_is_left_alone(self):
        pool = _pool(moments=2)

        cut, asked = self._choose_without_asking(pool, target=20.0)

        assert asked.call_count == 0
        assert cut.kept == pool

    def test_the_trace_says_why_it_was_not_asked(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            self._choose_without_asking(pool, target=20.0)

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
            return_value=_answer(cut=[3, 4]),
        ) as asked:
            pass_.choose(pool, target_duration=20.0, moment_window=MOMENT_WINDOW, target_clips=2)
            pass_.choose(
                _pool(moments=4), target_duration=10.0, moment_window=MOMENT_WINDOW, target_clips=2
            )

        assert asked.call_count == 1


class TestAPoolThatFitsIsStillADecision:
    """Skipping the caps on a genuinely fitting pool IS the design.

    The counting stages exist to force a pool down to a runtime. A pool that
    already fits one has nothing for them to do, and running them anyway is
    how a month with fourteen moments shipped four. Dedup and the fine cut
    still run underneath.
    """

    def test_the_counting_stages_are_skipped_for_a_pool_that_already_fits(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            _refine(pool, "", target_clips=4)

        names = [stage.name for stage in recorded.stages]
        assert "structure" in names
        assert "per-day photo cap" not in names
        assert "distribute by date" not in names
        assert "photo ratio cap" not in names

    def test_the_stage_says_the_pool_already_fits_the_budget(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            _refine(pool, "", target_clips=4)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert stage.dropped == 0
        assert any("already fits the budget" in reason for reason in stage.reasons)

    def test_the_dedup_stages_still_run_underneath(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            _refine(pool, "", target_clips=4)

        assert "same-moment dedup" in [stage.name for stage in recorded.stages]


class TestAValidJudgmentIsNeverThrownAway:
    """A failed rank must not cost the cut the model got right.

    The first answer's judgment about what stays and what goes has been sound
    in every measured run; it is the second call that has never once been. So
    a failed rank leaves the cuts standing and hands the length — only the
    length — to the counting stages.
    """

    def test_an_unusable_ranking_keeps_the_first_cut(self):
        pool = _pool(moments=5)

        cut, asked = _choose_exactly(pool, [_answer(cut=[5]), "no idea"])

        assert asked.call_count == 2
        assert cut is not None
        assert cut.dropped == frozenset({"m5c0", "m5c1"})
        assert not cut.narrowed

    def test_a_ranking_that_is_not_the_same_moments_is_refused(self):
        """The rank question regenerates no cut: only the order may change."""
        pool = _pool(moments=5)

        cut, asked = _choose_exactly(pool, [_answer(cut=[]), _priority([2, 1])])

        assert asked.call_count == 2
        assert cut is not None and not cut.narrowed

    def test_a_malformed_moment_label_keeps_the_first_cut(self):
        """A bad rank is an unusable answer, not an exception from selection."""
        pool = _pool(moments=5)
        malformed_rank = json.dumps({"keep": ["M²", "M1", "M2", "M3"]})

        cut, asked = _choose_exactly(pool, [_answer(cut=[5]), malformed_rank])

        assert asked.call_count == 2
        assert cut is not None
        assert cut.dropped == frozenset({"m5c0", "m5c1"})
        assert not cut.narrowed

    def test_the_trace_says_the_funnel_narrowed_the_remainder(self):
        pool = _pool(moments=5)

        with selection_trace.tracing() as recorded:
            _choose_exactly(pool, [_answer(cut=[]), "no idea"])

        assert any("the structure pass cut, but stated no priority" in w for w in recorded.warnings)

    def test_a_failed_re_ask_is_visible_in_the_stage(self):
        """Forensics must tell a failed re-ask from one that never happened."""
        pool = _pool(moments=5)

        with selection_trace.tracing() as recorded:
            _choose_exactly(pool, [_answer(cut=[]), "no idea"])

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert any("asked to revise; the answer was unusable" in r for r in stage.reasons)

    def test_a_re_ask_spent_on_a_defective_answer_says_it_had_none_left(self):
        pool = _pool(moments=5)
        nonsense = json.dumps({"cut": [{"index": 99, "reason": "no such moment"}]})

        with selection_trace.tracing() as recorded:
            cut, asked = _choose_exactly(pool, [nonsense, _answer(cut=[])])

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert asked.call_count == 2
        assert not cut.narrowed
        assert any("no re-ask left" in reason for reason in stage.reasons)


class TestTheTraceSaysWhereThePriorityCameFrom:
    def _reasons(self, raws: list[str], *, target: float = 10.0) -> list[str]:
        with selection_trace.tracing() as recorded:
            _choose_exactly(_pool(moments=5), raws, target=target)
        return next(s for s in recorded.stages if s.name == "structure").reasons

    def test_a_ranked_answer_is_recorded_as_stated_on_request(self):
        reasons = self._reasons([_answer(cut=[]), _priority([2, 4, 1, 5, 3])])

        assert "priority: stated on request" in reasons

    def test_an_unranked_answer_names_the_funnel_that_took_over(self):
        reasons = self._reasons([_answer(cut=[]), "no idea"])

        assert "priority: unstated — the arithmetic funnel narrowed the remainder" in reasons


class TestTheQuestionIsAskedOnce:
    """The verify/judge loop re-enters selection; the story does not change."""

    def test_a_second_refinement_asks_nothing_and_applies_the_same_fates(self):
        pool = _pool(moments=4)
        refiner, tracker = _wired()

        # WHY: the model. What is under test is that it is consulted once.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            return_value=_answer(cut=[3, 4]),
        ) as asked:
            refiner.phase_refine(pool, tracker)
            thinner = [c for c in pool if c.clip.asset.id != "m1c1"]
            second = refiner.phase_refine(thinner, tracker)

        assert asked.call_count == 1
        shipped = {c.asset.id for c in second.selected_clips}
        assert shipped and not shipped & {"m3c0", "m3c1", "m4c0", "m4c1"}

    def test_a_replayed_hybrid_still_hands_the_length_to_the_funnel(self):
        """The memo carries WHO settles the length, not just what was cut.

        Replaying a hybrid as fully-narrowed skips the funnel on every later
        round — and the LAST round is the one that ships. Measured: round one
        selected 2 clips for a 10s budget, round two shipped all five.
        """
        pool = _pool(moments=5)
        refiner, tracker = _wired()

        # The first round goes hybrid (its rank is unusable); the second must
        # not quietly promote that to a settled length.
        # WHY: the model.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            side_effect=[_answer(cut=[]), "no idea"],
        ):
            refiner.phase_refine(pool, tracker)
            with selection_trace.tracing() as recorded:
                refiner.phase_refine(pool, tracker)

        names = [stage.name for stage in recorded.stages]
        assert "per-day photo cap" in names
        assert "distribute by date" in names
        assert any(name.startswith("fit to ") for name in names)
        assert "photo ratio cap" in names
        assert names[-1] == "the favourite wins its moment"


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
            return_value=_answer(cut=[5, 6, 7, 8]),
        ):
            result = refiner.phase_refine([*short, *long], tracker)

        shipped = {c.asset.id for c in result.selected_clips}
        assert shipped == {"keep0", "keep1", "keep2", "keep3"}

    def test_the_favourites_law_cannot_resurrect_a_structure_cut(self):
        """The law repairs mechanical drops; it does not overrule the editor."""
        pool = [
            _clip("plain", minutes=0),
            _clip("cut-star", minutes=6, starred=True),
            _clip("kept-star", minutes=30, starred=True),
        ]
        refiner, tracker = _wired(dedup_window=5.0)

        # WHY: the model. It cuts the middle moment while another starred
        # moment keeps the occasion represented.
        with patch(
            "immich_memories.analysis.selection_structure._ask",
            return_value=_answer(cut=[2]),
        ):
            result = refiner.phase_refine(pool, tracker)

        shipped = {c.asset.id for c in result.selected_clips}
        assert shipped == {"plain", "kept-star"}


class TestTheHybridRunsTheWholeChain:
    """The funnel narrows the remainder — through the normal chain, not beside it.

    fit-to-Ns protects coverage ids, not stars, so the favourites law at the
    end of the phase is the safety net for any arithmetic narrowing. A hybrid
    that skipped it could drop a starred moment through the honest-fallback
    door.
    """

    def _pool(self) -> list[ClipWithSegment]:
        return [
            _clip(f"day{day}", days=day, shows=subject)
            for day, subject in enumerate(SUBJECTS[:4], start=1)
        ]

    def _stages(self):
        with selection_trace.tracing() as recorded:
            _refine(self._pool(), [_answer(cut=[4]), "no idea"])
        return recorded, [stage.name for stage in recorded.stages]

    def test_the_counting_stages_run_after_the_structure_stage(self):
        _recorded, names = self._stages()

        assert names.index("structure") < names.index("per-day photo cap")
        assert "distribute by date" in names

    def test_the_favourites_law_still_closes_the_phase(self):
        _recorded, names = self._stages()

        assert names[-1] == "the favourite wins its moment"
        assert names.index("per-day photo cap") < names.index("the favourite wins its moment")

    def test_what_the_story_cut_stays_cut(self):
        recorded, _names = self._stages()

        assert "day4" not in recorded.stages[-1].kept_ids

    def test_the_warning_names_the_hybrid(self):
        recorded, _names = self._stages()

        assert any("the arithmetic funnel narrowed the remainder" in w for w in recorded.warnings)


class TestTheWalkHasAFloor:
    def _pool(self) -> list[ClipWithSegment]:
        return [
            _clip("huge1", days=1, seconds=50.0, shows=SUBJECTS[0]),
            _clip("huge2", days=2, seconds=50.0, shows=SUBJECTS[1]),
        ]

    def test_the_last_moment_standing_is_never_released(self):
        """An empty cut with every id refused is unrecoverable: backfill may
        not touch what a stage condemned."""
        cut, _asked = _choose_exactly(
            self._pool(), [_answer(cut=[]), _priority([2, 1])], target=10.0
        )

        assert len(cut.kept) == 1

    def test_the_stage_says_it_could_not_shrink_any_further(self):
        with selection_trace.tracing() as recorded:
            _choose_exactly(self._pool(), [_answer(cut=[]), _priority([2, 1])], target=10.0)

        stage = next(s for s in recorded.stages if s.name == "structure")
        assert any("last moment standing" in reason for reason in stage.reasons)


class TestWhatOneLineTellsTheModel:
    """Description is the only thing in the table saying what a moment is OF."""

    def _line(self, members: list[ClipWithSegment]) -> str:
        from immich_memories.analysis.selection_structure import _moments_of, _table

        return _table(list(enumerate(_moments_of(members, MOMENT_WINDOW), start=1)))

    def test_a_described_moment_says_what_it_shows(self):
        line = self._line([_clip("seen", minutes=0), _clip("also", minutes=1)])

        assert "a shot called seen" in line

    def test_descriptions_come_from_the_described_members_not_the_top_two(self):
        """Slicing before filtering starved the prompt of the only content it had."""
        blind = [_clip("blind1", minutes=0, score=0.9), _clip("blind2", minutes=1, score=0.8)]
        for member in blind:
            member.clip.llm_description = None

        line = self._line([*blind, _clip("seen", minutes=2, score=0.1)])

        assert "a shot called seen" in line

    def test_the_line_counts_the_owners_marks(self):
        line = self._line(
            [
                _clip("star", minutes=0, starred=True),
                _clip("plain1", minutes=1),
                _clip("plain2", minutes=2),
            ]
        )

        assert "starred 1/3" in line

    def test_the_line_estimates_from_the_top_scored_member(self):
        line = self._line(
            [_clip("long", minutes=0, score=0.9, seconds=9.0), _clip("short", minutes=1, score=0.1)]
        )

        assert "est 9s" in line


class TestTheAbsorbersDownstreamAreNotSilent:
    """Generation can fix a cut it should have been handed whole.

    Two absorbers sit below selection and neither says anything: the stride
    sampler drops every other clip when the cut holds more than the budget can
    give a minimum-length slot, and the proportional trim shortens every clip
    when the content runs long. Both are selection problems paid for at render
    time, so the trace names them while the cut is still readable.
    """

    def test_too_many_clips_for_the_budget_says_the_sampler_will_act(self):
        pool = [
            _clip(f"tiny{day}", days=day, seconds=1.0, shows=subject)
            for day, subject in enumerate(SUBJECTS, start=1)
        ]

        with selection_trace.tracing() as recorded:
            _refine(pool, "")

        assert any("stride sampler" in warning for warning in recorded.warnings)

    def test_content_beyond_the_envelope_says_every_clip_will_be_trimmed(self):
        pool = [
            _clip(f"star{day}", days=day, starred=True, shows=subject)
            for day, subject in enumerate(SUBJECTS[:3], start=1)
        ]

        with selection_trace.tracing() as recorded:
            _refine(pool, [_answer(cut=[]), _priority([2, 1, 3])])

        assert any("trimmed to fit" in warning for warning in recorded.warnings)

    def test_a_cut_that_fits_says_nothing(self):
        pool = _pool(moments=2)

        with selection_trace.tracing() as recorded:
            _refine(pool, "", target_clips=4)

        assert recorded.warnings == []


class TestTheSamplerWarningMatchesTheSampler:
    def test_exactly_as_many_clips_as_slots_is_not_a_warning(self):
        """The sampler acts above the count, not at it: `len(clips) <= max` returns."""
        pool = [
            _clip(f"tiny{day}", days=day, seconds=1.0, shows=subject)
            for day, subject in enumerate(SUBJECTS[:6], start=1)
        ]

        with selection_trace.tracing() as recorded:
            _refine(pool, "")

        assert not any("stride sampler" in warning for warning in recorded.warnings)


class TestAModelThatCannotAnswer:
    """The branch most likely to fire in production: nothing is listening."""

    def test_an_unreachable_model_hands_the_cut_to_the_funnel(self):
        pool = _pool(moments=4)
        pass_ = StructurePass(LLMConfig())

        # A local LLM on a laptop that went to sleep is a server that is gone.
        # WHY: the model.
        with (
            patch(
                "immich_memories.analysis.selection_structure._ask",
                side_effect=TimeoutError("no server"),
            ),
            selection_trace.tracing() as recorded,
        ):
            cut = pass_.choose(
                pool, target_duration=10.0, moment_window=MOMENT_WINDOW, target_clips=2
            )

        assert cut is None
        assert any("the model was unavailable (TimeoutError)" in w for w in recorded.warnings)

    def test_a_bug_in_our_own_code_is_not_treated_as_an_absent_model(self):
        """A TypeError in the ask is our mistake, and it stops the run."""
        import pytest

        pool = _pool(moments=4)
        pass_ = StructurePass(LLMConfig())

        # WHY: the model. The bug being simulated is on our side of the call.
        with (
            patch(
                "immich_memories.analysis.selection_structure._ask",
                side_effect=TypeError("unexpected keyword argument"),
            ),
            pytest.raises(TypeError),
        ):
            pass_.choose(pool, target_duration=10.0, moment_window=MOMENT_WINDOW, target_clips=2)


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
