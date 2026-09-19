"""A duration-independent account of the stories supported by a memory's sources.

The rows are the banked 90-minute episode readings, not the captions behind them, and a
page is one calendar month. Nothing carries between pages: a month's prompt is a pure
function of that month's rows, so the judgment bank answers it again for free, a changed
asset invalidates one month instead of every page after it, and the pages read together.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import partial
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_prompt_pages import pages
from immich_memories.analysis.editorial_reader_concurrency import reader_map
from immich_memories.analysis.editorial_story_grouping import _synthesize
from immich_memories.analysis.editorial_story_replies import (
    STORY_VERSION,
    read_episode_page,
)
from immich_memories.analysis.editorial_story_weighing import _apply_story_weights
from immich_memories.operations.cut_progress import StageUpdate, announce_stage

HEADLINE_CHARS = 160
OBSERVATION_CHARS = 160
REPRESENTATIVE_LINES = 3
FAVOURITE_LINES = 2
PAGE_EPISODES = 16
PAGE_CHARS = 18000
# A row three readings could not place stops costing calls and becomes visible evidence.
PLACEMENT_ATTEMPTS = 3

_PROMPT = f"""Read a personal photo library, chronologically, to understand what happened. {STORY_VERSION}.
No film duration, no picture choice here. Each row is one stretch of photographs taken
close together: when, where, who was there, how many captures, what a reader already
understood about it, and a few of the descriptions behind it.
One episode is ONE day: a later day is a new episode even when the activity repeats, and a
different occasion on the same day is a new episode too.

Place EACH row into one lived episode: the same episode id when two rows develop the same
occasion, visit or ongoing situation on the same day, otherwise a new episode. Unrelated
occasions stay separate even when the activity repeats.
Titles name the occasion, not the activity: "market day in Lisbon", not "walking".
No invented emotions, firsts or milestones. Roles: central, supporting, texture, incidental.

Number new episodes S0001, S0002, ... in order of first appearance.
JSON only, always both keys (new_episodes may be []): {{"fragments":[{{"reading":"r1","episode":"S0001"}}],
"new_episodes":[{{"id":"S0001","title":"Specific occasion","account":"What happened, up to 60 words","role":"supporting"}}]}}

EPISODES TO PLACE"""


@dataclass
class StoryEpisode:
    key: str
    title: str
    account: str
    significance: str
    role: str
    uncertainty: str
    moments: list[str] = field(default_factory=list)
    # One headline per placed reading. The prose account paraphrases; a single-moment
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
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def fragment_fact(row) -> dict:
    """The one line a reading contributes to its episode: its first observation, kept whole."""
    observations = row.get("observations") or []
    headline = next((o.strip() for o in observations if isinstance(o, str) and o.strip()), "")
    return {
        "reading": row["reading"],
        "capture_group": row.get("capture_group", ""),
        "taken": str(row.get("taken") or ""),
        "fact": headline[:HEADLINE_CHARS],
    }


def _merge_facts(existing, added):
    """Chronological, one line per reading. A re-offered row keeps its first record."""
    merged = {fact["reading"]: fact for fact in existing}
    for fact in added:
        merged.setdefault(fact["reading"], fact)
    return sorted(merged.values(), key=itemgetter("taken", "reading"))


def _merged_facts_column(moments, column: str) -> str:
    """One column of person or link facts across an episode's moments, first record per alias."""
    seen: dict[str, str] = {}
    for moment in moments:
        for part in str(moment.get(column) or "").split(";"):
            if part.strip():
                seen.setdefault(part.split("|", 1)[0].split(":", 1)[0], part)
    return ";".join(seen.values())


def _place_names(moments) -> str:
    """Place NAMES, never the wall's L aliases: a page must read without the wall's dictionary."""
    names: list[str] = []
    for moment in moments:
        for part in str(moment.get("places") or "").split(";"):
            name = part.split(":", 1)[-1].strip()
            if name and name not in names:
                names.append(name)
    return "; ".join(names)


def _count(moments, column: str) -> int:
    total = 0.0
    for moment in moments:
        try:
            total += float(moment.get(column) or 0)
        except (TypeError, ValueError):
            continue
    return int(total)


def _observations(moments, *, representatives, sources, lines, favourite) -> tuple[list[str], int]:
    """The representatives the episode reading named, then favourites it did not."""
    seen: list[str] = []
    for asset in representatives[:REPRESENTATIVE_LINES]:
        line = str(lines.get(asset) or "").strip()[:OBSERVATION_CHARS]
        if line and line not in seen:
            seen.append(line)
    named = len(seen)
    starred = [
        asset
        for moment in moments
        for asset in sources.get(moment["moment_id"], ())
        if favourite(asset)
    ]
    for asset in starred:
        line = str(lines.get(asset) or "").strip()[:OBSERVATION_CHARS]
        if line and line not in seen:
            seen.append(line)
        if len(seen) - named >= FAVOURITE_LINES:
            break
    return seen or ["Source description unavailable"], named or 1


