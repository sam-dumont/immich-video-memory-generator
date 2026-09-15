"""The period reading's prompts and the strict reader of its answers.

The compact episode table, the synthesis of an oversized period's parts, the repair ask,
and the parser that refuses anything it cannot ground in the episodes it offered.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from immich_memories.analysis.strict_json import (
    bounded_model_text,
    final_json_object,
    model_text_rows,
)
from immich_memories.store.period_insights import (
    BankedInsightEvidence,
    BankedPeriodInsight,
    PeriodEpisodeGrounding,
    PeriodInsightIdentity,
)

TEXT_PERIOD_SCHEMA_VERSION = "period-insight-text-v1"
_THESIS_MAX_CHARS = 800
_OBSERVATION_MAX_CHARS = 180
_TENSION_MAX_CHARS = 180
_THREAD_MAX_CHARS = 120
_MAX_EVIDENCE_ROWS = 20
_MAX_LIST_ROWS = 12
_SYNTHESIS_EVIDENCE_ROWS_PER_PART = 8

_PROMPT = """Read this period from one family's photo library. The compact table contains every
readable episode in chronological order; it is not a shortlist. L and P values refer to the
dictionaries; - means unavailable. Use only these facts. Consecutive away days that belong together
are one occasion. State what the period was, not whether it was good. Episode and asset identifiers
are private; use only the numeric episode aliases.

Return JSON only, at most 20 evidence rows, 12 tensions, 12 recurring threads.
Each observation is at most 180 characters. Each "episodes" list MUST contain only 1 to 5
representative episode aliases, even when the observation applies to hundreds of episodes.
Never enumerate every matching episode: the complete table remains the grounding.
{{"schema_version":"period-insight-text-v1","thesis":"at most 80 words",
"evidence":[{{"observation":"what this shows about the period","episodes":[1,2]}}],
"tensions":["at most 12 words"],"recurring_threads":["at most 8 words"]}}

shared_year={shared_year}
places:
{places}
people:
{people}
columns=episode\tdate_or_span\tplace\tpeople\tassets\thappened
{episodes}"""

_SYNTHESIS_PROMPT = """Synthesize this period from the readings of its consecutive parts. Each part was read
from the complete episode table of one stretch of time; together the parts cover the whole period in
chronological order. Episode aliases are global numbers over the whole period; each part lists its
range. Say what the WHOLE period was: its arc, what recurs across parts, what changed between them,
the tensions that hold across it. Do not concatenate the part theses. Cite only aliases inside a
part's range. State what the period was, not whether it was good.

Return JSON only, at most 20 evidence rows, 12 tensions, 12 recurring threads:
Each observation is at most 180 characters. Each "episodes" list MUST contain only 1 to 5
representative episode aliases, chosen across the relevant parts. Never enumerate every matching
episode: the complete episode grounding is retained separately from these example citations.
{{"schema_version":"period-insight-text-v1","thesis":"at most 80 words",
"evidence":[{{"observation":"what this shows about the period","episodes":[1,2]}}],
"tensions":["at most 12 words"],"recurring_threads":["at most 8 words"]}}

