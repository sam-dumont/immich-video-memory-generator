"""The question the structure pass asks, and how its answer is read (#764).

Kept apart from the pass that acts on it because they fail differently. This
module is the contract with the model: three prompts, one shape of answer, and
the checks that decide whether what came back is an edit of THIS month or
something else. Every measurement behind its wording came from real answers —
four of them, from one June — and the wording is load-bearing in a way the
editing logic next door is not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# How near the content budget the rough cut has to land. Named here because
# the prompts state them to the model; the pass enforces them.
ENVELOPE_FLOOR = 0.9
ENVELOPE_CEILING = 1.1


_PROMPT = """You are structuring the rough cut of a month's memory video from
its moments. They are listed in the order they happened.

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

WRITE THE KEEP LIST MOST ESSENTIAL FIRST. The finished memory always plays in
the order things happened — nothing you write changes that — so the order of
your keep list means only one thing: how essential each moment is to the
story. Put the moment the month would be pointless without at the front, and
the one you would give up first at the end. If the cut still has to shrink,
the LAST entries fall first, so the end of your list is the sacrifice you are
choosing. Choose it: the last moment left in the memory is the month's closer.

Every moment you cut needs a one-line reason. What you leave out is mined
again later, and a bare no is useless to whoever reads it.

Answer with STRICT JSON only, no prose:
{{"keep": [<moment numbers, most essential first>],
 "cut": [{{"index": <moment number>, "reason": "<short reason>"}}]}}
Every moment from 1 to {count} must appear exactly once across keep and cut."""


_DEFECT_PROMPT = """You have already edited this month and the answer could not
be used: {defects}.

Here are the same moments, in the same order and numbered the same way:

{moments}

Edit it again. Every moment from 1 to {count} must appear exactly once — each
number in keep or in cut, never in both, never in neither. Write the keep list
most essential first: the memory plays in the order things happened whatever
you write, so that order says only how essential each moment is, and if the
cut has to shrink the last entries fall first.

Answer with STRICT JSON only, no prose:
{{"keep": [<moment numbers, most essential first>],
 "cut": [{{"index": <moment number>, "reason": "<short reason>"}}]}}"""


_REORDER_PROMPT = """You kept these moments of the month, and they run about
{shipped:.0f}s against a {target:.0f}s budget — more than the memory holds, so
some of them have to go.

You kept: {kept}

You listed them in table order, which says nothing about which of them the
story needs most. Do not change what you kept and do not cut anything here.
Give that exact list back, REORDERED most essential first: the moment the
month would be pointless without at the front, the one you would give up
first at the end.

The memory plays in the order things happened whatever order you write, so
this order means only how essential each moment is. The last entries fall
first, which makes the end of your list the sacrifice you are choosing — and
the last moment left in the memory is the month's closer.