def _rules_line(moments, captures: int, places: str) -> str:
    """What the no-model reader would have written, for an episode nothing read."""
    line = f"{captures} captures on {str(moments[0].get('taken') or '')[:10]}"
    return f"{line}: {places}" if places else line


def story_episode_rows(moment_rows, *, readings, sources, lines, favourite):
    """One row per canonical episode, chronological, from what was already read and banked.

    The 90-minute episode reading is the row's meaning; its representatives are the only
    captions shown. An episode nothing read keeps a factual line so it is never invisible.
    """
    grouped: dict[str, list[dict]] = {}
    for moment in moment_rows:
        card = readings.get(moment["moment_id"])
        grouped.setdefault(card.episode_id if card is not None else moment["moment_id"], []).append(
            moment
        )
    rows = []
    for episode, moments in sorted(
        grouped.items(), key=lambda item: (str(item[1][0].get("taken") or ""), item[0])
    ):
        cards = [readings[m["moment_id"]] for m in moments if m["moment_id"] in readings]
        captures = _count(moments, "visuals")
        places = _place_names(moments)
        observations, named = _observations(
            moments,
            representatives=list(
                dict.fromkeys(a for card in cards for a in card.representative_asset_ids)
            ),
            sources=sources,
            lines=lines,
            favourite=favourite,
        )
        rows.append(
            {
                "episode": episode,
                "episode_evidence_key": next((c.evidence_key for c in cards if c.evidence_key), ""),
                "capture_group": moments[0]["moment_id"],
                "moments": [m["moment_id"] for m in moments],
                "taken": min(str(m.get("taken") or "") for m in moments),
                "last_taken": max(str(m.get("taken") or "") for m in moments),
                "places": places,
                "known_people_in_group": _merged_facts_column(moments, "people"),
                "person_links": _merged_facts_column(moments, "person_links"),
                "captures": captures,
                "favourites": _count(moments, "favorites"),
                "video": _count(moments, "video"),
                "live": _count(moments, "live"),
                "what_happened": next(
                    (c.what_happened for c in cards if c.what_happened),
                    str(moments[0].get("episode_context") or "")
                    or _rules_line(moments, captures, places),
                ),
                "observations": observations,
                "named_observations": named,
            }
        )
    return rows


def _compact_dates(first: datetime, last: datetime, *, shared_year: int | None) -> str:
    pattern = "%m-%d" if shared_year is not None else "%Y-%m-%d"
    first_text, last_text = first.strftime(pattern), last.strftime(pattern)
    return first_text if first.date() == last.date() else f"{first_text}..{last_text}"


def _prompt_row(row, *, shared_year: int | None) -> dict[str, Any]:
    """What the model sees: no wall aliases, no source identifiers, no run-scoped context."""
    first, last = _instant(row["taken"]), _instant(row.get("last_taken") or row["taken"])
    return {
        "reading": row.get("reading", ""),
        "taken": _compact_dates(first, last or first, shared_year=shared_year)
        if first is not None
        else "",
        "places": row["places"],
        "people": row["known_people_in_group"],
        "person_links": row["person_links"],
        "captures": row["captures"],
        "favourites": row["favourites"],
        "video": row["video"],
        "live": row["live"],
        "what_happened": row["what_happened"],
        "observations": row["observations"],
    }


def _row_width(row) -> int:
    return len(json.dumps(_prompt_row(row, shared_year=_year(row)), ensure_ascii=False))


def _year(row) -> int | None:
    moment = _instant(row["taken"])
    return None if moment is None else moment.year


@dataclass(frozen=True)
class StoryPage:
    """One month of episode rows, or one part of a month cut at a day boundary."""

    month: str
    part: int
    rows: tuple[dict[str, Any], ...]
    trimmed: bool

    @property
    def stage(self) -> str:
        return f"story-episodes-{self.month}" + (f"-{self.part}" if self.part else "")

    @property
    def year(self) -> int | None:
        return _year(self.rows[0])

    def evidence_key(self) -> str:
        material = [
            (
                row["episode"],
                row["episode_evidence_key"],
                json.dumps(_prompt_row(row, shared_year=self.year), ensure_ascii=False),
            )
            for row in self.rows
        ]
        return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()


