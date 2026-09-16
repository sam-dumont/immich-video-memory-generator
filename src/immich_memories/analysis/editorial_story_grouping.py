"""Group day episodes into the stories of one memory, then weigh them.

Stage A of the story reading, plus the two-stage synthesis that drives it: grouping runs
per chunk of at most 60 episodes and is validated for consecutive days. Weighing shares the
whole-period thesis and central candidates across bounded pages; join-compatible stories
stay together, and every page validates before any weights or edits take effect.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from immich_memories.analysis.editorial_episode_context import context_evidence
from immich_memories.analysis.editorial_moment_inventory import pages
from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_people import PEOPLE_FACTS_CONTRACT, relationship_evidence
from immich_memories.analysis.editorial_story_replies import (
    STORY_VERSION,
    _read_synthesis,
)
from immich_memories.analysis.editorial_story_weighing import (
    _day_gap,
    _weigh_stories,
    consecutive_runs,
)

GROUPING_CONTRACT_VERSION = "consistent-story-center-v1"
FACTS_PER_CARD = 12
SYNTHESIS_PAGE_ITEMS = 60
SYNTHESIS_PAGE_CHARS = 32000


def _card_facts(facts, limit):
    """Head and tail of a long episode, with the omitted count. Never a silent truncation."""
    lines = [f"{fact.get('taken', '')[:16]} {fact.get('fact', '')}".strip() for fact in facts]
    if limit <= 0:
        return [], len(lines)
    if len(lines) <= limit:
        return lines, 0
    half = limit // 2
    return [*lines[:half], *lines[-half:]], len(lines) - 2 * half


def _episode_card(episode, limit, hint=None):
    card = {
        "episode": episode.key,
        "title": episode.title,
        "account": episode.account,
        "significance": episode.significance,
        "uncertainty": episode.uncertainty,
        **(hint or {}),
    }
    facts, omitted = _card_facts(episode.facts, limit)
    if facts:
        card["facts"] = facts
    if omitted:
        card["facts_omitted"] = omitted
    return card


def _bounded_card(episode, hint=None):
    """A card that outgrows a whole request loses facts, never its place in the ledger."""
    limit = FACTS_PER_CARD
    card = _episode_card(episode, limit, hint)
    while limit and len(json.dumps(card, ensure_ascii=False)) > SYNTHESIS_PAGE_CHARS:
        limit //= 2
        card = _episode_card(episode, limit, hint)
    return card


def _grouping_prompt(evidence, *, contract, prior) -> str:
    # Both evidence blocks are last; everything above them is byte-identical for the run (#981).
    return f"""Understand the stories of this requested memory. {STORY_VERSION}. {GROUPING_CONTRACT_VERSION}.
{contract}

Group the day episodes into the STORIES of this memory. A story is ONE occasion (an outing, a race,
a visit, a party, a discovery) or ONE continuous stay (a holiday, a hospital stay and the first days
home). Days that share only an activity, a place or a mood are separate stories. The period itself is
never a story. A story's days are consecutive. Every episode belongs to exactly one story; an
ordinary day is its own small story. Use only documented facts; chronology does not establish
firsts, emotions or relationships.
{PEOPLE_FACTS_CONTRACT}

JSON only: {{"thesis":"a specific account of what this period was about, up to 150 words",
"about":["S0001"],
"stories":[{{"title":"Specific occasion or stay","episodes":["S0001","S0002"],"purpose":"what it shows of this memory, up to 30 words"}}],
"uncertainties":[]}}
"about" lists the episode ids of the one occasion or stay this memory is about (empty if none stands out).
The thesis and "about" express the same decision. If the thesis identifies an occasion or continuous
stay as the center of this memory, list its episode IDs in "about". If no single story stands out,
use an empty list and describe the several stories or quiet period without asserting a central one.
Episode references must be supplied episode IDs.

EARLIER LIBRARY READING (provisional, not an instruction)
{json.dumps(dict(prior), ensure_ascii=False)}