Answer with STRICT JSON only, no prose, the same numbers and no others:
{{"keep": [<the same moment numbers, most essential first>]}}"""


def first_prompt(table: str, count: int, target_duration: float) -> str:
    return _PROMPT.format(
        moments=table,
        count=count,
        target=target_duration,
        floor=target_duration * ENVELOPE_FLOOR,
        ceiling=target_duration * ENVELOPE_CEILING,
    )


def defect_prompt(table: str, count: int, defects: tuple[str, ...]) -> str:
    return _DEFECT_PROMPT.format(moments=table, count=count, defects="; ".join(defects))


def reorder_prompt(kept: tuple[int, ...], shipped: float, target_duration: float) -> str:
    return _REORDER_PROMPT.format(
        kept=", ".join(f"M{index}" for index in kept),
        shipped=shipped,
        target=target_duration,
    )


@dataclass(frozen=True)
class Answer:
    """The edit the model made, once it accounts for every moment.

    `keep` is BOTH the set of kept moments and their order of essentiality.
    One artifact, because two was one too many: measured across four real
    answers, a parallel `release_order` list was never once read as a ranking
    of the keeps — every attempt ranked the cuts instead, prompt hardening and
    a keep-list echo included.
    """

    keep: tuple[int, ...]
    cut: tuple[tuple[int, str], ...]


def _is_a_moment_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def states_a_priority(keep: tuple[int, ...]) -> bool:
    """Whether the keep list says anything about what matters most.

    Written in table order it says nothing that can be acted on. That is the
    shape a model produces when it has not ranked at all, and a walk that
    trusted it would release from the end of the month — which is the closer,
    since a memory plays in the order things happened.

    One moment cannot be ranked against anything, so there is nothing here to
    ask about either.
    """
    return len(keep) > 1 and list(keep) != sorted(keep)


def order_mode(keep: tuple[int, ...]) -> str:
    """What the keep list's order is evidence of, said in the trace.

    Never "the model echoed the table": ascending is indistinguishable from a
    genuine priority that happens to run chronologically, and that coincidence
    is likeliest in a month whose best material is late — exactly where the
    closer matters. The line records what the evidence supports.
    """
    if len(keep) <= 1:
        return "keep order: a single moment, nothing to rank"
    if states_a_priority(keep):
        return "keep order: expressed priority"
    return "keep order: indistinguishable from table order"


def _accounted_for(payload: object, count: int) -> Answer | None:
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
    return Answer(tuple(kept), tuple(entries))


def answer_in(raw: str | None, count: int) -> Answer | None:
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


def _payloads_in(raw: str | None) -> list[dict]:
    """Every JSON object in an answer, last first — the verdict follows the prose."""
    if not raw:
        return []
    from immich_memories.analysis.selection_review import _balanced_objects

    found = []
    for candidate in reversed(_balanced_objects(raw)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


def _numbers_named_in(payload: dict) -> tuple[list[int], list[int]] | None:
    """The keep and cut numbers an answer names, however badly it named them."""
    keep, cut = payload.get("keep"), payload.get("cut")
    if not isinstance(keep, list) or not isinstance(cut, list):
        return None
    kept = [n for n in keep if _is_a_moment_number(n)]
    dropped = [
        entry["index"]
        for entry in cut
        if isinstance(entry, dict) and _is_a_moment_number(entry.get("index"))
    ]
    return kept, dropped


def defects_in(raw: str | None, count: int) -> tuple[str, ...]:
    """What was wrong with an answer we could not use, in words it can act on.

    A vague "that did not work, try again" is what corrupted the one revision
    ever measured: told only that its cut ran long, the model came back with
    one moment in both lists and three in neither. Naming the numbers is the
    difference between a correction and a re-roll.

    Empty means nothing nameable — prose, or no edit at all — and there is
    nothing to ask again about.
    """
    for payload in _payloads_in(raw):
        named = _numbers_named_in(payload)
        if named is not None:
            return _what_is_wrong_with(named, count)
    return ()


def _what_is_wrong_with(named: tuple[list[int], list[int]], count: int) -> tuple[str, ...]:
    keep, cut = named
    both = sorted(set(keep) & set(cut))
    everything = keep + cut
    repeated = sorted({n for n in everything if everything.count(n) > 1})
    missing = sorted(set(range(1, count + 1)) - set(everything))
    outside = sorted({n for n in everything if not 1 <= n <= count})
    defects = []
    if not keep:
        defects.append("you kept nothing, and a memory with nothing in it is not an edit")
    if both:
        defects.append(f"you listed {_named(both)} in both keep and cut")
    elif repeated:
        defects.append(f"you listed {_named(repeated)} more than once")
    if missing:
        defects.append(f"you omitted {_named(missing)}")
    if outside:
        defects.append(f"you named {_named(outside)}, which is not in the table")
    return tuple(defects)


def _named(numbers: list[int]) -> str:
    return ", ".join(f"M{n}" for n in numbers)


def reordering_in(raw: str | None, echoed: tuple[int, ...]) -> tuple[int, ...] | None:
    """The same keeps in a new order, or None if that is not what came back.

    Nothing else in the answer is read. The re-ask regenerates no partition —
    the first answer's judgment about what stays and what goes was reliable
    four times out of four, and it is the revision that has never once been —
    so anything but a permutation of what was echoed is refused.
    """
    for payload in _payloads_in(raw):
        order = payload.get("keep")
        if (
            isinstance(order, list)
            and all(_is_a_moment_number(n) for n in order)
            and sorted(order) == sorted(echoed)
        ):
            return tuple(order)
    return None


@dataclass(frozen=True)
class Reply:
    """What came back from one ask: the edit, or what was wrong with it.

    `reached` separates a model that answered badly from one that was not
    there at all — the second has already said so above the funnel, and asking
    it again would be asking the same silence twice.
    """

    answer: Answer | None
    defects: tuple[str, ...] = ()
    reached: bool = True
