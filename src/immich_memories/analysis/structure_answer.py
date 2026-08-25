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

Name the moments the story does NOT need, and give one line of why for each.
Everything you do not name stays in.

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

The content budget is {target:.0f}s. Do the arithmetic: cut enough that the
`est` seconds of what remains add up to between {floor:.0f}s and {ceiling:.0f}s.
Leaving more than that in is not an edit, it is a list.
Leave at least two moments in the month; one moment alone is not a structure.

Every moment you name needs a one-line reason. What you leave out is mined
again later, and a bare no is useless to whoever reads it.

Answer with STRICT JSON only, no prose:
{{"cut": [{{"index": <moment number>, "reason": "<short reason>"}}]}}
Use moment numbers from 1 to {count} and nothing else. An empty list means the
month needs all of it."""


_DEFECT_PROMPT = """You have already named the moments this month's story does
not need, and some of what you named could not be used: {defects}.

Here are the same moments, in the same order and numbered the same way:

{moments}

Name them again. Use moment numbers from 1 to {count} and nothing else, and
give every one of them a one-line reason. Everything you do not name stays in.
Leave at least two moments in the month; one moment alone is not a structure.

Answer with STRICT JSON only, no prose:
{{"cut": [{{"index": <moment number>, "reason": "<short reason>"}}]}}"""


_REORDER_PROMPT = """These are the moments of the month you kept. They run
about {shipped:.0f}s against a {target:.0f}s budget — more than the memory
holds, so some of them have to go.

{moments}

You kept: {kept}

Rank them. Give that exact list back, most essential first: the moment the
month would be pointless without at the front, the one you would give up first
at the end. Do not add anything, do not remove anything, do not cut anything
here — only the order changes.

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


def reorder_prompt(
    table: str, kept: tuple[int, ...], shipped: float, target_duration: float
) -> str:
    """The rank question, showing the moments it is asking about.

    The table is not decoration. Handed a bare "You kept: M1, M2, M3", the
    model ranks opaque integers, and whatever permutation comes back would
    then kill moments by it — an uninformed ranking is a mechanical kill
    wearing the editor's clothes, which is the one thing this pass forbids.
    """
    return _REORDER_PROMPT.format(
        moments=table,
        kept=", ".join(f"M{index}" for index in kept),
        shipped=shipped,
        target=target_duration,
    )


@dataclass(frozen=True)
class Answer:
    """The moments the model says the story does not need, and why each.

    Rejects only. Everything it never names is kept, so this carries no keep
    list and no order at all — the ranking is a separate question, asked only
    when something has to go.
    """

    cut: tuple[tuple[int, str], ...]

    def survivors(self, count: int) -> tuple[int, ...]:
        """The moments left standing, in the order they happened."""
        named = {index for index, _ in self.cut}
        return tuple(index for index in range(1, count + 1) if index not in named)


def _is_a_moment_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _entries_in(payload: object) -> list | None:
    """The cut list, if this payload has one at all."""
    if not isinstance(payload, dict):
        return None
    cut = payload.get("cut")
    return cut if isinstance(cut, list) else None


def _read_cut(payload: object, count: int) -> Answer | None:
    """The rejects, but only if every entry names a moment and says why.

    Deliberately NOT the strict partition the fine cut uses (see
    selection_review._accounted_for). That check exists to stop silent
    deletion-by-truncation, which is real under keep-semantics: an answer that
    stops after four clips cuts everything it never reached. Reject-only gets
    the same protection BY CONSTRUCTION, and fails in the safe direction when
    it fails at all — a truncated answer names fewer rejects, so more content
    survives, not less.

    Keep-semantics remain right for Pass 4, where N is small and the model
    sees the whole finished cut. At 53 moments the full partition is a cliff:
    every measured answer went over it by dropping indices, and the retry
    rewrote from scratch rather than patching.

    One moment named twice is one decision, not a defect: the first reason
    stands.
    """
    entries = _entries_in(payload)
    if entries is None:
        return None
    seen: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not _is_a_moment_number(entry.get("index")):
            return None
        index, reason = entry["index"], entry.get("reason")
        if not 1 <= index <= count or not isinstance(reason, str) or not reason.strip():
            return None
        seen.setdefault(index, reason.strip())
    return Answer(tuple(seen.items()))


def answer_in(raw: str | None, count: int) -> Answer | None:
    """The model's rejects, or None when it named none we could read."""
    for payload in _payloads_in(raw):
        answer = _read_cut(payload, count)
        if answer is not None:
            return answer
    return None


def _payloads_in(raw: str | None) -> list[dict]:
    """The final JSON object in an answer — the verdict follows the prose."""
    if not raw:
        return []
    from immich_memories.analysis.selection_review import _balanced_objects

    candidates = _balanced_objects(raw)
    if not candidates:
        return []
    try:
        payload = json.loads(candidates[-1])
    except json.JSONDecodeError:
        return []
    return [payload] if isinstance(payload, dict) else []


def defects_in(raw: str | None, count: int) -> tuple[str, ...]:
    """What was wrong with an answer we could not use, in words it can act on.

    A vague "that did not work, try again" is what corrupted the one revision
    ever measured: told only that its answer was unusable, the model rewrote
    the whole edit from scratch and came back worse. Naming the entries is the
    difference between a correction and a re-roll.

    Empty means nothing nameable — prose, or no cut list at all — and there is
    nothing to ask again about.
    """
    for payload in _payloads_in(raw):
        entries = _entries_in(payload)
        if entries is not None:
            return _what_is_wrong_with(entries, count)
    return ()


def _what_is_wrong_with(entries: list, count: int) -> tuple[str, ...]:
    outside: list[int] = []
    reasonless: list[int] = []
    unreadable = 0
    for entry in entries:
        if not isinstance(entry, dict) or not _is_a_moment_number(entry.get("index")):
            unreadable += 1
            continue
        index, reason = entry["index"], entry.get("reason")
        if not 1 <= index <= count:
            outside.append(index)
        elif not isinstance(reason, str) or not reason.strip():
            reasonless.append(index)
    defects = []
    if outside:
        defects.append(f"you named {_named(sorted(set(outside)))}, which is not in the table")
    if reasonless:
        defects.append(f"you named {_named(sorted(set(reasonless)))} with no reason")
    if unreadable:
        defects.append(f"{unreadable} of your entries named no moment at all")
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
        if not isinstance(order, list):
            continue
        numbers = [_moment_number(entry) for entry in order]
        if None in numbers:
            continue
        ranked = [n for n in numbers if n is not None]
        if sorted(ranked) == sorted(echoed):
            return tuple(ranked)
    return None


def _moment_number(value: object) -> int | None:
    """A moment number, whether it came back as 12 or as "M12".

    Measured against the local model: asked for bare integers it sometimes
    labels them anyway. The label is not a different answer — it is the same
    stated order with the table's own prefix on it — so it is read rather than
    refused. The permutation check stays strict afterwards.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().removeprefix("M").removeprefix("m")
        if text.isdigit():
            try:
                return int(text)
            except ValueError:
                return None
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
