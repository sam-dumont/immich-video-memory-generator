"""Pass 3 — the structure pass: which moments does the story need? (#764)

Selection narrowed its pool by counting: a per-day photo cap, a spread across
dates, a fit to the runtime, two ratio caps. Five stages of arithmetic, and not
one of them ever looked at what a moment was OF — measured on one real month,
they deleted a moment scored 0.80 and shipped one scored 0.36.

This pass asks the question those caps were standing in for, once, at MOMENT
granularity: which of these moments does the month's story need? Numbers still
order things here — the table is chronological, durations are estimated, the
keep-set is measured against the budget — but no number decides a kill. Every
moment that goes is one the model named, or one the model itself ranked most
expendable, released against a fixed envelope.

Duration is a convergence bound, not an eviction rule. This is the rough cut
and it only has to land near the content budget; the fine cut (the llm review)
converges further downstream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)

# Room enough to answer rather than narrate — the ceiling the review measured
# against a local reasoning model, for a prompt of the same shape.
_STRUCTURE_MAX_TOKENS = 8000

# How near the content budget the rough cut has to land. Not an eviction rule:
# the fine cut converges further, and backfill fills an under-run.
_ENVELOPE_FLOOR = 0.9
_ENVELOPE_CEILING = 1.1

# How much of a member's description one table line carries.
_DESCRIPTION_CHARS = 120

_PROMPT = """You are structuring the rough cut of a month's memory video from
its moments. They are listed in the order they happened, and that order is
fixed: what you include is your only lever.

Ask ONE question of every moment: **does the month's story need it?**

The story needs coverage of what actually happened, not only what looks
strongest — the punch-up is not the march. A day earns a second moment only
when the story needs both of them. A cut made of nothing but peaks is
shapeless: vary the tension.

`starred n/m` counts the items the owner marked a favourite. Those are the
owner's own marks and they carry the month's meaning. Cut a starred moment
only when its occasion is already over-represented AND another starred moment
of that occasion stays in.

Moments sharing an `episode` name are ONE occasion — an afternoon somewhere, a
party, a hike — however different their descriptions read.

{moments}

The content budget is {target:.0f}s. Do the arithmetic: add up the `est`
seconds of every moment you keep, and that sum must land between {floor:.0f}s
and {ceiling:.0f}s. Keeping more than that is not an edit, it is a list.

Every moment you cut needs a one-line reason. What you leave out is mined
again later, and a bare no is useless to whoever reads it.

Also return `release_order`: every moment you kept, most expendable first —
what falls first if the cut still has to shrink. Choose that order yourself:
the memory plays in the order things happened, so the last moment you keep is
the month's closer, and nothing here will guess for you.

Answer with STRICT JSON only, no prose:
{{"keep": [<moment numbers>],
 "cut": [{{"index": <moment number>, "reason": "<short reason>"}}],
 "release_order": [<every kept moment number, most expendable first>]}}
Every moment from 1 to {count} must appear exactly once across keep and cut."""


_REVISION_PROMPT = """You have already edited this month, and what you kept
does not fit its runtime. Here are the same moments, in the same order and
numbered the same way:

{moments}

You kept: {kept}
Assembled, those moments run about {shipped:.0f}s. The content budget is
{target:.0f}s, so what you keep has to land between {floor:.0f}s and
{ceiling:.0f}s.

Revise the edit. Either cut more moments until what you keep fits, or give a
`release_order` — every moment you keep, most expendable first — and the cut
will shrink in the order you choose.

What falls first matters. The memory plays in the order things happened, so
the last moment you keep is the month's closer: an order that releases from
the end shortens the month instead of trimming it.

Same rules as before: starred moments carry the month's meaning, moments
sharing an `episode` are one occasion, and every moment you cut needs a
one-line reason.

Answer with STRICT JSON only, no prose:
{{"keep": [<moment numbers>],
 "cut": [{{"index": <moment number>, "reason": "<short reason>"}}],
 "release_order": [<every kept moment number, most expendable first>]}}
