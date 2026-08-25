"""Pass 3 — the structure pass: which moments does the story need? (#764)

Selection narrowed its pool by counting: a per-day photo cap, a spread across
dates, a fit to the runtime, two ratio caps. Five stages of arithmetic, and not
one of them ever looked at what a moment was OF — measured on one real month,
they deleted a moment scored 0.80 and shipped one scored 0.36.

This pass asks the question those caps were standing in for, once, at MOMENT
granularity: which of these moments does the month's story need? Numbers still
order things here — the table is chronological, durations are estimated, the
keep-set is measured against the budget — but no number decides a kill. Every
moment that goes is one the model named. If those survivors exceed the
envelope, a separate question ranks that exact set most essential first and
the envelope releases from its tail. Nothing here invents a fallback order.

Duration is a convergence bound, not an eviction rule. This is the rough cut
and it only has to land near the content budget; the fine cut (the llm review)
converges further downstream. When the editor cannot say what to give up, the
cut still stands and the counting stages narrow what it kept — see
StructureCut.narrowed.

The question itself, and the reading of its answer, live next door in
structure_answer: the wording there is measured against real answers and fails
differently from the editing done here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from pathlib import Path

    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.config_loader import Config
    from immich_memories.config_models_llm import LLMConfig

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.llm_failures import stop_if_this_is_our_bug
from immich_memories.analysis.structure_answer import (
    ENVELOPE_CEILING,
    ENVELOPE_FLOOR,
    Answer,
    Reply,
    answer_in,
    defect_prompt,
    defects_in,
    first_prompt,
    reorder_prompt,
    reordering_in,
)

logger = logging.getLogger(__name__)

# Room enough to answer rather than narrate — the ceiling the review measured
# against a local reasoning model, for a prompt of the same shape.
_STRUCTURE_MAX_TOKENS = 8000

# How much of a member's description one table line carries.
_DESCRIPTION_CHARS = 120


def _ask(
    prompt: str, llm_config: LLMConfig, timeout_seconds: int, cache_path: Path | None = None
) -> str:
    """One question, one answer, reasoning on and with room to give it.

    Both calls carry the full token ceiling. Measured on the rank question
    against a 22-moment table: at the wrapper's 4000-token thinking floor the
    model spends the budget reasoning, the answer truncates, and the
    non-thinking retry comes back in table order — a silent loss of the
    priority the whole call exists to get.
    """
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(
            prompt,
            llm_config,
            temperature=0.2,
            max_tokens=_STRUCTURE_MAX_TOKENS,
            timeout_seconds=timeout_seconds,
            thinking=True,
            cache_path=cache_path,
        )
    )


@dataclass(frozen=True)
class _Moment:
    """One moment of the pool, and the occasion it belongs to."""

    members: tuple[ClipWithSegment, ...]
    episode: str


def _started(member: ClipWithSegment) -> datetime | None:
    return getattr(member.clip.asset, "file_created_at", None)


def _est(moment: _Moment) -> float:
    """How long this moment would run — its likely representative's length."""
    best = max(moment.members, key=lambda m: m.score)
    return best.end_time - best.start_time


def _shipped_est(kept: list[_Moment], target_clips: int) -> float:
    """What the kept moments will actually run once the cut reaches assembly.

    Not one representative each. This pass hands ALL members of a kept moment
    on, and the same-moment dedup below it keeps up to `_clips_per_moment` of
    them — three, on a long memory. Measured per representative, a keep-set
    reading 1.0T left selection at two or three times that, and on this path
    no later stage bounds it: the fit-to-runtime stage is one of the two the
    structure pass replaces.

    The share depends on how many moments are kept, so it is recomputed as the
    keep-set shrinks rather than fixed at the start of a walk. That makes this
    deliberately NON-MONOTONE in the number of kept moments: crossing the
    keep_per_moment threshold lets each surviving moment ship more, so
    releasing a moment can raise the estimate rather than lower it.
    """
    from immich_memories.analysis.clip_refiner import _clips_per_moment

    share = _clips_per_moment(target_clips, len(kept))
    return sum(_est(m) * min(len(m.members), share) for m in kept)


def _starred(moment: _Moment) -> int:
    return sum(1 for m in moment.members if getattr(m.clip.asset, "is_favorite", False))


def _ids(moment: _Moment) -> set[str]:
    return {m.clip.asset.id for m in moment.members}