def _blocks(rows, width: int):
    """Rows grouped by a leading slice of their timestamp, in source order."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["taken"])[:width], []).append(row)
    return grouped


def _too_wide(rows) -> bool:
    """A day travels whole, so only its own width can overflow: how many rows it holds is
    not something dropping lines can fix."""
    return len(list(pages(rows, max_items=len(rows), max_chars=PAGE_CHARS))) > 1


def _trim(rows, keep: int) -> None:
    for row in rows:
        row["observations"] = row["observations"][:keep] or row["observations"][:1]


def _fit_one_day(rows) -> bool:
    """A single day is never cut, so a day wider than one request drops lines instead: the
    favourites it added, then the third representative, then the second."""
    if not _too_wide(rows):
        return False
    for keep in (max(row["named_observations"] for row in rows), 2, 1):
        _trim(rows, keep)
        if not _too_wide(rows):
            break
    return True


def _month_parts(rows):
    """Cut a month with the page budgets, but only where one day ends and the next begins."""
    part: list[dict] = []
    size, trimmed = 0, False
    for day in _blocks(rows, 10).values():
        cut = _fit_one_day(day)
        width = sum(_row_width(row) for row in day)
        if part and (len(part) + len(day) > PAGE_EPISODES or size + width > PAGE_CHARS):
            yield part, trimmed
            part, size, trimmed = [], 0, False
        part.extend(day)
        size += width
        trimmed = trimmed or cut
    if part:
        yield part, trimmed


def month_pages(rows) -> list[StoryPage]:
    """One page per calendar month; a month too large for one request keeps whole days."""
    book: list[StoryPage] = []
    for month, month_rows in _blocks(rows, 7).items():
        parts = list(_month_parts(month_rows))
        for index, (part, trimmed) in enumerate(parts, 1):
            for number, row in enumerate(part, 1):
                row["reading"] = f"r{number}"
            book.append(StoryPage(month, index if len(parts) > 1 else 0, tuple(part), trimmed))
    return book


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


def _split_by_day(updates, rows_by_reading):
    """The one-day rule is structure, not instruction: a month page can file a whole week
    into one episode, so every returned episode is split back at its own day boundaries."""
    out = []
    for row in updates:
        bucket: list[str] = []
        first, last = "", ""
        for reading in sorted(
            row["readings"], key=lambda r: str(rows_by_reading[r].get("taken") or "")
        ):
            taken = str(rows_by_reading[reading].get("taken") or "")
            if bucket and not _same_episode_day(taken, first, last):
                out.append({**row, "continues": None, "readings": bucket})
                bucket, first, last = [], "", ""
            bucket.append(reading)
            first = first or taken
            last = max(last, str(rows_by_reading[reading].get("last_taken") or taken))
        if bucket:
            out.append({**row, "continues": None, "readings": bucket})
    return sorted(out, key=lambda r: str(rows_by_reading[r["readings"][0]].get("taken") or ""))


def _absorb(updates, episodes, *, rows_by_reading) -> None:
    """Fold one page's placements into the episode ledger, minting global keys in order."""
    for row in updates:
        key = f"S{len(episodes) + 1:04d}"
        episodes[key] = StoryEpisode(
            key,
            row["title"],
            row["account"],
            row["significance"],
            row["role"],
            row["uncertainty"],
            list(dict.fromkeys(m for r in row["readings"] for m in rows_by_reading[r]["moments"])),
            facts=_merge_facts([], [fragment_fact(rows_by_reading[r]) for r in row["readings"]]),
            page_role=row["role"],
        )


def _carry_forward(omitted, attempts):
    """A row gets three readings of its own month before it is filed as unplaced evidence."""
    retry: list[dict] = []
    unplaced: list[dict] = []
    for row in omitted:
        attempts[row["reading"]] = attempts.get(row["reading"], 0) + 1
        target = retry if attempts[row["reading"]] < PLACEMENT_ATTEMPTS else unplaced
        target.append(row)
    return retry, unplaced


def _unplaced_episode(key, unplaced, rows_by_reading) -> StoryEpisode:
    """Three readings could not place these rows; keep them visible as their own incidental
    episode rather than silently dropping evidence."""
    return StoryEpisode(
        key,
        "Unplaced source episodes",
        "Episodes the reader could not place after three asks of their own month.",
        "Unknown; read directly before any cut.",
        "incidental",
        "unplaced by the reader",
        list(dict.fromkeys(m for r in unplaced for m in rows_by_reading[r["reading"]]["moments"])),
        facts=_merge_facts([], [fragment_fact(r) for r in unplaced]),
        page_role="incidental",
    )