parts={part_count} episodes={episode_count}
Cite only aliases between 1 and {episode_count}.
{parts}"""


@dataclass(frozen=True)
class _PeriodEpisodeFacts:
    """Structured facts shared by durable grounding and compact transport."""

    first_taken_at: datetime
    last_taken_at: datetime
    place: str
    people: tuple[str, ...]
    asset_count: int
    what_happened: str


def _compact_dates(episode: _PeriodEpisodeFacts, *, shared_year: int | None) -> str:
    first = episode.first_taken_at.date()
    last = episode.last_taken_at.date()
    if shared_year is not None:
        first_text = first.strftime("%m-%d")
        last_text = last.strftime("%m-%d")
    else:
        first_text = first.isoformat()
        last_text = last.isoformat()
    return first_text if first == last else f"{first_text}..{last_text}"


def _prompt_for(facts: tuple[_PeriodEpisodeFacts, ...]) -> str:
    years = {
        taken.year for episode in facts for taken in (episode.first_taken_at, episode.last_taken_at)
    }
    shared_year = next(iter(years)) if len(years) == 1 else None
    places = tuple(dict.fromkeys(episode.place for episode in facts if episode.place))
    people = tuple(dict.fromkeys(episode.people for episode in facts if episode.people))
    place_aliases = {value: f"L{index}" for index, value in enumerate(places, start=1)}
    people_aliases = {value: f"P{index}" for index, value in enumerate(people, start=1)}
    rendered = "\n".join(
        "\t".join(
            (
                str(alias),
                _compact_dates(episode, shared_year=shared_year),
                place_aliases.get(episode.place, "-"),
                people_aliases.get(episode.people, "-"),
                str(episode.asset_count),
                json.dumps(episode.what_happened, ensure_ascii=False),
            )
        )
        for alias, episode in enumerate(facts, start=1)
    )
    place_dictionary = "\n".join(
        f"{place_aliases[value]}\t{json.dumps(value, ensure_ascii=False)}" for value in places
    )
    people_dictionary = "\n".join(
        f"{people_aliases[value]}\t{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        for value in people
    )
    return _PROMPT.format(
        shared_year=shared_year or "-",
        places=place_dictionary or "-",
        people=people_dictionary or "-",
        episodes=rendered,
    )


def _synthesis_prompt(
    leaves: tuple[tuple[tuple[int, int], BankedPeriodInsight], ...],
    grounding: tuple[PeriodEpisodeGrounding, ...],
) -> str:
    alias_of = {episode.episode_id: index for index, episode in enumerate(grounding, start=1)}
    blocks = []
    for number, ((start, end), leaf) in enumerate(leaves, start=1):
        first = grounding[start].rendered_line.split(" | ", 1)[0]
        last = grounding[end - 1].rendered_line.split(" | ", 1)[0]
        rows = [
            f"- [{', '.join(str(alias_of[episode_id]) for episode_id in item.episode_ids)}] {item.observation}"
            for item in leaf.evidence[:_SYNTHESIS_EVIDENCE_ROWS_PER_PART]
        ]
        blocks.append(
            "\n".join(
                (
                    f"part {number}: episodes {start + 1}..{end}, {first.split('..')[0]}..{last.split('..')[-1]}",
                    f"thesis: {leaf.thesis}",
                    "evidence:",
                    *rows,
                    f"tensions: {'; '.join(leaf.tensions) or '-'}",
                    f"recurring_threads: {'; '.join(leaf.recurring_threads) or '-'}",
                )
            )
        )
    return _SYNTHESIS_PROMPT.format(
        part_count=len(leaves),
        episode_count=len(grounding),
        parts="\n\n".join(blocks),
    )


def _repair_prompt(prompt: str, problem: str, episode_count: int) -> str:
    return (
        prompt
        + f"\n\nYour previous answer could not be used ({problem}). Answer again with ONE valid JSON object only, "
        f"schema_version period-insight-text-v1, at most 20 evidence rows, aliases between 1 and {episode_count}."
    )


def _unfenced_object(text: str) -> tuple[dict | None, str, bool]:
    """The leading object, whatever follows it, and whether a Markdown fence wrapped it."""
    fenced = text.startswith("```")
    if fenced:
        opening, separator, text = text.partition("\n")
        if not separator or opening.rstrip().lower() not in {"```", "```json"}:
            return None, "", fenced
        text = text.lstrip()
    if not text.startswith("{"):
        return None, "", fenced
    try:
        payload, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None, "", fenced
    return payload, text[end:].strip(), fenced


def _is_only_a_note(commentary: str) -> bool:
    """A second object, a broken enclosing object, or another JSON value is not a note.

    Decode only at the start: scanning could salvage a nested or conflicting answer.
    """
    if (
        not commentary
        or any(character in commentary for character in "{}")
        or "```" in commentary
        or commentary.startswith(("[", '"'))
    ):
        return False
    try:
        json.JSONDecoder().raw_decode(commentary)
    except json.JSONDecodeError:
        return True
    return False


def _period_response_object(raw: str) -> dict | None:
    """Keep accepted envelopes, then recover a complete leading object plus a prose note."""
    payload = final_json_object(raw)
    if payload is not None:
        return payload
    payload, commentary, fenced = _unfenced_object(raw.lstrip())
    if payload is None:
        return None
    if fenced:
        closing, _, commentary = commentary.partition("\n")
        if closing.rstrip() != "```":
            return None
        commentary = commentary.strip()
    return payload if _is_only_a_note(commentary) else None


def _read_response(
    raw: str,
    identity: PeriodInsightIdentity,
    grounding: tuple[PeriodEpisodeGrounding, ...],
) -> BankedPeriodInsight | None:
    payload = _period_response_object(raw)
    if payload is None or payload.get("schema_version") != TEXT_PERIOD_SCHEMA_VERSION:
        return None
    thesis = bounded_model_text(payload.get("thesis"), max_chars=_THESIS_MAX_CHARS)
    evidence = _evidence(payload.get("evidence"), grounding)
    tensions = _text_list(
        payload.get("tensions"),
        max_rows=_MAX_LIST_ROWS,
        max_chars=_TENSION_MAX_CHARS,
    )
    recurring_threads = _text_list(
        payload.get("recurring_threads"),
        max_rows=_MAX_LIST_ROWS,
        max_chars=_THREAD_MAX_CHARS,
    )
    if thesis is None or evidence is None or tensions is None or recurring_threads is None:
        return None
    try:
        return BankedPeriodInsight(
            identity=identity,
            episode_grounding=grounding,
            thesis=thesis,
            evidence=evidence,
            tensions=tensions,
            recurring_threads=recurring_threads,
        )
    except ValueError:
        return None


def _cited(aliases: object, grounding: tuple[PeriodEpisodeGrounding, ...]) -> tuple | None:
    """The episodes one evidence row names, or None when it names something outside the period."""
    if (
        not isinstance(aliases, list)
        or not aliases
        or any(not _is_alias(alias) or alias > len(grounding) for alias in aliases)
        or len(aliases) != len(set(aliases))
    ):
        return None
    return tuple(grounding[alias - 1] for alias in aliases)


def _evidence(
    value: object,
    grounding: tuple[PeriodEpisodeGrounding, ...],
) -> tuple[BankedInsightEvidence, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    evidence = []
    # WHY: a 21st row is surplus, not a reason to lose the reading
    for item in value[:_MAX_EVIDENCE_ROWS]:
        if not isinstance(item, Mapping):
            continue
        observation = bounded_model_text(
            item.get("observation"),
            max_chars=_OBSERVATION_MAX_CHARS,
        )
        cited = _cited(item.get("episodes"), grounding)
        # WHY: one row citing an alias outside the period is that row's fault, not the reading's
        if observation is None or cited is None:
            continue
        evidence.append(
            BankedInsightEvidence(
                observation=observation,
                episode_ids=tuple(episode.episode_id for episode in cited),
                asset_ids=tuple(
                    dict.fromkeys(
                        asset_id
                        for episode in cited
                        for asset_id in episode.representative_asset_ids
                    )
                ),
            )
        )
    return tuple(evidence) or None


def _text_list(value: object, *, max_rows: int, max_chars: int) -> tuple[str, ...] | None:
    values = model_text_rows(value)
    if values is None or len(values) > max_rows:
        return None
    rows = tuple(bounded_model_text(item, max_chars=max_chars) for item in values)
    if any(item is None for item in rows):
        return None
    return tuple(item for item in rows if item is not None)


def _is_alias(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _ungrounded_rows(rows: Iterable, grounding: tuple[PeriodEpisodeGrounding, ...]) -> list:
    return [
        row
        for row in rows
        if not isinstance(row, Mapping)
        or not isinstance(row.get("episodes"), list)
        or any(not _is_alias(a) or a > len(grounding) for a in row.get("episodes") or [])
        or not row.get("episodes")
    ]


def _response_problem(raw: str, grounding: tuple[PeriodEpisodeGrounding, ...]) -> str:
    """Why an answer did not read, in a phrase the repair ask can quote."""
    payload = _period_response_object(raw)
    if payload is None:
        return "not one JSON object" if raw.strip() else "empty answer"
    if payload.get("schema_version") != TEXT_PERIOD_SCHEMA_VERSION:
        return "schema_version missing or wrong"
    if bounded_model_text(payload.get("thesis"), max_chars=_THESIS_MAX_CHARS) is None:
        return "thesis missing or too long"
    rows = payload.get("evidence")
    if not isinstance(rows, list) or not rows:
        return "evidence missing"
    if len(_ungrounded_rows(rows, grounding)) == len(rows):
        return f"every evidence row cites an alias outside 1..{len(grounding)} or none"
    if (
        _text_list(payload.get("tensions"), max_rows=_MAX_LIST_ROWS, max_chars=_TENSION_MAX_CHARS)
        is None
    ):
        return "tensions missing or more than 12"
    if (
        _text_list(
            payload.get("recurring_threads"), max_rows=_MAX_LIST_ROWS, max_chars=_THREAD_MAX_CHARS
        )
        is None
    ):
        return "recurring_threads missing or more than 12"
    return "evidence could not be grounded"