def _moments_of(pool: list[ClipWithSegment], moment_window: float) -> list[_Moment]:
    """The pool as moments, chained into episodes, in chronological order.

    The same grouping the dedup stage collapses on, so the pass reasons in the
    unit its decision is later carried out in. Episodes chain those moments
    the way the review labels them: a gap wider than the episode window starts
    a new occasion.
    """
    from immich_memories.analysis.clip_scaler import group_by_moment
    from immich_memories.analysis.moment_grouping import EPISODE_WINDOW_MINUTES

    moments: list[_Moment] = []
    episode = 0
    previous: datetime | None = None
    for group in group_by_moment(pool, moment_window):
        start = _started(group[0])
        # An undated moment can neither continue an occasion nor be continued.
        if (
            start is None
            or previous is None
            or (start - previous).total_seconds() > EPISODE_WINDOW_MINUTES * 60
        ):
            episode += 1
        moments.append(_Moment(tuple(group), f"E{episode}"))
        previous = start
    return moments


def _when(moment: _Moment) -> str:
    start = _started(moment.members[0])
    if start is None:
        return "undated"
    end = _started(moment.members[-1]) or start
    return f"{start.date().isoformat()} {start:%H:%M}-{end:%H:%M}"


def _place_of(moment: _Moment) -> str | None:
    # WHY the review's helper: city, state and country, and the measurement of
    # why a caption is too thin to reason from lives beside it.
    from immich_memories.analysis.selection_review import _place_for_llm

    for member in moment.members:
        where = _place_for_llm(getattr(member.clip.asset, "exif_info", None))
        if where:
            return where
    return None


def _descriptions(moment: _Moment) -> str:
    """What the moment's two strongest DESCRIBED members were said to show.

    Filtered before it is sliced. A third of a real pool has no analysis yet,
    so taking the top two by score and then dropping the undescribed ones
    starved the line of the only thing on it that says what the moment is OF —
    exactly when the moment holds a described member the model could have
    judged on.
    """
    ranked = sorted(moment.members, key=lambda m: m.score, reverse=True)
    said = [
        str(m.clip.llm_description)[:_DESCRIPTION_CHARS]
        for m in ranked
        if getattr(m.clip, "llm_description", None)
    ]
    return " / ".join(said[:2]) if said else "not described"


def _moment_line(index: int, moment: _Moment) -> str:
    from immich_memories.api.models import AssetType

    videos = sum(1 for m in moment.members if m.clip.asset.type != AssetType.IMAGE)
    parts = [
        f"M{index}: {_when(moment)}",
        f"episode {moment.episode}",
        f"{len(moment.members)} items ({videos} video/{len(moment.members) - videos} photos)",
        f"starred {_starred(moment)}/{len(moment.members)}",
        f"est {_est(moment):.0f}s",
    ]
    where = _place_of(moment)
    if where:
        parts.append(where)
    parts.append(_descriptions(moment))
    return " · ".join(parts)


def _table(numbered: list[tuple[int, _Moment]]) -> str:
    """The moments as the model sees them, each under the number it is known by.

    Numbered by the caller rather than by position: the rank question shows
    only the survivors, and they must keep the numbers the first answer used.
    """
    return "\n".join(starmap(_moment_line, numbered))


@dataclass(frozen=True)
class StructureCut:
    """The clips whose moments the story kept, and why each of the rest went.

    `narrowed` says whether the length of this cut was settled here. False is
    the honest fallback: the story made a cut it stands behind but could not
    say what to give up first, so the counting stages narrow what it kept
    rather than the whole pool.
    """

    kept: list[ClipWithSegment]
    cuts: dict[str, str]
    narrowed: bool = True

    @property
    def dropped(self) -> frozenset[str]:
        return frozenset(self.cuts)


