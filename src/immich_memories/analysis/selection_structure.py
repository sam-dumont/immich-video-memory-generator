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

The content budget is {target:.0f}s. The `est` seconds of the moments you keep
should sum to between {floor:.0f}s and {ceiling:.0f}s.

Every moment you cut needs a one-line reason. What you leave out is mined
again later, and a bare no is useless to whoever reads it.

Also return `release_order`: the moments you kept, most expendable first. It is
used only if the cut still has to shrink to fit.

Answer with STRICT JSON only, no prose:
{{"keep": [<moment numbers>],
 "cut": [{{"index": <moment number>, "reason": "<short reason>"}}],
 "release_order": [<kept moment numbers, most expendable first>]}}
Every moment from 1 to {count} must appear exactly once across keep and cut."""


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
    """What the moment's two strongest members were said to show."""
    ranked = sorted(moment.members, key=lambda m: m.score, reverse=True)
    said = [
        str(m.clip.llm_description)[:_DESCRIPTION_CHARS]
        for m in ranked[:2]
        if getattr(m.clip, "llm_description", None)
    ]
    return " / ".join(said) if said else "not described"


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
    release_order: tuple[int, ...]


def _is_a_moment_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _release_order(given: object, kept: list[int]) -> tuple[int, ...]:
    """The keeps, most expendable first — the model's order, or the fallback.

    Read as one list rather than entry by entry. An order naming something
    that was never kept is not an order over this cut, and half of it mixed
    with a fallback is two different rankings spliced together. The fallback
    is the keep list reversed: the model listed its keeps in story order, so
    the later one is the more expendable.
    """
    if (
        isinstance(given, list)
        and all(_is_a_moment_number(n) for n in given)
        and set(given) == set(kept)
    ):
        return tuple(given)
    return tuple(reversed(kept))


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
    return _Answer(tuple(kept), tuple(entries), _release_order(payload.get("release_order"), kept))


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
    ) -> StructureCut | None:
        """The clips whose moments the story needs, or None if it never decided.

        None is the signal to fall back: the arithmetic funnel makes the cut
        instead, and the trace says so above the funnel.
        """
        if self._fates is not None:
            return _replay(pool, self._fates)
        moments = _moments_of(pool, moment_window)
        nothing_to_decide = _nothing_to_decide(moments, target_duration)
        if nothing_to_decide is not None:
            # No memo: a later round may see more material than this one, and
            # a pool that fitted once is not a decision about the next pool.
            trace.record("structure", pool, pool, [f"not asked — {nothing_to_decide}"])
            return StructureCut(kept=pool.copy(), cuts={})
        answer = self._ask_about(moments, target_duration)
        if answer is None:
            return None
        cut = _apply(pool, moments, answer, target_duration)
        self._fates = cut.cuts.copy()
        return cut

    def _ask_about(self, moments: list[_Moment], target_duration: float) -> _Answer | None:
        prompt = _PROMPT.format(
            moments=_table(moments),
            count=len(moments),
            target=target_duration,
            floor=target_duration * _ENVELOPE_FLOOR,
            ceiling=target_duration * _ENVELOPE_CEILING,
        )
        try:
            raw = _ask(prompt, self._llm, self._timeout, self._cache_path)
        except Exception as e:  # WHY broad: the pass is optional; the funnel decides instead
            stop_if_this_is_our_bug(e, "structure pass")
            _never_ran(f"the model was unavailable ({type(e).__name__})")
            return None
        answer = _answer_in(raw, len(moments))
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


def _nothing_to_decide(moments: list[_Moment], target_duration: float) -> str | None:
    """Why this pool needs no editing, or None when it does.

    A reasoning call is minutes of a local model's time. Spending it to hear
    that a pool which already fits its runtime should ship whole is the kind
    of question worth not asking.
    """
    if len(moments) < 2:
        return "one moment is not a structure to decide"
    estimated = sum(_est(moment) for moment in moments)
    if estimated <= target_duration * _ENVELOPE_CEILING:
        return f"the pool already fits — {estimated:.0f}s against {target_duration:.0f}s"
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


def _apply(
    pool: list[ClipWithSegment],
    moments: list[_Moment],
    answer: _Answer,
    target_duration: float,
) -> StructureCut:
    """Carry out the edit: kept moments continue, cut moments go whole."""
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

    _release_to_fit(kept, answer.release_order, target_duration, notes, reasons)

    total = sum(_est(m) for m in kept.values())
    reasons.insert(
        0,
        f"kept {len(kept)} of {len(moments)} moment(s), "
        f"{total:.0f}s estimated against a {target_duration:.0f}s budget",
    )
    if total < target_duration * _ENVELOPE_FLOOR:
        # Nothing is forced back in: backfill exists for exactly this, and a
        # moment the story does not need is not a way to fill a runtime.
        reasons.append(f"under-run — {target_duration - total:.0f}s left for backfill to fill")
    survivors = [item for item in pool if item.clip.asset.id not in notes]
    trace.record("structure", pool, survivors, reasons, notes)
    return StructureCut(kept=survivors, cuts=notes)


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
    notes: dict[str, str],
    reasons: list[str],
) -> None:
    """Shrink an over-long keep-set by the model's own order of expendability.

    The only stage here a number can start, and it still does not choose: the
    envelope says how much has to go, the editor said in which order.
    """
    ceiling = target_duration * _ENVELOPE_CEILING
    said = f"released to fit the {target_duration:.0f}s budget (the editor's own release order)"
    for index in release_order:
        if sum(_est(m) for m in kept.values()) <= ceiling:
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
