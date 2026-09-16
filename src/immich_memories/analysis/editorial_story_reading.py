"""A duration-independent account of the stories supported by a memory's sources.

Temporal source groups are reading envelopes. The reader connects them into lived
episodes, preserving every source reference before any picture budget exists.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from functools import partial
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_episode_context import context_evidence
from immich_memories.analysis.editorial_moment_inventory import pages
from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_people import PEOPLE_FACTS_CONTRACT, relationship_evidence
from immich_memories.analysis.editorial_story_grouping import _synthesize
from immich_memories.analysis.editorial_story_replies import (
    STORY_VERSION,
    read_episode_page,
)
from immich_memories.analysis.editorial_story_weighing import _apply_story_weights
from immich_memories.operations.cut_progress import StageUpdate, announce_stage

HEADLINE_CHARS = 160
PAGE_FRAGMENTS = 16
PAGE_CHARS = 18000
# A fragment three readings could not place stops costing calls and becomes visible evidence.
PLACEMENT_ATTEMPTS = 3


@dataclass
class StoryEpisode:
    key: str
    title: str
    account: str
    significance: str
    role: str
    uncertainty: str
    moments: list[str] = field(default_factory=list)
    # One headline per placed fragment. The prose account paraphrases; a single-moment
    # milestone inside a long episode survives only as its own line.
    facts: list[dict] = field(default_factory=list)
    page_role: str = ""


@dataclass
class PeriodStory:
    thesis: str
    episodes: list[StoryEpisode]
    connections: list[dict[str, Any]]
    priorities: list[dict[str, Any]]
    uncertainties: list[str]
    audit: dict[str, Any]
    # Stories group day episodes and carry the weight the synthesis gave them for this memory.
    stories: list[dict[str, Any]] = field(default_factory=list)

    def as_record(self):
        return asdict(self)


def _instant(value):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def fragment_fact(row) -> dict:
    """The one line a fragment contributes to its episode: its first observation, kept whole."""
    observations = row.get("observations") or []
    headline = next((o.strip() for o in observations if isinstance(o, str) and o.strip()), "")
    return {
        "reading": row["reading"],
        "capture_group": row.get("capture_group", ""),
        "taken": str(row.get("taken") or ""),
        "fact": headline[:HEADLINE_CHARS],
        **{
            key: row[key]
            for key in ("known_people_in_group", "person_links", "episode_context")
            if row.get(key)
        },
    }


def _merge_facts(existing, added):
    """Chronological, one line per reading. A re-offered fragment keeps its first record."""
    merged = {fact["reading"]: fact for fact in existing}
    for fact in added:
        merged.setdefault(fact["reading"], fact)
    return sorted(merged.values(), key=itemgetter("taken", "reading"))


def story_evidence_rows(moment_rows, *, sources, annotations, lines):
    """Read every distinct source caption, not the two representatives on a moment card.

    Identical captions within a capture group share a row. Large groups become multiple
    reading fragments; their identity is retained so paging cannot hide the tail.
    """
    rows = []
    for moment in moment_rows:
        key = moment["moment_id"]
        descriptions = []
        for asset in sources.get(key, ()):
            annotation = annotations.get(asset)
            description = annotation.description if annotation is not None else lines.get(asset)
            if description and description not in descriptions:
                descriptions.append(description)
        if not descriptions:
            descriptions = [
                moment.get("evidence_1_description") or "Source description unavailable"
            ]
        for index, fragment in enumerate(pages(descriptions, max_items=24, max_chars=6500), 1):
            rows.append(
                {
                    "reading": f"{key}/{index}",
                    "capture_group": key,
                    "taken": moment["taken"],
                    "known_people_in_group": moment.get("people", ""),
                    "person_links": moment.get("person_links", ""),
                    "places": moment.get("places", ""),
                    "episode_context": moment.get("episode_context", ""),
                    "observations": fragment,
                }
            )
    return rows


def _same_episode_day(taken: str, first: str, last: str | None) -> bool:
    if not taken or not first:
        return True
    if taken[:10] == first[:10]:
        return True
    started, ended = _instant(taken), _instant(last)
    return (
        started is not None
        and ended is not None
        and 0 <= (started - ended).total_seconds() <= 6 * 3600
    )


def _split_off_other_days(updates, fragment_rows, first_seen, last_seen):
    """The one-day rule per FRAGMENT, not per page: a page that spans a week keeps its first
    day's episodes open, and the model files a later day into them. Those fragments start
    their own episode (same title, new id) instead."""
    out = []
    for row in updates:
        key = row.get("continues")
        if not key or key not in first_seen:
            out.append(row)
            continue
        same: list[str] = []
        other: list[str] = []
        for reading in row["readings"]:
            taken = str(fragment_rows[reading].get("taken") or "")
            bucket = (
                same if _same_episode_day(taken, first_seen[key], last_seen.get(key)) else other
            )
            bucket.append(reading)
        if same:
            out.append({**row, "readings": same})
        if other:
            out.append({**row, "continues": None, "readings": other})
    return out


def _still_open(key, *, first_seen, last_seen, page_start, page_day) -> bool:
    """Structure, not instruction: an episode is one day (or a night that runs past midnight).

    Yesterday's episodes are not offered, so an outing cannot join the preparations of the
    evening before; the synthesis names recurrences across days.
    """
    started, last = _instant(first_seen.get(key)), _instant(last_seen.get(key))
    if page_start is None or started is None or last is None:
        return True
    same_day = str(first_seen.get(key, ""))[:10] == page_day
    return same_day or 0 <= (page_start - last).total_seconds() <= 6 * 3600


def _episode_page_prompt(page, known, next_id, contract) -> str:
    # The two evidence blocks and the numbered example are last; everything above them is
    # byte-identical for the whole run, so a server that reuses a prefix reads the contract
    # and the placement rules once instead of once per page (#981).
    return f"""Read a personal photo library, chronologically, to understand what happened. {STORY_VERSION}.
{contract}
No film duration, no picture choice here. Every fragment is a group of photo descriptions
taken close together (capture group, time, known people, places).
Earlier days are closed: a new day is a new episode even when the activity repeats. A different occasion on the same day is also a new episode.