class StructurePass:
    """Asks which moments a memory's story needs, and applies the answer."""

    def __init__(
        self,
        llm_config: LLMConfig,
        *,
        cache_path: Path | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        self._llm = llm_config
        self._cache_path = cache_path
        self._timeout = timeout_seconds
        # What the story decided, keyed by asset id, once it has decided. The
        # verify/judge loop re-enters selection several times a run and the
        # answer to "what does this month need?" does not change because a
        # later pass dropped a clip — re-asking is whack-a-mole and ten more
        # reasoning calls.
        self._fates: dict[str, str] | None = None
        # And WHO settled the length. Replaying a hybrid as fully-narrowed
        # skips the funnel on every later round — and the last round is the
        # one that ships: measured, round one selected 2 clips for a 10s
        # budget and round two shipped all five.
        self._fates_narrowed: bool = True

    def choose(
        self,
        pool: list[ClipWithSegment],
        *,
        target_duration: float,
        moment_window: float,
        target_clips: int,
    ) -> StructureCut | None:
        """The clips whose moments the story needs, or None if it never decided.

        None is the signal to fall back entirely: the arithmetic funnel makes
        the cut over the whole pool, and the trace says so above the funnel. A
        cut that stands but could not be shrunk comes back with `narrowed`
        False instead — the judgment is kept, the length is not.
        """
        if self._fates is not None:
            return _replay(pool, self._fates, self._fates_narrowed)
        moments = _moments_of(pool, moment_window)
        nothing_to_decide = _nothing_to_decide(moments, target_duration, target_clips)
        if nothing_to_decide is not None:
            # No memo: a later round may see more material than this one, and
            # a pool that fitted once is not a decision about the next pool.
            trace.record("structure", pool, pool, [f"not asked — {nothing_to_decide}"])
            return StructureCut(kept=pool.copy(), cuts={})
        table = _table(list(enumerate(moments, start=1)))
        answer, asked_twice = self._rejects_of(table, moments, target_duration)
        if answer is None:
            return None
        if len(answer.survivors(len(moments))) < 2:
            _never_ran("the answer cut all but one moment, which is not an edit")
            return None
        cut = self._carry_out(
            pool, moments, answer, target_duration, target_clips, may_ask=not asked_twice
        )
        self._fates, self._fates_narrowed = cut.cuts.copy(), cut.narrowed
        return cut

    def _rejects_of(
        self, table: str, moments: list[_Moment], target_duration: float
    ) -> tuple[Answer | None, bool]:
        """What the story does not need, and whether it cost the run its re-ask.

        An answer whose entries name a moment outside the table, or name one
        with no reason, gets a single retry told exactly which entries were
        wrong. An answer with no cut list in it at all gets nothing: there is
        no defect to name, so there is nothing to correct.
        """
        count = len(moments)
        reply = self._put(first_prompt(table, count, target_duration), count)
        if reply.answer is not None or not reply.reached:
            return reply.answer, False
        if not reply.defects:
            _never_ran("the model's answer could not be read")
            return None, True
        retry = self._put(defect_prompt(table, count, reply.defects), count)
        if retry.answer is None and retry.reached:
            _never_ran("the revised answer still could not be read")
        return retry.answer, True

    def _carry_out(
        self,
        pool: list[ClipWithSegment],
        moments: list[_Moment],
        answer: Answer,
        target_duration: float,
        target_clips: int,
        *,
        may_ask: bool,
    ) -> StructureCut:
        """Apply the rejects, and shrink to the envelope if the editor ranks."""
        kept, notes, reasons = _keep_after_the_starred_rule(moments, answer)
        shipped = _shipped_est(list(kept.values()), target_clips)
        if shipped <= target_duration * ENVELOPE_CEILING:
            return _apply(pool, kept, notes, reasons, target_duration, target_clips, len(moments))
        order = self._priority_for(kept, shipped, target_duration, reasons, may_ask)
        if order is None:
            # The judgment stands; only the length is unsettled. What the model
            # cut has been sound in every measured run and what it said about
            # order has not, so the cut is kept and the counting stages are
            # handed the remainder rather than the whole pool.
            reasons.append("priority: unstated — the arithmetic funnel narrowed the remainder")
            trace.warn(
                "the structure pass cut, but stated no priority — "
                "the arithmetic funnel narrowed the remainder"
            )
            return _apply(
                pool,
                kept,
                notes,
                reasons,
                target_duration,
                target_clips,
                len(moments),
                narrowed=False,
            )
        _release_to_fit(kept, order, target_duration, target_clips, notes, reasons)
        return _apply(pool, kept, notes, reasons, target_duration, target_clips, len(moments))

    def _priority_for(
        self,
        kept: dict[int, _Moment],
        shipped: float,
        target_duration: float,
        reasons: list[str],
        may_ask: bool,
    ) -> tuple[int, ...] | None:
        """The order to release in, asked for only when something has to go.

        The first question carries no order — it names rejects — so this is
        where a ranking comes from at all, and it costs a reasoning call only
        when the cut actually overshoots.
        """
        if not may_ask:
            reasons.append("no re-ask left — the first answer had already been sent back once")
            return None
        echoed = tuple(kept)
        table = _table(list(kept.items()))
        order = reordering_in(
            self._say(reorder_prompt(table, echoed, shipped, target_duration)), echoed
        )
        if order is None:
            reasons.append("asked to revise; the answer was unusable")
            return None
        reasons.append("priority: stated on request")
        return order

    def _put(self, prompt: str, count: int) -> Reply:
        raw = self._say(prompt)
        if raw is None:
            return Reply(None, reached=False)
        answer = answer_in(raw, count)
        if answer is not None:
            return Reply(answer)
        return Reply(None, defects_in(raw, count))

    def _say(self, prompt: str) -> str | None:
        try:
            return _ask(prompt, self._llm, self._timeout, self._cache_path)
        except Exception as e:  # WHY broad: the pass is optional; the funnel decides instead
            stop_if_this_is_our_bug(e, "structure pass")
            _never_ran(f"the model was unavailable ({type(e).__name__})")
            return None


def _never_ran(why: str) -> None:
    """Say out loud that the funnel made this cut, not the story.

    Above the funnel rather than inside it: a `structure` stage with nothing
    dropped reads as an editor who approved the pool, which is the confusion
    the review's own warning exists to end.
    """
    logger.warning("Structure pass: %s — the arithmetic funnel decided this cut", why)
    trace.warn(f"the structure pass never ran — {why}; the arithmetic funnel decided this cut")


def _nothing_to_decide(
    moments: list[_Moment], target_duration: float, target_clips: int
) -> str | None:
    """Why this pool needs no editing, or None when it does.

    A reasoning call is minutes of a local model's time. Spending it to hear
    that a pool which already fits its runtime should ship whole is the kind
    of question worth not asking — and skipping the counting stages for such a
    pool is the point of this pass, not a shortcut around it.

    Measured as it will ship, not per representative: the per-representative
    number let a pool two or three times its budget call itself a fit.
    """
    if len(moments) < 2:
        return "one moment is not a structure to decide"
    shipped = _shipped_est(moments, target_clips)
    if shipped <= target_duration * ENVELOPE_CEILING:
        return (
            f"the pool already fits the budget — {shipped:.0f}s as it will ship "
            f"against {target_duration:.0f}s"
        )
    return None


def _the_last_star_of_its_episode(moment: _Moment, kept: Iterable[_Moment]) -> bool:
    """Whether losing this moment leaves its occasion with nothing starred.

    The owner's marks are the month's meaning, so an occasion never gives up
    its last one — the same battle the review fights per clip, fought here per
    moment. Identity, not equality: the moment under judgement may itself be
    in the keep-set, and it cannot vouch for itself.
    """
    if not _starred(moment):
        return False
    return not any(
        other is not moment and other.episode == moment.episode and _starred(other)
        for other in kept
    )


def _keep_after_the_starred_rule(
    moments: list[_Moment], answer: Answer
) -> tuple[dict[int, _Moment], dict[str, str], list[str]]:
    """The keep-set this answer really produces, and the account of the rest.

    Kept separate from the envelope because it runs BEFORE it: an occasion
    whose last starred moment was cut keeps that moment, and what it will run
    is part of the overshoot. Told otherwise, the editor would revise against
    a shorter month than the one it has.
    """
    by_index = {index + 1: moment for index, moment in enumerate(moments)}
    kept = {index: by_index[index] for index in answer.survivors(len(moments))}
    notes: dict[str, str] = {}
    reasons: list[str] = []
    for index, why in answer.cut:
        moment = by_index[index]
        if _the_last_star_of_its_episode(moment, kept.values()):
            kept[index] = moment
            reasons.append(
                f"M{index} stays — starred, and {moment.episode} would keep "
                f"no starred moment without it ({why})"
            )
            continue
        notes.update(dict.fromkeys(_ids(moment), why))
    # In the order they happened: a vetoed moment is re-admitted after the
    # survivors, and both the rank question's table and its echoed list read
    # as a timeline.
    return {index: kept[index] for index in sorted(kept)}, notes, reasons


def _apply(
    pool: list[ClipWithSegment],
    kept: dict[int, _Moment],
    notes: dict[str, str],
    reasons: list[str],
    target_duration: float,
    target_clips: int,
    of_moments: int,
    *,
    narrowed: bool = True,
) -> StructureCut:
    """Put the finished edit on the record and hand back what survived it."""
    representatives = sum(_est(m) for m in kept.values())
    # Deliberately non-monotone in the number of kept moments: the share one
    # moment may ship steps up as the keep-set shrinks, so releasing a moment
    # can RAISE this estimate. A reader of the trace should not be surprised.
    total = _shipped_est(list(kept.values()), target_clips)
    reasons.insert(
        0,
        f"kept {len(kept)} of {of_moments} moment(s) — {representatives:.0f}s of "
        f"representatives, {total:.0f}s as it will ship, against a "
        f"{target_duration:.0f}s budget",
    )
    if narrowed and total > target_duration * ENVELOPE_CEILING:
        # The walk ran out of moments it was allowed to release. Not a refusal:
        # what stopped it is the starred rule, or the last moment standing.
        reasons.append(
            "still over the ceiling — nothing left to release that is not "
            "protected or the last moment standing"
        )
    if total < target_duration * ENVELOPE_FLOOR:
        # Nothing is forced back in: backfill exists for exactly this, and a
        # moment the story does not need is not a way to fill a runtime.
        reasons.append(f"under-run — {target_duration - total:.0f}s left for backfill to fill")
    survivors = [item for item in pool if item.clip.asset.id not in notes]
    trace.record("structure", pool, survivors, reasons, notes)
    return StructureCut(kept=survivors, cuts=notes, narrowed=narrowed)


def _replay(pool: list[ClipWithSegment], fates: dict[str, str], narrowed: bool) -> StructureCut:
    """Apply a decision already taken to whatever pool this round supplies.

    An id nobody decided about passes through: the pool only ever shrinks
    between rounds, so this should not happen, and inventing a fate for it
    would be the pass deciding without being asked.
    """
    survivors = [item for item in pool if item.clip.asset.id not in fates]
    cuts = {
        item.clip.asset.id: fates[item.clip.asset.id]
        for item in pool
        if item.clip.asset.id in fates
    }
    trace.record(
        "structure",
        pool,
        survivors,
        [
            "the structure this run already decided, unchanged and unasked",
            "the length was settled here"
            if narrowed
            else "priority: unstated — the arithmetic funnel narrows the remainder",
        ],
        cuts,
    )
    return StructureCut(kept=survivors, cuts=cuts, narrowed=narrowed)


def _release_to_fit(
    kept: dict[int, _Moment],
    priority: tuple[int, ...],
    target_duration: float,
    target_clips: int,
    notes: dict[str, str],
    reasons: list[str],
) -> None:
    """Shrink an over-long keep-set from the tail of the editor's priority.

    The only stage here a number can start, and it still does not choose: the
    envelope says how much has to go, the editor's own order says which.

    A floor it never crosses: the last moment standing is never released. An
    empty cut with every id in the refused set is unrecoverable — backfill may
    not touch them — so a memory that cannot be shrunk any further ships long
    and says so, and the absorber warning at the end of the phase catches it.
    """
    ceiling = target_duration * ENVELOPE_CEILING
    said = (
        f"released to fit the {target_duration:.0f}s budget "
        "(released last from the editor's priority order)"
    )
    for index in reversed(priority):
        if len(kept) <= 1:
            return
        if _shipped_est(list(kept.values()), target_clips) <= ceiling:
            return
        moment = kept.get(index)
        if moment is None or _the_last_star_of_its_episode(moment, kept.values()):
            continue
        del kept[index]
        notes.update(dict.fromkeys(_ids(moment), said))
        reasons.append(f"M{index} released to fit the budget")


def structure_pass_for(app_config: Config) -> StructurePass | None:
    """The editor for this run, or None when nothing here asks an LLM anything.

    The same gate the fine cut answers to (SelectionQuality.cut): one switch
    decides whether a run has a reader at all, and a run without one keeps the
    arithmetic funnel it always had. Verdicts land in the same judgment cache,
    so an identical question about an identical month is answered once.
    """
    if not app_config.content_analysis.enabled:
        return None
    from immich_memories.cache.judgment_cache import verdicts_beside

    return StructurePass(app_config.llm, cache_path=verdicts_beside(app_config.cache.cache_path))