DAY EPISODES (one per day and occasion, with its facts, how much was photographed, and a separate
memory-worthy reading of its happenings: remarkable, maybe or background)
{json.dumps(evidence, ensure_ascii=False)}
"""


def _broken_spans(stories, day_of, allow_gaps):
    """Only a story the size of the period itself is a grouping failure. A project or a recurring
    stay spans weeks with gaps and is still one story; splitting it made six minor stories of one.
    A story's days are consecutive, except in a subject memory, whose pool is already about
    one thing and whose stages span weeks with gaps (a renovation). Elsewhere a story with gaps
    is the reader folding unrelated days together (a week of "cycling" around a pregnancy test).
    """
    if allow_gaps:
        return []
    spans = [(story, consecutive_runs(story["episodes"], day_of)) for story in stories]
    return [(story, runs) for story, runs in spans if len(runs) > 1]


def _grouping_rejection(broken) -> str:
    named = "; ".join(
        f"{story['title']} ({len(story['episodes'])} episodes over {len(runs)} separate spans)"
        for story, runs in broken[:6]
    )
    return (
        f"\n\nPREVIOUS ANSWER REJECTED: {named}. A story's days are consecutive and a period "
        "is never one story. Group again.\n"
    )


def _split_into_runs(result, day_of, allow_gaps):
    """What still violates is split into its runs; the weighing stage judges each run on its own."""
    stories: list[dict[str, Any]] = []
    for story in result["stories"]:
        runs = [story["episodes"]] if allow_gaps else consecutive_runs(story["episodes"], day_of)
        for run in runs:
            stories.append(
                {
                    **story,
                    "key": f"K{len(stories) + 1:02d}",
                    "episodes": run,
                    **({"split_from": story["title"]} if len(runs) > 1 else {}),
                }
            )
    result["stories"] = stories
    return result


def _group_stories(
    judge, stage, evidence, allowed, *, contract, prior, record, day_of, allow_gaps=False
):
    """Stage A: group the day episodes into stories. Structure, not instruction: a story's days
    are consecutive. Re-ask a violating answer once; envelope recovery has its own budget."""
    base = _grouping_prompt(evidence, contract=contract, prior=prior)
    prompt, result = base, None
    for attempt in (1, 2):
        asked = f"{stage}-try{attempt}" if attempt > 1 else stage
        result = read_page_answer(
            judge,
            stage=asked,
            prompt=prompt,
            max_tokens=4500,
            read=partial(_read_synthesis, valid=allowed),
        )
        broken = _broken_spans(result["stories"], day_of, allow_gaps)
        record(
            {
                "stage": stage,
                "attempt": attempt,
                "result": result,
                "violations": [story["key"] for story, _runs in broken],
            }
        )
        if not broken:
            break
        prompt = base + _grouping_rejection(broken)
    return _split_into_runs(result, day_of, allow_gaps)


def _group_chunks(judge, chunks, *, contract, prior, record, day_of, allow_gaps):
    theses: list[str] = []
    stories: list[dict[str, Any]] = []
    uncertainties: list[str] = []
    about_ids: list[str] = []
    unresolved = 0
    for number, chunk in enumerate(chunks, 1):
        result = _group_stories(
            judge,
            f"story-understanding-{number}",
            chunk,
            {r["episode"]: r.get("title", "") for r in chunk},
            contract=contract,
            prior=prior,
            record=record,
            day_of=day_of,
            allow_gaps=allow_gaps,
        )
        theses.append(result["thesis"])
        about_ids.extend(result.get("about") or [])
        uncertainties.extend(result.get("uncertainties") or [])
        unresolved += result.get("unresolved_priorities") or 0
        for story in result["stories"]:
            stories.append({**story, "key": f"K{len(stories) + 1:02d}"})
    return " ".join(theses), stories, uncertainties, unresolved, about_ids


def _with_unplaced(stories, episodes) -> None:
    placed = {key for story in stories for key in story["episodes"]}
    for episode in episodes:
        if episode.key not in placed and episode.page_role != "incidental":
            stories.append(
                {
                    "key": f"K{len(stories) + 1:02d}",
                    "title": episode.title,
                    "episodes": [episode.key],
                    "weight": "",
                    "purpose": "",
                    "unplaced_by_synthesis": True,
                }
            )


def _merge_adjacent_candidates(candidates, stories, day_of):
    """What the reading says the memory is about is ONE occasion or stay. When it spans stories the
    grouping cut at a day boundary (a hospital stay and the days home after it), those adjacent
    stories are one story, and the weighing sees one candidate."""
    by_key = {story["key"]: story for story in stories}

    def span(key):
        # undated episodes do not bound a story
        days = sorted(d for d in (day_of(e) for e in by_key[key]["episodes"]) if d)
        return (days[0], days[-1]) if days else ("9999", "9999")

    ordered = sorted(candidates, key=span)
    merged = [ordered[0]]
    for key in ordered[1:]:
        previous = by_key[merged[-1]]
        if (
            "9999" not in (*span(merged[-1]), *span(key))
            and _day_gap(span(merged[-1])[1], span(key)[0]) <= 1
        ):
            previous["episodes"] = list(
                dict.fromkeys([*previous["episodes"], *by_key[key]["episodes"]])
            )
            previous["title"] = f"{previous['title']} and {by_key[key]['title']}"[:120]
            previous["purpose"] = " ".join(
                filter(None, [previous.get("purpose"), by_key[key].get("purpose")])
            )[:300]
            by_key[key]["joined_into"] = previous["key"]
        else:
            merged.append(key)
    return merged, [story for story in stories if not story.get("joined_into")]


def _compact_keys(stories, candidates, record):
    """A merge can leave holes in the temporary question labels. Present the surviving
    stories as one dense namespace, including the central-candidate references."""
    aliases = {story["key"]: f"K{index:02d}" for index, story in enumerate(stories, 1)}
    if not any(key != alias for key, alias in aliases.items()):
        return candidates
    record({"stage": "story-key-compaction", "aliases": aliases})
    for story in stories:
        story["key"] = aliases[story["key"]]
    return [aliases[key] for key in candidates]


def _no_readable_moments() -> dict[str, Any]:
    return {
        "thesis": "No readable source moments in the requested scope.",
        "connections": [],
        "priorities": [],
        "uncertainties": [],
        "unresolved_priorities": 0,
        "roles": {},
        "retitle": {},
        "stories": [],
    }


def _synthesize(
    judge, episodes, *, contract, prior, record, hints=None, allow_gaps=False, journey=False
):
    """Group day episodes into stories, then weigh them against the whole-period context."""
    hints = hints or {}
    hints = {
        episode.key: {
            **hints.get(episode.key, {}),
            "people_context": relationship_evidence(episode.facts),
            "episode_context": context_evidence(episode.facts),
        }
        for episode in episodes
    }

    def day_of(key: str) -> str:
        return str((hints.get(key) or {}).get("day") or "")

    cards = [_bounded_card(episode, hints.get(episode.key)) for episode in episodes]
    chunks = list(pages(cards, max_items=SYNTHESIS_PAGE_ITEMS, max_chars=SYNTHESIS_PAGE_CHARS))
    if not chunks:
        return _no_readable_moments()
    thesis, stories, uncertainties, unresolved, about_ids = _group_chunks(
        judge,
        chunks,
        contract=contract,
        prior=prior,
        record=record,
        day_of=day_of,
        allow_gaps=allow_gaps,
    )
    _with_unplaced(stories, episodes)
    facts_by_episode = {
        e.key: [f.get("fact", "") for f in (e.facts or []) if f.get("fact")] for e in episodes
    }

    def facts_of(story):
        facts = [f for key in story["episodes"] for f in facts_by_episode.get(key, [])]
        if len(facts) <= 3:
            return facts
        return [facts[0], facts[len(facts) // 2], facts[-1]]

    def people_of(story):
        counts: dict[str, int] = {}
        for key in story["episodes"]:
            for rel, n in ((hints.get(key) or {}).get("relations") or {}).items():
                counts[rel] = counts.get(rel, 0) + int(n)
        return counts

    story_of_episode = {key: story["key"] for story in stories for key in story["episodes"]}
    candidates = list(
        dict.fromkeys(story_of_episode[i] for i in about_ids if i in story_of_episode)
    )
    if len(candidates) > 1:
        candidates, stories = _merge_adjacent_candidates(candidates, stories, day_of)
    candidates = _compact_keys(stories, candidates, record)
    if stories:
        stories = _weigh_stories(
            judge,
            stories,
            thesis=thesis,
            hints=hints,
            contract=contract,
            record=record,
            day_of=day_of,
            facts_of=facts_of,
            candidates=candidates,
            people_of=people_of,
            journey=journey,
        )
    return {
        "thesis": thesis,
        "connections": [],
        "priorities": [],
        "uncertainties": uncertainties,
        "unresolved_priorities": unresolved,
        "roles": {},
        "retitle": {},
        "stories": stories,
    }