Place EACH new fragment into one lived episode: a known episode id when the fragment develops
the same occasion, visit, journey or ongoing situation, otherwise a new episode. A trip or a
hospital stay can span days; unrelated occasions stay separate even when the activity repeats.
Titles name the occasion, not the activity: "market day in Lisbon", not "walking".
No invented emotions, firsts or milestones. Roles: central, supporting, texture, incidental.
{PEOPLE_FACTS_CONTRACT}

OPEN EPISODES (today's; ids, titles, first facts)
{json.dumps(known, ensure_ascii=False)}

NEW FRAGMENTS TO PLACE
{json.dumps(page, ensure_ascii=False)}

Number new episodes S{next_id:04d}, S{next_id + 1:04d}, ... in order of first appearance.
JSON only, always both keys (new_episodes may be []): {{"fragments":[{{"reading":"{page[0]["reading"]}","episode":"S{next_id:04d}"}}],
"new_episodes":[{{"id":"S{next_id:04d}","title":"Specific occasion","account":"What happened, up to 60 words","role":"supporting"}}]}}
"""


def _episode_summary(episode, last_seen) -> dict[str, Any]:
    return {
        "id": episode.key,
        "title": episode.title,
        "last_seen": last_seen.get(episode.key, "")[:16],
        "facts": [f["fact"][:90] for f in (episode.facts or [])[:3]],
        "people_context": relationship_evidence(episode.facts),
        "episode_context": context_evidence(episode.facts),
    }


def _absorb(
    updates, episodes, *, fragment_moments, fragment_rows, moment_taken, first_seen, last_seen
) -> None:
    """Fold one page's placements into the episode ledger and its first/last-seen times."""
    for row in updates:
        key = row["continues"] or f"S{len(episodes) + 1:04d}"
        carried = episodes.get(key)
        moments = list(
            dict.fromkeys(
                [
                    *(carried.moments if carried else []),
                    *(fragment_moments[r] for r in row["readings"]),
                ]
            )
        )
        episodes[key] = StoryEpisode(
            key,
            row["title"],
            row["account"],
            row["significance"],
            row["role"],
            row["uncertainty"],
            moments,
            facts=_merge_facts(
                carried.facts if carried else [],
                [fragment_fact(fragment_rows[r]) for r in row["readings"]],
            ),
            page_role=row["role"],
        )
        times = [moment_taken.get(m, "") for m in moments if moment_taken.get(m)]
        if times:
            last_seen[key] = max(times)
            first_seen.setdefault(key, min(times))


def _carry_forward(omitted, attempts):
    """A fragment gets three readings before it is filed as unplaced evidence."""
    retry: list[dict] = []
    unplaced: list[dict] = []
    for row in omitted:
        attempts[row["reading"]] = attempts.get(row["reading"], 0) + 1
        target = retry if attempts[row["reading"]] < PLACEMENT_ATTEMPTS else unplaced
        target.append(row)
    return retry, unplaced


def _unplaced_episode(key, unplaced, fragment_moments) -> StoryEpisode:
    """Three readings could not place these fragments; keep them visible as their own
    incidental episode rather than silently dropping evidence."""
    return StoryEpisode(
        key,
        "Unplaced source fragments",
        "Fragments the reader could not place after three pages.",
        "Unknown; read directly before any cut.",
        "incidental",
        "unplaced by the reader",
        list(dict.fromkeys(fragment_moments[r["reading"]] for r in unplaced)),
        facts=_merge_facts([], [fragment_fact(r) for r in unplaced]),
        page_role="incidental",
    )


def _page_request(page, episodes, first_seen, last_seen, contract):
    """The prompt for one page of fragments, and the episodes it was allowed to continue."""
    page_start = _instant(page[0].get("taken"))
    page_day = str(page[0].get("taken") or "")[:10]
    open_episodes = {
        key: episode
        for key, episode in episodes.items()
        if _still_open(
            key,
            first_seen=first_seen,
            last_seen=last_seen,
            page_start=page_start,
            page_day=page_day,
        )
    }
    closed = {key: e for key, e in episodes.items() if key not in open_episodes}
    prompt = _episode_page_prompt(
        page,
        [_episode_summary(e, last_seen) for e in open_episodes.values()],
        len(episodes) + 1,
        contract,
    )
    return prompt, open_episodes, closed


def read_period_story(
    judge,
    *,
    evidence: list[dict],
    contract: str,
    prior: Mapping[str, Any],
    record: Callable[[dict], None] = lambda _: None,
    enrich: Callable[[list[StoryEpisode]], Mapping[str, Mapping[str, Any]]] | None = None,
    allow_gaps: bool = False,
    journey: bool = False,
) -> PeriodStory:
    """Read every source fragment, then interpret its place in the complete period.

    `enrich(episodes)` runs between the page reading and the synthesis and returns, per episode
    key, the facts the synthesis weighs with (day, place, moments, pictures, favourites, and the
    memory-worthy gate's reading); they are shown on the episode's card.
    """
    episodes: dict[str, StoryEpisode] = {}
    audit: dict[str, Any] = {
        "version": STORY_VERSION,
        "status": "reading",
        "source_fragments": len(evidence),
        "pages": [],
        "synthesis": [],
    }
    fragment_moments = {r["reading"]: r["capture_group"] for r in evidence}
    fragment_rows = {r["reading"]: r for r in evidence}
    moment_taken = {r["capture_group"]: str(r.get("taken") or "") for r in evidence}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    attempts: dict[str, int] = {}
    queue, number = evidence.copy(), 0
    while queue:
        number += 1
        announce_stage(StageUpdate(f"Reading the period account: page {number}"))
        page = next(iter(pages(queue, max_items=PAGE_FRAGMENTS, max_chars=PAGE_CHARS)))
        queue = queue[len(page) :]
        prompt, open_episodes, closed = _page_request(
            page, episodes, first_seen, last_seen, contract
        )
        try:
            updates = read_page_answer(
                judge,
                stage=f"story-episodes-{number}",
                prompt=prompt,
                max_tokens=1400 + 220 * len(page),
                read=partial(
                    read_episode_page,
                    offered={r["reading"] for r in page},
                    existing=open_episodes,
                    closed=closed,
                ),
            )
        except ValueError as exc:
            audit.update(status="incomplete", failure=str(exc))
            record(audit)
            raise
        updates = _split_off_other_days(updates, fragment_rows, first_seen, last_seen)
        _absorb(
            updates,
            episodes,
            fragment_moments=fragment_moments,
            fragment_rows=fragment_rows,
            moment_taken=moment_taken,
            first_seen=first_seen,
            last_seen=last_seen,
        )
        used = {r for row in updates for r in row["readings"]}
        omitted = [r for r in page if r["reading"] not in used]
        retry, unplaced = _carry_forward(omitted, attempts)
        if unplaced:
            key = f"S{len(episodes) + 1:04d}"
            episodes[key] = _unplaced_episode(key, unplaced, fragment_moments)
        queue = retry + queue
        audit["pages"].append(
            {
                "readings": [r["reading"] for r in page],
                "decisions": updates,
                "omitted": [r["reading"] for r in omitted],
                "unplaced": [r["reading"] for r in unplaced],
            }
        )
        record(audit | {"episodes": [asdict(e) for e in episodes.values()]})

    def synthesis_record(result):
        audit["synthesis"].append(result)
        record(audit | {"episodes": [asdict(e) for e in episodes.values()]})

    hints = dict(enrich(list(episodes.values()))) if enrich is not None else {}
    audit["hints"] = hints
    try:
        result = _synthesize(
            judge,
            list(episodes.values()),
            contract=contract,
            prior=prior,
            record=synthesis_record,
            hints=hints,
            allow_gaps=allow_gaps,
            journey=journey,
        )
    except ValueError as exc:
        audit.update(status="incomplete", failure=str(exc))
        record(audit | {"episodes": [asdict(e) for e in episodes.values()]})
        raise
    audit["unresolved_priorities"] = result.pop("unresolved_priorities", 0)
    _apply_story_weights(list(episodes.values()), result, hints)
    audit["status"] = "complete"
    story = PeriodStory(**result, episodes=list(episodes.values()), audit=audit)
    record(story.as_record())
    return story