def _read_month_page(judge, page: StoryPage):
    """Read one month, re-asking only its own omitted rows. Nothing carries to another month."""
    announce_stage(StageUpdate(f"Reading the period account: {page.month}"))
    attempts: dict[str, int] = {}
    queue, decisions, unplaced, omitted, asked = list(page.rows), [], [], [], 0
    while queue:
        stage = page.stage if asked == 0 else f"{page.stage}-again-{asked}"
        decisions.extend(
            read_page_answer(
                judge,
                stage=stage,
                prompt=_episode_page_prompt(
                    [_prompt_row(row, shared_year=page.year) for row in queue], page.month
                ),
                max_tokens=1400 + 220 * len(queue),
                read=partial(
                    read_episode_page,
                    offered={row["reading"] for row in queue},
                    existing={},
                    closed={},
                ),
            )
        )
        placed = {r for row in decisions for r in row["readings"]}
        omitted = [row for row in queue if row["reading"] not in placed]
        queue, filed = _carry_forward(omitted, attempts)
        unplaced.extend(filed)
        asked += 1
    return decisions, {
        "stage": page.stage,
        "month": page.month,
        "part": page.part,
        "rows": len(page.rows),
        "readings": [row["reading"] for row in page.rows],
        "omitted": [row["reading"] for row in omitted],
        "unplaced": [row["reading"] for row in unplaced],
        "evidence_key": page.evidence_key(),
        "trimmed": page.trimmed,
        "retries": asked - 1,
    }


def _episode_page_prompt(page, month) -> str:
    # The month and its rows are last; everything above them is byte-identical for every
    # page of every run, so a server that reuses a prefix reads the rules once (#981), and
    # nothing run-scoped can keep the judgment bank from answering the same month twice.
    return f"{_PROMPT} ({month})\n{json.dumps(page, ensure_ascii=False)}\n"


def _page_cache_hits(judge, book) -> dict[str, bool | None]:
    """What the judge recorded for each page's first ask, when it keeps its call rows."""
    banked: dict[str, bool | None] = {}
    for call in getattr(judge, "calls", None) or []:
        if isinstance(call, Mapping) and "cache_hit" in call:
            banked.setdefault(str(call.get("stage")), bool(call["cache_hit"]))
    return {page.stage: banked.get(page.stage) for page in book}


def _reading_calls(book, records, hits) -> dict[str, int]:
    return {
        "pages": len(book),
        "fresh": sum(1 for page in book if hits.get(page.stage) is False),
        "banked": sum(1 for page in book if hits.get(page.stage) is True),
        "retries": sum(row["retries"] for row in records),
    }


def _read_pages(judge, book, audit, record):
    try:
        return reader_map(judge, _read_month_page, book)
    except ValueError as exc:
        audit.update(status="incomplete", failure=str(exc))
        record(audit)
        raise


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
    fold: Callable[[list[dict], list[StoryEpisode], Mapping], list[dict]] | None = None,
) -> PeriodStory:
    """Read the period a month at a time, then interpret what those months add up to.

    `enrich(episodes)` runs between the page reading and the synthesis and returns, per episode
    key, the facts the synthesis weighs with (day, place, moments, pictures, favourites, and the
    memory-worthy gate's reading); they are shown on the episode's card. `fold` regroups the
    reader's stories before they are weighed.
    """
    episodes: dict[str, StoryEpisode] = {}
    book = month_pages(evidence)
    audit: dict[str, Any] = {
        "version": STORY_VERSION,
        "status": "reading",
        "source_episodes": len(evidence),
        "pages": [],
        "synthesis": [],
    }
    answers = _read_pages(judge, book, audit, record)
    hits = _page_cache_hits(judge, book)
    for page, (decisions, page_record) in zip(book, answers, strict=True):
        rows_by_reading = {row["reading"]: row for row in page.rows}
        placed = _split_by_day(decisions, rows_by_reading)
        _absorb(placed, episodes, rows_by_reading=rows_by_reading)
        if page_record["unplaced"]:
            key = f"S{len(episodes) + 1:04d}"
            episodes[key] = _unplaced_episode(
                key,
                [rows_by_reading[r] for r in page_record["unplaced"]],
                rows_by_reading,
            )
        audit["pages"].append(
            page_record | {"decisions": placed, "cache_hit": hits.get(page.stage)}
        )
        record(audit | {"episodes": [asdict(e) for e in episodes.values()]})
    audit["reading_calls"] = _reading_calls(book, audit["pages"], hits)

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
            fold=fold,
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
