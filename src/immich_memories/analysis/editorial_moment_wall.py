"""Compact, privacy-safe wire wall for the text-only production editor."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.editorial_people import EditorialPeople

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_moment_contract import MomentCard as EditorCard
    from immich_memories.analysis.moment_cards import MomentCard as ProductionCard

PRODUCTION_MOMENT_WALL_VERSION = "production-moment-wall-v1-tsv"
MAX_PRODUCTION_MOMENT_WALL_ROW_CHARS = 256

_UUIDISH = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f-]{27}|[0-9a-f]{32,})$",
    re.IGNORECASE,
)
_LONG_HEX_RUN = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)
_ANNOTATION_KEYS = frozenset({"people", "children", "activity", "location", "stitch"})


@dataclass(frozen=True, slots=True)
class RepresentativeEvidence:
    """One already-bounded literal description and its editorial reason."""

    description: str
    reason: str

    def __post_init__(self) -> None:
        if not self.description.strip() or not self.reason.strip():
            raise ValueError("moment representative evidence cannot be blank")


@dataclass(frozen=True, slots=True)
class MomentCardEvidence:
    """Structured facts retained beside the legacy compact card summary."""

    episode_meaning: str
    representatives: tuple[RepresentativeEvidence, ...]
    annotations: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        keys = tuple(key for key, _value in self.annotations)
        if (
            not self.episode_meaning.strip()
            or not self.representatives
            or any(
                key not in _ANNOTATION_KEYS or not value.strip() for key, value in self.annotations
            )
            or len(keys) != len(set(keys))
        ):
            raise ValueError("moment card evidence is incomplete or ambiguous")


@dataclass(frozen=True, slots=True)
class RenderedMomentWall:
    """One immutable wire snapshot reused by every consumer of an alias subset."""

    format_version: str
    aliases: tuple[str, ...]
    text: str
    sha256: str
    max_row_chars: int

    def __post_init__(self) -> None:
        rows = self.text.splitlines()
        if (
            self.format_version != PRODUCTION_MOMENT_WALL_VERSION
            or not self.aliases
            or not rows
            or self.sha256 != hashlib.sha256(self.text.encode()).hexdigest()
            or self.max_row_chars != max(map(len, rows))
            or self.max_row_chars > MAX_PRODUCTION_MOMENT_WALL_ROW_CHARS
        ):
            raise ValueError("rendered moment wall failed its immutable wire contract")


@dataclass(frozen=True, slots=True)
class _SourceMoment:
    alias: str
    group: Any
    card: Any
    evidence: MomentCardEvidence


@dataclass(frozen=True, slots=True)
class _ObservedPerson:
    token: str
    name: str
    fact: Any | None


def _canonical_groups(prepared: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    groups = tuple(prepared.moment_groups)
    groups_by_id = {group.group_id: group for group in groups}
    if len(groups_by_id) != len(groups):
        raise ValueError("production moment wall received duplicate canonical groups")
    episode_groups = tuple(prepared.episode_groups)
    if not episode_groups or len({group.group_id for group in episode_groups}) != len(
        episode_groups
    ):
        raise ValueError("production moment wall needs unique canonical episode groups")
    return groups_by_id, episode_groups


def _check_card_episode(card: Any, group: Any, episode_groups: tuple[Any, ...]) -> None:
    moment_members = set(group.candidate_ids)
    carrying = tuple(
        episode for episode in episode_groups if moment_members.issubset(episode.candidate_ids)
    )
    if not card.episode_id.strip() or len(carrying) != 1 or card.episode_id != carrying[0].group_id:
        raise ValueError("production moment wall card differs from its canonical episode")


def _conserved_sources(
    cards: Sequence[ProductionCard],
    groups_by_id: dict[str, Any],
    episode_groups: tuple[Any, ...],
) -> tuple[_SourceMoment, ...]:
    sources: list[_SourceMoment] = []
    previous_taken_at: datetime | None = None
    for index, card in enumerate(cards, start=1):
        group = groups_by_id.get(card.moment_id)
        evidence = card.evidence
        if group is None or card.full_asset_ids != group.candidate_ids:
            raise ValueError("production moment wall card differs from its canonical group")
        if evidence is None:
            raise ValueError("normalized production moment wall needs structured card evidence")
        _check_card_episode(card, group, episode_groups)
        taken_at = group.candidates[0].taken_at
        if previous_taken_at is not None and taken_at < previous_taken_at:
            raise ValueError("production moment wall cards must remain chronological")
        previous_taken_at = taken_at
        sources.append(_SourceMoment(f"M{index:03d}", group, card, evidence))
    if len({source.card.moment_id for source in sources}) != len(sources):
        raise ValueError("production moment wall received duplicate moment IDs")
    return tuple(sources)


def _merged_people(
    observations: dict[str, str],
    known_by_id: dict[str, Any],
    token_by_id: dict[str, str],
) -> dict[str, _ObservedPerson]:
    people: dict[str, _ObservedPerson] = {}
    for person_id, name in observations.items():
        fact = known_by_id.get(person_id)
        token = token_by_id[person_id]
        observed = _ObservedPerson(token, fact.name if fact is not None else name, fact)
        if people.setdefault(token, observed) != observed:
            raise ValueError("merged people identities carry conflicting prompt facts")
    return people


class ProductionMomentWallRenderer:
    """Render normalized tables from one conserved production workprint."""

    format_version = PRODUCTION_MOMENT_WALL_VERSION

    def __init__(
        self,
        prepared: Any,
        cards: Sequence[ProductionCard],
        facts: EditorialPeople,
    ) -> None:
        if not cards:
            raise ValueError("production moment wall needs at least one card")
        if not isinstance(facts, EditorialPeople):
            raise ValueError("production moment wall requires opaque-ID EditorialPeople")
        groups_by_id, episode_groups = _canonical_groups(prepared)
        sources = _conserved_sources(cards, groups_by_id, episode_groups)

        self._sources = sources
        self._source_by_alias = {source.alias: source for source in sources}
        self._facts = facts
        self._episodes = self._build_episode_namespace()
        self._places = self._build_place_namespace()
        self._people, self._person_token_by_id = self._build_people_namespace()
        self._private_fragments = self._collect_private_fragments()
        self._cache: dict[tuple[str, ...], RenderedMomentWall] = {}

    def render(self, cards: tuple[EditorCard, ...]) -> RenderedMomentWall:
        aliases = tuple(card.moment.alias for card in cards)
        sources = self._resolve_sources(cards, aliases)
        cached = self._cache.get(aliases)
        if cached is not None:
            return cached
        text = self._render_sources(sources)
        lowered = text.casefold()
        leaked = next(
            (fragment for fragment in self._private_fragments if fragment in lowered),
            None,
        )
        if leaked is not None:
            raise ValueError("normalized moment wall contains a private identifier fragment")
        rows = text.splitlines()
        snapshot = RenderedMomentWall(
            format_version=self.format_version,
            aliases=aliases,
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            max_row_chars=max(map(len, rows)),
        )
        self._cache[aliases] = snapshot
        return snapshot

    def _resolve_sources(
        self,
        cards: tuple[EditorCard, ...],
        aliases: tuple[str, ...],
    ) -> tuple[_SourceMoment, ...]:
        if not aliases or len(aliases) != len(set(aliases)):
            raise ValueError("normalized moment wall needs unique requested aliases")
        sources: list[_SourceMoment] = []
        for card, alias in zip(cards, aliases, strict=True):
            source = self._source_by_alias.get(alias)
            if (
                source is None
                or card.moment.group.group_id != source.group.group_id
                or card.moment.group.candidate_ids != source.group.candidate_ids
            ):
                raise ValueError("normalized moment wall alias differs from its conserved source")
            sources.append(source)
        return tuple(sources)

    def _build_episode_namespace(self) -> dict[str, tuple[str, str]]:
        episodes: dict[str, tuple[str, str]] = {}
        for source in self._sources:
            episode_id = source.card.episode_id
            current = episodes.get(episode_id)
            if current is None:
                episodes[episode_id] = (
                    f"E{len(episodes) + 1:02d}",
                    source.evidence.episode_meaning,
                )
            elif current[1] != source.evidence.episode_meaning:
                raise ValueError("one episode has conflicting meanings across moment cards")
        return episodes

    def _build_place_namespace(self) -> dict[str, str]:
        places = sorted(
            {
                place
                for source in self._sources
                for candidate in source.group.candidates
                if (place := _candidate_place(candidate))
            },
            key=lambda value: (value.casefold(), value),
        )
        return {place: f"L{index:02d}" for index, place in enumerate(places, start=1)}

    def _observed_person_names(self) -> dict[str, str]:
        observations: dict[str, str] = {}
        for source in self._sources:
            for candidate in source.group.candidates:
                for person in candidate.source.people or ():
                    person_id = str(person.id)
                    name = str(person.name or "").strip()
                    if not name:
                        continue
                    if observations.setdefault(person_id, name) != name:
                        raise ValueError("one observed person ID has conflicting names")
        return observations

    def _build_people_namespace(
        self,
    ) -> tuple[dict[str, _ObservedPerson], dict[str, str]]:
        observations = self._observed_person_names()
        known_by_id = {
            person_id: fact
            for person_id in observations
            if (fact := self._facts.fact_for_person_id(person_id)) is not None
        }

        unknown_ids = sorted(
            set(observations).difference(known_by_id),
            key=lambda person_id: (observations[person_id].casefold(), person_id),
        )
        width = max(2, len(str(len(unknown_ids))))
        token_by_id = {person_id: fact.token for person_id, fact in known_by_id.items()} | {
            person_id: f"U{index:0{width}d}" for index, person_id in enumerate(unknown_ids, start=1)
        }
        return _merged_people(observations, known_by_id, token_by_id), token_by_id

    def _collect_private_fragments(self) -> frozenset[str]:
        values: set[str] = set()
        for source in self._sources:
            values.update(
                {
                    str(source.card.moment_id),
                    str(source.card.episode_id),
                    *map(str, source.card.full_asset_ids),
                    *map(str, source.card.selectable_asset_ids),
                    *map(str, source.card.representative_asset_ids),
                }
            )
            for candidate in source.group.candidates:
                values.add(str(candidate.asset_id))
                values.update(map(str, candidate.live_photo_stitch_member_ids))
                if candidate.rendering_family_id:
                    values.add(str(candidate.rendering_family_id))
                values.update(str(person.id) for person in candidate.source.people or ())
        fragments: set[str] = set()
        for value in values:
            if _UUIDISH.fullmatch(value):
                fragments.update((value.casefold(), value.replace("-", "")[:8].casefold()))
            if match := _LONG_HEX_RUN.search(value):
                fragments.update((value.casefold(), match.group()[:8].casefold()))
        return frozenset(fragments)

    def _render_sources(self, sources: tuple[_SourceMoment, ...]) -> str:
        used_episode_ids = {source.card.episode_id for source in sources}
        used_places = {
            place
            for source in sources
            for candidate in source.group.candidates
            if (place := _candidate_place(candidate))
        }
        used_people = {
            self._person_token_by_id[str(person.id)]
            for source in sources
            for candidate in source.group.candidates
            for person in candidate.source.people or ()
            if str(person.name or "").strip()
        }
        lines = [_format_row()]
        lines.extend(self._people_rows(used_people))
        lines.extend(self._person_link_rows(used_people))
        lines.extend(self._place_rows(used_places))
        lines.extend(self._episode_rows(used_episode_ids))
        lines.extend(self._moment_rows(sources))
        lines.extend(self._moment_people_rows(sources))
        lines.extend(self._moment_place_rows(sources))
        lines.extend(self._evidence_rows(sources))
        if any(len(line) > MAX_PRODUCTION_MOMENT_WALL_ROW_CHARS for line in lines):
            raise AssertionError("moment wall emitted a row beyond its checked writer")
        return "\n".join(lines)

    def _people_rows(self, used: set[str]) -> list[str]:
        rows = [_header("people", len(used), "id|name|relationship|source|birth|first|onset|tier")]
        for token in sorted(used):
            observed = self._people[token]
            fact = observed.fact
            rows.append(
                _row(
                    "people",
                    token,
                    observed.name,
                    "unconfirmed" if fact is None else fact.relationship,
                    None if fact is None else fact.relationship_source,
                    None if fact is None else fact.birth_date,
                    None if fact is None else fact.first_month,
                    None if fact is None else fact.onset,
                    None if fact is None else fact.tier,
                )
            )
        return rows

    def _person_link_rows(self, used: set[str]) -> list[str]:
        links = sorted(
            {
                (token, link.kind, link.target_token, link.source)
                for token in used
                if (fact := self._people[token].fact) is not None
                for link in fact.links
                if link.target_token in used
            }
        )
        return [
            _header("person_links", len(links), "from|kind|to|source"),
            *(_row("person_links", *link) for link in links),
        ]

    def _place_rows(self, used: set[str]) -> list[str]:
        ordered = sorted(used, key=lambda place: self._places[place])
        return [
            _header("places", len(ordered), "id|name"),
            *(_row("places", self._places[place], place) for place in ordered),
        ]

    def _episode_rows(self, used: set[str]) -> list[str]:
        ordered = sorted(used, key=lambda episode_id: self._episodes[episode_id][0])
        return [
            _header("episodes", len(ordered), "id|meaning"),
            *(
                _row("episodes", self._episodes[episode_id][0], self._episodes[episode_id][1])
                for episode_id in ordered
            ),
        ]

    def _moment_rows(self, sources: tuple[_SourceMoment, ...]) -> list[str]:
        fields = (
            "id|episode|taken|visuals|photo|video|live|favorites|span_s|"
            "recognized_people_counts|"
            "people_head|children|activity|location_head|stitches"
        )
        rows = [_header("moments", len(sources), fields)]
        for source in sources:
            candidates = source.group.candidates
            kinds = Counter(candidate.media_kind for candidate in candidates)
            annotations = dict(source.evidence.annotations)
            span = max(0, round((candidates[-1].taken_at - candidates[0].taken_at).total_seconds()))
            observed_face_counts = sorted(
                {
                    len(candidate.source.people)
                    for candidate in candidates
                    if candidate.source.people
                }
            )
            recognized_people_counts = (
                ",".join(map(str, observed_face_counts)) if observed_face_counts else None
            )
            rows.append(
                _row(
                    "moments",
                    source.alias,
                    self._episodes[source.card.episode_id][0],
                    candidates[0].taken_at.isoformat(),
                    len(candidates),
                    kinds["photo"],
                    kinds["video"],
                    kinds["live_photo"],
                    sum(candidate.favourite for candidate in candidates),
                    span,
                    recognized_people_counts,
                    annotations.get("people"),
                    annotations.get("children"),
                    annotations.get("activity"),
                    annotations.get("location"),
                    annotations.get("stitch"),
                )
            )
        return rows

    def _moment_people_rows(self, sources: tuple[_SourceMoment, ...]) -> list[str]:
        values: list[tuple[str, str, str | None]] = []
        for source in sources:
            first_seen: dict[str, datetime] = {}
            for candidate in source.group.candidates:
                for person in candidate.source.people or ():
                    person_id = str(person.id)
                    if person_id not in self._person_token_by_id:
                        continue
                    first_seen.setdefault(self._person_token_by_id[person_id], candidate.taken_at)
            for token in sorted(first_seen):
                fact = self._people[token].fact
                values.append(
                    (
                        source.alias,
                        token,
                        _compact_age(None if fact is None else fact.birth_date, first_seen[token]),
                    )
                )
        return [
            _header("moment_people", len(values), "moment|person|age"),
            *(_row("moment_people", *value) for value in values),
        ]

    def _moment_place_rows(self, sources: tuple[_SourceMoment, ...]) -> list[str]:
        values: list[tuple[str, str]] = []
        for source in sources:
            places = {
                value
                for candidate in source.group.candidates
                if (value := _candidate_place(candidate))
            }
            values.extend(
                (source.alias, self._places[place])
                for place in sorted(places, key=lambda item: self._places[item])
            )
        return [
            _header("moment_places", len(values), "moment|place"),
            *(_row("moment_places", *value) for value in values),
        ]

    def _evidence_rows(self, sources: tuple[_SourceMoment, ...]) -> list[str]:
        values = [
            (source.alias, rank, representative.description, representative.reason)
            for source in sources
            for rank, representative in enumerate(source.evidence.representatives, start=1)
        ]
        return [
            _header("evidence", len(values), "moment|rank|description|reason"),
            *(_row("evidence", *value) for value in values),
        ]


def _candidate_place(candidate: Any) -> str:
    exif = candidate.source.exif_info
    if exif is None:
        return ""
    return ", ".join(part for part in (exif.city, exif.state, exif.country) if part)


def _compact_age(born: str | None, when: datetime) -> str | None:
    if not born:
        return None
    try:
        birthday = date.fromisoformat(born)
    except ValueError:
        return None
    days = (when.date() - birthday).days
    if days < 0:
        return "prebirth"
    if days <= 1:
        return "newborn"
    if days < 60:
        return f"{days}d"
    if days < 730:
        return f"{days // 30}mo"
    years = when.year - birthday.year - ((when.month, when.day) < (birthday.month, birthday.day))
    return f"{years}y"


def _header(name: str, count: int, fields: str) -> str:
    return _checked_row(
        name,
        "\t".join(("@table", name, str(count), *fields.split("|"))),
    )


def _format_row() -> str:
    return _checked_row("format", f"@format\t{_json_cell(PRODUCTION_MOMENT_WALL_VERSION)}")


def _row(section: str, *values: object) -> str:
    return _checked_row(
        section,
        "\t".join(
            "null"
            if value is None
            else str(value).lower()
            if isinstance(value, bool)
            else str(value)
            if isinstance(value, (int, float))
            else _json_cell(str(value))
            for value in values
        ),
    )


def _json_cell(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        encoded.replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _checked_row(section: str, value: str) -> str:
    if value.splitlines() != [value]:
        raise ValueError(f"{section} row contains a physical line break")
    if len(value) > MAX_PRODUCTION_MOMENT_WALL_ROW_CHARS:
        raise ValueError(f"{section} row exceeds {MAX_PRODUCTION_MOMENT_WALL_ROW_CHARS} characters")
    return value
