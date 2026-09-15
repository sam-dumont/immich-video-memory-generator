"""Weigh the stories of one memory over bounded tables with shared period context.

Stage B of the story reading. The model can only name story keys, so it cannot explode
or fold the grouping; it weighs, and may join two adjacent stories. What it leaves out,
and the floors and ceilings ruled long ago, are settled here rather than in the prompt.
"""

from __future__ import annotations

import re
from functools import partial
from typing import Any

from immich_memories.analysis.editorial_story_replies import (
    GATE_WEIGHT,
    STORY_VERSION,
    WEIGHT_ROLE,
    WEIGHTS,
    _lenient_object,
)
from immich_memories.analysis.editorial_story_weight_contract import (
    WEIGHING_CONTRACT_VERSION,
    ask_complete_weights,
)

_FAMILY_WORD = re.compile(
    r"\b(mother|father|parent|grand|sibling|brother|sister|uncle|aunt|nibling|niece|nephew|in-law|twin|son|daughter|godfather|godmother|partner|spouse)\b",
    re.IGNORECASE,
)
WEIGHING_PAGE_ITEMS = 60
WEIGHING_PAGE_CHARS = 48_000


def _day_gap(a: str, b: str) -> int:
    from datetime import date

    try:
        return abs((date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days)
    except ValueError:
        return 0


def consecutive_runs(keys, day_of) -> list[list[str]]:
    """Split a list of episode keys into runs of consecutive days (a gap of more than one day
    starts a new run). A holiday or a hospital stay is one run; a month is many."""
    ordered = sorted(keys, key=lambda k: (day_of(k), k))
    runs: list[list[str]] = []
    for key in ordered:
        if runs and _day_gap(day_of(runs[-1][-1]), day_of(key)) <= 1:
            runs[-1].append(key)
        else:
            runs.append([key])
    return runs


def _counted(hints, keys, field: str) -> int:
    return sum(int((hints.get(key) or {}).get(field, 0)) for key in keys)


def _seen(story, hints, days) -> dict[str, int]:
    keys = story["episodes"]
    return {
        "episodes": len(keys),
        "days": len(days),
        "moments": _counted(hints, keys, "moments"),
        "pictures": _counted(hints, keys, "pictures"),
        "favourites": _counted(hints, keys, "favourites"),
    }


def _story_rows(stories, hints, day_of, facts_of=lambda _story: [], people_of=lambda _story: {}):
    rows = []
    for story in stories:
        days = sorted({day_of(k) for k in story["episodes"] if day_of(k)})
        seen = _seen(story, hints, days)
        gates = [str((hints.get(k) or {}).get("gate") or "") for k in story["episodes"]]
        gate = next((g for g in ("remarkable", "maybe", "background") if g in gates), "")
        story["seen"], story["gate"] = seen, gate
        span = f"{days[0]} -> {days[-1]}" if days else "?"
        facts = "; ".join(str(f)[:90] for f in facts_of(story))
        people = people_of(story)
        people_text = ", ".join(
            f"{rel} x{n}" for rel, n in sorted(people.items(), key=lambda kv: -kv[1])[:6]
        )
        rows.append(
            f"{story['key']} | {span} | {seen['days']} day(s) | {seen['episodes']} episode(s) | "
            f"{seen['moments']} moments | {seen['pictures']} pictures | {seen['favourites']} favourites | "
            f"reading: {gate or 'none'} | {story['title']} | {story.get('purpose') or ''}"
            + (f" | people: {people_text}" if people_text else "")
            + (f" | facts: {facts}" if facts else "")
        )
    return rows


def _weighing_prompt(rows, *, thesis, contract, candidates) -> str:
    # The thesis and the table are last; everything above them is byte-identical for the run,
    # so the two orders of the same table share their whole preamble (#981).
    return f"""Weigh the stories of this requested memory. {STORY_VERSION}. {WEIGHING_CONTRACT_VERSION}.
{contract}

Confirm which of the stories below (one, rarely two) this memory is ABOUT in the "about" list. It takes up to half the film. Only a story the reading named can be chosen; leave the list empty if none deserves half.
Then weigh every other story for THIS memory (month, year, journey, person, anniversary, album or subject):
"major" = an occasion that must be there, with several moments;
"minor" = one or two moments;
"glimpse" = one picture of ordinary life, only when a picture would stand on its own;
"none" = leave out.
Weigh by what happened and how it was lived. The reading, favourites, moments and days are evidence,
not a formula. Ordinary domestic routine, however well photographed, is "none" unless this memory is
about it. Do not force a dramatic arc, equal calendar coverage or quotas; a quiet period stays quiet.
Favourites are the owner's own marks on the pictures. Two rows that are one occasion, split across
the list or filed as separate rows of one afternoon (a party, a march, a fair read as several
scenes), may be joined in "join" as pairs of story keys. Rows of different days are never joined. A title that names a detail of the
first picture rather than the occasion the facts show may be retitled.

Return one complete JSON object with "about" (a list of story keys), "weights" (a mapping from
every remaining story key to major, minor, glimpse or none), "join" (a list of key pairs, usually empty),
and "retitle" (a mapping from a story key to a better title, usually empty).

THESIS (from the reading)
{thesis}
THE READING SAYS THIS MEMORY IS ABOUT: {", ".join(candidates) if candidates else "nothing in particular"}

STORIES (key | first day -> last day | days | episodes | moments | pictures | favourites | memory-worthy reading | title | purpose | people present, by their relation to the owner | three facts)
{chr(10).join(rows)}

Assess all {len(rows)} stories. Use only the actual keys in the table. Do not return a sample answer.
"retitle" is only for a current title that names a detail of the first picture.
"""


def _ask_both_orders(judge, prompt, rows, by_key, candidates, record, *, suffix=""):
    """The same table twice, in both directions, so row order cannot decide a weight."""
    answers: dict[str, dict[str, str]] = {}
    abouts: dict[str, list[str]] = {}
    reply_audits: dict[str, dict] = {}
    joins: list[list[str]] = []
    retitles: dict[str, str] = {}
    for order_name, listing in (("source", rows), ("reversed", list(reversed(rows)))):
        obj, reply_audits[order_name] = ask_complete_weights(
            judge,
            stage=f"story-weighing-{order_name}{suffix}",
            prompt=prompt.replace(chr(10).join(rows), chr(10).join(listing)),
            story_keys=list(by_key),
            candidates=candidates,
            parse=_lenient_object,
            record=record,
        )
        answers[order_name], abouts[order_name] = obj["weights"], obj["about"]
        joins.extend(obj["join"])
        retitles.update(
            {
                key: title
                for key, title in obj["retitle"].items()
                if "<" not in title and "title" not in title.lower()
            }
        )
    return answers, abouts, reply_audits, joins, retitles


def _weighing_groups(by_key, day_of):
    """Keep join-compatible components together, including nonadjacent rows of a day."""
    groups: list[set[str]] = []
    for key, story in by_key.items():
        connected = [
            group
            for group in groups
            if any(_one_afternoon(story, by_key[other], day_of) for other in group)
        ]
        merged = {key}
        for group in connected:
            merged.update(group)
            groups.remove(group)
        groups.append(merged)
    positions = {key: index for index, key in enumerate(by_key)}
    return sorted(groups, key=lambda group: min(positions[key] for key in group))


def _weighing_pages(rows, by_key, candidates, prompt_for, day_of):
    """Repeat central candidates and their join partners; never split a possible occasion."""
    row_of = dict(zip(by_key, rows, strict=True))
    groups = _weighing_groups(by_key, day_of)
    anchors = set().union(*(group for group in groups if group.intersection(candidates)))

    def listing(keys):
        return [row for key, row in row_of.items() if key in keys]

    def fits(keys):
        return (
            len(keys) <= WEIGHING_PAGE_ITEMS
            and len(prompt_for(listing(keys))) <= WEIGHING_PAGE_CHARS
        )

    chunks, current = [], anchors.copy()
    for group in groups:
        if group <= anchors:
            continue
        if not fits(current | group):
            if current != anchors:
                chunks.append(listing(current))
            current = anchors.copy()
        current.update(group)
        if not fits(current):
            raise ValueError("story weighing context exceeds the bounded request size")
    if not fits(current):
        raise ValueError("story weighing context exceeds the bounded request size")
    chunks.append(listing(current))
    return chunks


def _ask_weighing_pages(judge, chunks, by_key, candidates, prompt_for, record):
    answers: dict[str, dict[str, str]] = {}
    abouts: dict[str, list[str]] = {}
    audits: dict[str, dict] = {}
    joins: list[list[str]] = []
    retitles: dict[str, str] = {}
    for number, rows in enumerate(chunks, 1):
        keys = {row.split(" |", 1)[0]: by_key[row.split(" |", 1)[0]] for row in rows}
        weights, central, checks, pairs, titles = _ask_both_orders(
            judge, prompt_for(rows), rows, keys, candidates, record, suffix=f"-page-{number}"
        )
        answers.update({f"{order}-page-{number}": values for order, values in weights.items()})
        audits.update({f"{order}-page-{number}": values for order, values in checks.items()})
        for order, named in central.items():
            # Each list is already limited to two. Confirmation must hold across the
            # whole period, not just whichever page happened to be read first.
            abouts[order] = [key for key in abouts.get(order, named) if key in named]
        joins.extend(pairs)
        retitles.update(titles)
    return answers, abouts, audits, joins, retitles


def _settle_weights(by_key, answers) -> None:
    """Two orders disagree: the gate's first judgment decides which way. A happening it read
    as background takes the lighter answer; anything else the heavier, so no occasion is lost."""
    for key, story in by_key.items():
        given = [
            weight
            for answer in answers.values()
            if (weight := answer.get(key)) and weight != "dominant"
        ]
        if given:
            pick = max if story.get("gate") == "background" else min
            story["weight"] = pick(given, key=WEIGHTS.index)


def _central_stories(abouts, candidates, by_key, stories):
    """Dominant only among the stories the thesis writer named; the weighing confirms (both orders
    first, else either). If the reading named candidates and the weighing confirmed none, the
    candidate with the strongest reading and the most moments stands, so a period that is about
    something never ends up with nothing at its centre."""
    named = (
        [k for k in abouts.get("source", []) if k in abouts.get("reversed", [])]
        or abouts.get("source")
        or abouts.get("reversed")
        or []
    )
    confirmed = list(named)
    if not named and candidates:
        # Unconfirmed: a candidate stands only if it is the period's largest story by moments (a
        # chunk's local "about", one posing afternoon, must not take half of a month around a birth).
        most = max(
            (
                (story.get("seen") or {}).get("moments", 0)
                for story in stories
                if not story.get("joined_into")
            ),
            default=0,
        )
        strongest = sorted(
            candidates,
            key=lambda k: (
                {"remarkable": 0, "maybe": 1}.get(by_key[k].get("gate"), 2),
                -(by_key[k].get("seen") or {}).get("moments", 0),
            ),
        )[:1]
        named = [k for k in strongest if (by_key[k].get("seen") or {}).get("moments", 0) >= most]
    return named, confirmed


def _span_of(story, day_of):
    days = sorted(d for d in (day_of(e) for e in story["episodes"]) if d)
    return (days[0], days[-1]) if days else None


def _one_afternoon(first, second, day_of) -> bool:
    """Two rows of one afternoon, or two parts of one story the day rule split."""
    span = _span_of(first, day_of)
    same_day = bool(span) and span == _span_of(second, day_of) and span[0] == span[1]
    return same_day or bool(
        first.get("split_from") and first.get("split_from") == second.get("split_from")
    )


def _lighter(first, second) -> str:
    return (
        first.get("weight")
        if WEIGHTS.index(first.get("weight") or "none")
        <= WEIGHTS.index(second.get("weight") or "none")
        else second.get("weight")
    )


def _apply_joins(by_key, joins, day_of) -> list[list[str]]:
    joined = []
    for pair in joins:
        if not (isinstance(pair, list) and len(pair) == 2 and all(k in by_key for k in pair)):
            continue
        first, second = by_key[pair[0]], by_key[pair[1]]
        if first is second or first.get("joined_into") or second.get("joined_into"):
            continue
        if not _one_afternoon(first, second, day_of):
            continue  # parts of one split story, or two rows of one afternoon; nothing across days
        last_first = max(day_of(k) for k in first["episodes"])
        first_second = min(day_of(k) for k in second["episodes"])
        if _day_gap(last_first, first_second) <= 1:
            first["episodes"] = list(dict.fromkeys([*first["episodes"], *second["episodes"]]))
            first["weight"] = _lighter(first, second)
            second["joined_into"] = first["key"]
            joined.append(pair)
    return joined


def _present_in_this_memory(story, favourites: int) -> bool:
    """Memory-worthy first, and the favourite wins its moment. Close family present (the kinship
    words the people file produces) makes a visit an occasion: mom over grandparents over
    acquaintance over stranger is what the words themselves say once they are on the row."""
    if story.get("gate") == "remarkable" or favourites > 0:
        return True
    family = any(_FAMILY_WORD.search(rel) for rel in (story.get("people_counts") or {}))
    return family and story.get("gate") in ("remarkable", "maybe")


def _floor_one(story, *, anyone_known: bool, journey: bool) -> list[str]:
    notes: list[str] = []
    if not story.get("weight"):
        story["weight"] = GATE_WEIGHT.get(story.get("gate") or "", "glimpse")
    favourites = (story.get("seen") or {}).get("favourites", 0)
    present = _present_in_this_memory(story, favourites)
    # Density, the other way round: a story in which no known person appears (strangers, a
    # street scene, signs) is at most a glimpse, unless the owner starred it or the memory is a
    # journey, where places are the subject.
    strangers_only = (
        anyone_known and not story.get("people_counts") and not favourites and not journey
    )
    if (
        strangers_only
        and story.get("gate") != "remarkable"
        and story["weight"] != "dominant"
        and WEIGHTS.index(story["weight"]) < WEIGHTS.index("glimpse")
    ):
        story["weight"] = "glimpse"
        notes.append(f"{story['key']}:strangers")
    floor = "major" if favourites >= 3 else "minor" if present else None
    if floor and WEIGHTS.index(story["weight"]) > WEIGHTS.index(floor):
        story["weight"] = floor
        notes.append(story["key"])
    # Ceiling: one moment without a star or a remarkable reading is at most minor, whatever its title.
    if (
        (story.get("seen") or {}).get("moments", 0) == 1
        and not present
        and WEIGHTS.index(story["weight"]) < WEIGHTS.index("minor")
    ):
        story["weight"] = "minor"
        notes.append(f"{story['key']}:ceiling")
    return notes


def _floor_weights(kept, *, journey: bool) -> list[str]:
    # no people data at all: the rule cannot speak
    anyone_known = any(story["people_counts"] for story in kept)
    return [
        note
        for story in kept
        for note in _floor_one(story, anyone_known=anyone_known, journey=journey)
    ]


def _weigh_stories(
    judge,
    stories,
    *,
    thesis,
    hints,
    contract,
    record,
    day_of,
    facts_of=lambda _story: [],
    candidates=(),
    people_of=lambda _story: {},
    journey=False,
):
    """Stage B: weigh the complete story table in both orders, paging large memories."""
    rows = _story_rows(stories, hints, day_of, facts_of, people_of)
    prompt_for = partial(_weighing_prompt, thesis=thesis, contract=contract, candidates=candidates)
    prompt = prompt_for(rows)
    by_key = {story["key"]: story for story in stories}
    if len(rows) <= WEIGHING_PAGE_ITEMS and len(prompt) <= WEIGHING_PAGE_CHARS:
        decisions = _ask_both_orders(judge, prompt, rows, by_key, candidates, record)
    else:
        prompt_for = partial(
            _weighing_prompt,
            thesis=thesis,
            candidates=candidates,
            contract=contract
            + f"\nThis table is one page of {len(rows)} stories in the WHOLE memory. "
            "The whole-memory thesis, all central candidates and their possible join partners "
            "are repeated for comparison. Weigh against the whole memory, with no page quotas.",
        )
        chunks = _weighing_pages(rows, by_key, candidates, prompt_for, day_of)
        decisions = _ask_weighing_pages(judge, chunks, by_key, candidates, prompt_for, record)
    answers, abouts, reply_audits, joins, retitles = decisions
    # Both order decisions are complete before any of their edits take effect.
    for key, title in retitles.items():
        by_key[key]["title"] = title.strip()[:120]
    _settle_weights(by_key, answers)
    named, confirmed = _central_stories(abouts, candidates, by_key, stories)
    central_audit = {
        "confirmed": confirmed[:2],
        "fallback": named[:2] if not confirmed else [],
        "overrode_explicit_none": [
            key
            for key in named[:2]
            if any(answer.get(key) == "none" for answer in answers.values())
        ],
    }
    for key in named[:2]:
        by_key[key]["weight"] = "dominant"
    joined = _apply_joins(by_key, joins, day_of)
    kept = [story for story in stories if not story.get("joined_into")]
    for story in kept:
        story["people_counts"] = people_of(story)
    floored = _floor_weights(kept, journey=journey)
    record(
        {
            "stage": "story-weighing",
            "rows": rows,
            "answers": answers,
            "about": abouts,
            "candidates": list(candidates),
            "floored": floored,
            "weights": {s["key"]: s["weight"] for s in kept},
            "joined": joined,
            "answered": any(answers.values()),
            "judgment_audit": {
                "orders": reply_audits,
                "central": central_audit,
                "review_required": bool(
                    central_audit["fallback"]
                    or central_audit["overrode_explicit_none"]
                    or any(not a["coverage_complete"] for a in reply_audits.values())
                ),
            },
        }
    )
    return kept


def _gate_story(key: str, episode, hints) -> dict[str, Any]:
    """An episode the synthesis left out becomes its own story, weighed by the memory-worthy
    reading it was shown: remarkable a minor story, maybe a glimpse, background none."""
    gate = str((hints.get(episode.key) or {}).get("gate") or "")
    return {
        "key": key,
        "title": episode.title,
        "episodes": [episode.key],
        "weight": "none" if episode.page_role == "incidental" else GATE_WEIGHT.get(gate, "glimpse"),
        "purpose": "",
        "unplaced_by_synthesis": True,
    }


def _apply_story_weights(episodes, result, hints) -> None:
    """Only the synthesis saw the period; its stories set the roles. Retitles apply as given."""
    stories = list(result.get("stories") or [])
    result.pop("roles", None)
    retitle = result.pop("retitle", {}) or {}
    placed = {key for story in stories for key in story["episodes"]}
    for episode in episodes:
        if episode.key in retitle:
            episode.title = retitle[episode.key]
        if episode.key not in placed:
            stories.append(_gate_story(f"K{len(stories) + 1:02d}", episode, hints))
    for story in stories:
        if not story.get("weight"):
            story["weight"] = GATE_WEIGHT.get(str(story.get("gate") or ""), "glimpse")
    weight_of = {key: story["weight"] for story in stories for key in story["episodes"]}
    for episode in episodes:
        episode.role = WEIGHT_ROLE[weight_of.get(episode.key, "none")]
    if not any(story["weight"] != "none" for story in stories):
        result.setdefault("uncertainties", []).append("synthesis weighed no story above none")
    result["stories"] = stories
    result["priorities"] = [
        {"episodes": story["episodes"], "purpose": story["purpose"]}
        for story in sorted(
            (s for s in stories if s["weight"] != "none"), key=lambda s: WEIGHTS.index(s["weight"])
        )
    ]