Every moment from 1 to {count} must appear exactly once across keep and cut."""


def _first_prompt(table: str, count: int, target_duration: float) -> str:
    return _PROMPT.format(
        moments=table,
        count=count,
        target=target_duration,
        floor=target_duration * _ENVELOPE_FLOOR,
        ceiling=target_duration * _ENVELOPE_CEILING,
    )


def _revision_prompt(
    table: str,
    count: int,
    kept: tuple[int, ...],
    shipped: float,
    target_duration: float,
) -> str:
    return _REVISION_PROMPT.format(
        moments=table,
        count=count,
        kept=", ".join(f"M{index}" for index in kept),
        shipped=shipped,
        target=target_duration,
        floor=target_duration * _ENVELOPE_FLOOR,
        ceiling=target_duration * _ENVELOPE_CEILING,
    )


def _ask(
    prompt: str, llm_config: LLMConfig, timeout_seconds: int, cache_path: Path | None = None
) -> str:
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
    keep-set shrinks rather than fixed at the start of a walk.
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


def _table(moments: list[_Moment]) -> str:
    return "\n".join(_moment_line(index + 1, m) for index, m in enumerate(moments))


@dataclass(frozen=True)
class _Answer:
    """The edit the model made, once it accounts for every moment."""

    keep: tuple[int, ...]
    cut: tuple[tuple[int, str], ...]
    # None when the model stated no usable order. There is no fallback: see
    # _stated_release_order.
    release_order: tuple[int, ...] | None


def _is_a_moment_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stated_release_order(given: object, kept: list[int]) -> tuple[int, ...] | None:
    """The editor's own order over its own keeps, or None if it stated none.

    No fallback, and that is the whole point. An order we invent is a
    mechanical kill wearing an editor's clothes: guessing "the keeps are in
    story order, so later means looser" released M53, M52, M50 and downwards
    on a real June and amputated the end of the month. A memory plays in the
    order things happened, so the last moment kept IS the closer, and a rule
    that releases from the end is a rule that shortens the month.

    Read as one list rather than entry by entry: an order naming something
    that was never kept is not an order over this cut.
    """
    if (
        isinstance(given, list)
        and all(_is_a_moment_number(n) for n in given)
        and set(given) == set(kept)
    ):
        return tuple(given)
    return None


def _accounted_for(payload: object, count: int) -> _Answer | None:
    """The edit, but only if the answer accounts for every moment exactly once.

    Under keep-semantics silence is a kill, so a truncated answer would cut
    every moment it never reached. Requiring the two lists to partition 1..M
    turns truncation back into a parse failure, which this pass survives by
    handing the cut to the arithmetic funnel. It is also the cheapest check
    that the model answered about THESE moments.
    """
    if not isinstance(payload, dict):
        return None
    keep, cut = payload.get("keep"), payload.get("cut")
    if not isinstance(keep, list) or not isinstance(cut, list) or not keep:
        return None
    kept = [n for n in keep if _is_a_moment_number(n)]
    entries = [
        (entry["index"], str(entry.get("reason", "no reason given")))
        for entry in cut
        if isinstance(entry, dict) and _is_a_moment_number(entry.get("index"))
    ]
    if len(kept) != len(keep) or len(entries) != len(cut):
        return None
    if sorted(kept + [index for index, _ in entries]) != list(range(1, count + 1)):
        return None
    return _Answer(
        tuple(kept), tuple(entries), _stated_release_order(payload.get("release_order"), kept)
    )


def _answer_in(raw: str | None, count: int) -> _Answer | None:
    """The model's edit, or None when it never made one about these moments."""
    if not raw:
        return None
    from immich_memories.analysis.selection_review import _balanced_objects

    for candidate in reversed(_balanced_objects(raw)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        answer = _accounted_for(payload, count)
        if answer is not None:
            return answer
    return None


@dataclass(frozen=True)
class StructureCut:
    """The clips whose moments the story kept, and why each of the rest went."""

    kept: list[ClipWithSegment]
    cuts: dict[str, str]

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

    def choose(
        self,
        pool: list[ClipWithSegment],
        *,
        target_duration: float,
        moment_window: float,
        target_clips: int,
    ) -> StructureCut | None:
        """The clips whose moments the story needs, or None if it never decided.

        None is the signal to fall back: the arithmetic funnel makes the cut
        instead, and the trace says so above the funnel.
        """
        if self._fates is not None:
            return _replay(pool, self._fates)
        moments = _moments_of(pool, moment_window)
        nothing_to_decide = _nothing_to_decide(moments, target_duration, target_clips)
        if nothing_to_decide is not None:
            # No memo: a later round may see more material than this one, and
            # a pool that fitted once is not a decision about the next pool.
            trace.record("structure", pool, pool, [f"not asked — {nothing_to_decide}"])
            return StructureCut(kept=pool.copy(), cuts={})
        table = _table(moments)
        answer = self._put(_first_prompt(table, len(moments), target_duration), len(moments))
        if answer is None:
            return None
        edit = _edit_from(moments, answer, target_duration, target_clips)
        if edit is None:
            edit = self._revise(table, moments, answer, target_duration, target_clips)
        if edit is None:
            return None
        cut = _apply(pool, edit)
        self._fates = cut.cuts.copy()
        return cut

    def _revise(
        self,
        table: str,
        moments: list[_Moment],
        overshot: _Answer,
        target_duration: float,
        target_clips: int,
    ) -> _Edit | None:
        """One corrective ask, then the funnel. No retry ladder.

        The editor kept more than the runtime holds and named no order to
        shrink by, so it is shown its own keep list, what it will actually run
        and the envelope, and asked to revise. A revision that still overshoots
        with no order is refused exactly like an unreadable answer: nothing
        here may invent the order itself.
        """
        kept, _cuts, _account = _keep_after_the_starred_rule(moments, overshot)
        prompt = _revision_prompt(
            table,
            len(moments),
            tuple(sorted(kept)),
            _shipped_est(list(kept.values()), target_clips),
            target_duration,
        )
        answer = self._put(prompt, len(moments))
        if answer is None:
            return None
        edit = _edit_from(moments, answer, target_duration, target_clips, revised=True)
        if edit is None:
            _never_ran("the revised cut still overshot the budget and stated no release order")
        return edit

    def _put(self, prompt: str, count: int) -> _Answer | None:
        try:
            raw = _ask(prompt, self._llm, self._timeout, self._cache_path)
        except Exception as e:  # WHY broad: the pass is optional; the funnel decides instead
            stop_if_this_is_our_bug(e, "structure pass")
            _never_ran(f"the model was unavailable ({type(e).__name__})")
            return None
        answer = _answer_in(raw, count)
        if answer is None:
            _never_ran("the model's answer could not be read")
        return answer


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
    if shipped <= target_duration * _ENVELOPE_CEILING:
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


@dataclass(frozen=True)
class _Edit:
    """A carried-out edit: why each dropped clip went, and the stage's account."""

    notes: dict[str, str]
    reasons: list[str]


def _keep_after_the_starred_rule(
    moments: list[_Moment], answer: _Answer
) -> tuple[dict[int, _Moment], dict[str, str], list[str]]:
    """The keep-set this answer really produces, and the account of the rest.

    Kept separate from the envelope because it runs BEFORE it: an occasion
    whose last starred moment was cut keeps that moment, and what it will run
    is part of the overshoot. Told otherwise, the editor would revise against
    a shorter month than the one it has.
    """
    by_index = {index + 1: moment for index, moment in enumerate(moments)}
    kept = {index: by_index[index] for index in answer.keep}
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
    return kept, notes, reasons


def _edit_from(
    moments: list[_Moment],
    answer: _Answer,
    target_duration: float,
    target_clips: int,
    *,
    revised: bool = False,
) -> _Edit | None:
    """Carry out the answer, or None when it overshoots with no order to shrink by.

    Kept moments continue whole; cut moments go whole, minus the occasions
    that would be left with no starred moment. None is not a refusal on its
    own — the caller asks the editor to revise once, and refuses only if that
    comes back the same way.
    """
    kept, notes, reasons = _keep_after_the_starred_rule(moments, answer)
    if revised:
        reasons.insert(0, "the first answer overshot the budget: asked once to revise")

    if _shipped_est(list(kept.values()), target_clips) > target_duration * _ENVELOPE_CEILING:
        if answer.release_order is None:
            return None
        reasons.append(
            "released against the editor's order, stated "
            + ("when asked to revise" if revised else "with its first answer")
        )
        _release_to_fit(kept, answer.release_order, target_duration, target_clips, notes, reasons)

    representatives = sum(_est(m) for m in kept.values())
    total = _shipped_est(list(kept.values()), target_clips)
    reasons.insert(
        0,
        f"kept {len(kept)} of {len(moments)} moment(s) — {representatives:.0f}s of "
        f"representatives, {total:.0f}s as it will ship, against a "
        f"{target_duration:.0f}s budget",
    )
    if total > target_duration * _ENVELOPE_CEILING:
        # The walk ran out of moments it was allowed to release. Not a refusal:
        # what stopped it is the starred rule, which outranks the envelope.
        reasons.append("still over the ceiling — every remaining moment is protected")
    if total < target_duration * _ENVELOPE_FLOOR:
        # Nothing is forced back in: backfill exists for exactly this, and a
        # moment the story does not need is not a way to fill a runtime.
        reasons.append(f"under-run — {target_duration - total:.0f}s left for backfill to fill")
    return _Edit(notes=notes, reasons=reasons)


def _apply(pool: list[ClipWithSegment], edit: _Edit) -> StructureCut:
    """Put the edit on the record and hand back what survived it."""
    survivors = [item for item in pool if item.clip.asset.id not in edit.notes]
    trace.record("structure", pool, survivors, edit.reasons, edit.notes)
    return StructureCut(kept=survivors, cuts=edit.notes)


def _replay(pool: list[ClipWithSegment], fates: dict[str, str]) -> StructureCut:
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
        ["the structure this run already decided, unchanged and unasked"],
        cuts,
    )
    return StructureCut(kept=survivors, cuts=cuts)


def _release_to_fit(
    kept: dict[int, _Moment],
    release_order: tuple[int, ...],
    target_duration: float,
    target_clips: int,
    notes: dict[str, str],
    reasons: list[str],
) -> None:
    """Shrink an over-long keep-set by the model's own order of expendability.

    The only stage here a number can start, and it still does not choose: the
    envelope says how much has to go, the editor said in which order.
    """
    ceiling = target_duration * _ENVELOPE_CEILING
    said = f"released to fit the {target_duration:.0f}s budget (the editor's stated release order)"
    for index in release_order:
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
