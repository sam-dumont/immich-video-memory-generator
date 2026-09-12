"""Lossless wall rows shared by the production structure editor and replay tools."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime

SHEET_FORMAT = "sealed-direct-moment-sheet-v2-moment-first-tsv"


PEOPLE_FIELDS = frozenset(
    {"id", "name", "relationship", "source", "birth", "first", "onset", "tier"}
)


PERSON_LINK_FIELDS = frozenset({"from", "kind", "to", "source"})


PersonLink = tuple[str, str, str, str]


FIELDS = (
    "moment_id",
    "taken",
    "evidence_1_description",
    "evidence_1_reason",
    "evidence_2_description",
    "evidence_2_reason",
    "people",
    "person_links",
    "places",
    "recognized_people_counts",
    "people_head",
    "children",
    "activity",
    "location_head",
    "stitches",
    "visuals",
    "photo",
    "video",
    "live",
    "favorites",
    "span_s",
    "episode_context",
)


UUID_PATTERN = re.compile(
    rb"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    rb"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])"
)


def _records(
    tables: dict[str, tuple[list[str], list[list[str]]]], name: str
) -> list[dict[str, str]]:
    try:
        fields, values = tables[name]
    except KeyError as exc:
        raise ValueError(f"direct moment sheet has no {name!r} table") from exc
    if len(fields) != len(set(fields)):
        raise ValueError(f"direct moment sheet {name!r} fields are duplicated")
    if any(len(row) != len(fields) for row in values):
        raise ValueError(f"direct moment sheet {name!r} row is incomplete")
    return [dict(zip(fields, row, strict=True)) for row in values]


def _unique_index(rows: list[dict[str, str]], key: str, name: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        identity = row.get(key)
        if not identity or identity in indexed:
            raise ValueError(f"direct moment sheet {name!r} identity is missing or duplicated")
        indexed[identity] = row
    return indexed


def _by_moment(rows: list[dict[str, str]], name: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        moment = row.get("moment")
        if not moment:
            raise ValueError(f"direct moment sheet {name!r} association has no moment")
        grouped.setdefault(moment, []).append(row)
    return grouped


def _optional(value: str | None) -> str | None:
    return None if value in (None, "", "null") else value


def _number(value: str | None, field: str) -> int | float | None:
    if value is None or value in ("", "null"):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"direct moment sheet {field!r} is not numeric") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise ValueError(f"direct moment sheet {field!r} is not numeric")
    return parsed


def _json_cell(value: str) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _tsv_row(*values: object) -> str:
    rendered = "\t".join(
        "null"
        if value is None
        else str(value).lower()
        if isinstance(value, bool)
        else str(value)
        if isinstance(value, (int, float))
        else _json_cell(str(value))
        for value in values
    )
    if "\r" in rendered or "\x00" in rendered:
        raise ValueError("direct moment sheet has an unsafe byte layout")
    return rendered


def _person_facts(
    moment_id: str,
    associations: dict[str, list[dict[str, str]]],
    people: dict[str, dict[str, str]],
) -> str | None:
    facts: list[str] = []
    seen: set[str] = set()
    for association in sorted(
        associations.get(moment_id, []), key=lambda row: row.get("person", "")
    ):
        person_id = association.get("person", "")
        if not re.fullmatch(r"P[0-9]+", person_id) or person_id in seen:
            raise ValueError("direct moment person aliases are invalid or duplicated")
        person = people.get(person_id)
        if person is None:
            raise ValueError("direct moment person association is ungrounded")
        seen.add(person_id)
        facts.append(
            f"{person_id}:name={person.get('name', '')}"
            f"|relationship={person.get('relationship', '')}"
            f"|source={person.get('source', '')}"
            f"|birth={_optional(person.get('birth')) or '?'}"
            f"|first={_optional(person.get('first')) or '?'}"
            f"|onset={_optional(person.get('onset')) or '?'}"
            f"|tier={person.get('tier', '')}"
            f"|age={_optional(association.get('age')) or '?'}"
        )
    return ";".join(facts) or None


def _validated_person_links(
    rows: list[dict[str, str]], people: dict[str, dict[str, str]]
) -> tuple[dict[str, list[PersonLink]], set[PersonLink]]:
    by_person: dict[str, list[PersonLink]] = {}
    all_links: set[PersonLink] = set()
    for row in rows:
        if set(row) != PERSON_LINK_FIELDS:
            raise ValueError("direct moment person_links fields differ from the sealed contract")
        from_id = row["from"]
        kind = row["kind"]
        to_id = row["to"]
        source = row["source"]
        link = (from_id, kind, to_id, source)
        if (
            not re.fullmatch(r"P[0-9]+", from_id)
            or not re.fullmatch(r"P[0-9]+", to_id)
            or from_id not in people
            or to_id not in people
            or not kind.strip()
            or not source.strip()
        ):
            raise ValueError("direct moment person link is incomplete or ungrounded")
        if link in all_links:
            raise ValueError("direct moment person link is duplicated")
        all_links.add(link)
        by_person.setdefault(from_id, []).append(link)
    for links in by_person.values():
        links.sort()
    return by_person, all_links


def _person_link_facts(
    moment_id: str,
    associations: dict[str, list[dict[str, str]]],
    links_by_person: dict[str, list[PersonLink]],
) -> tuple[str | None, set[PersonLink]]:
    observed: set[str] = set()
    for association in associations.get(moment_id, []):
        person_id = association.get("person", "")
        if not re.fullmatch(r"P[0-9]+", person_id) or person_id in observed:
            raise ValueError("direct moment person aliases are invalid or duplicated")
        observed.add(person_id)
    links = sorted({link for person_id in observed for link in links_by_person.get(person_id, [])})
    rendered = ";".join(
        f"{from_id}-{kind}->{to_id}|source={source}" for from_id, kind, to_id, source in links
    )
    return rendered or None, set(links)


def _place_facts(
    moment_id: str,
    associations: dict[str, list[dict[str, str]]],
    places: dict[str, dict[str, str]],
) -> str | None:
    facts: list[str] = []
    seen: set[str] = set()
    for association in sorted(
        associations.get(moment_id, []), key=lambda row: row.get("place", "")
    ):
        place_id = association.get("place", "")
        if not re.fullmatch(r"L[0-9]+", place_id) or place_id in seen:
            raise ValueError("direct moment place aliases are invalid or duplicated")
        place = places.get(place_id)
        if place is None:
            raise ValueError("direct moment place association is ungrounded")
        seen.add(place_id)
        facts.append(f"{place_id}:{place.get('name', '')}")
    return ";".join(facts) or None


def _evidence_cells(
    moment_id: str, evidence: dict[str, list[dict[str, str]]]
) -> tuple[str, str, str | None, str | None]:
    ranked: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for row in evidence.get(moment_id, []):
        try:
            rank = int(row.get("rank", ""))
        except ValueError as exc:
            raise ValueError("direct moment evidence rank is invalid") from exc
        description = row.get("description", "")
        reason = row.get("reason", "")
        if rank <= 0 or rank in seen or not description or not reason:
            raise ValueError("direct moment evidence is missing or duplicated")
        seen.add(rank)
        ranked.append((rank, description, reason))
    ranked.sort()
    if (
        not ranked
        or len(ranked) > 2
        or [row[0] for row in ranked] != list(range(1, len(ranked) + 1))
    ):
        raise ValueError("direct moment evidence must contain one or two contiguous ranks")
    first = ranked[0]
    second = ranked[1] if len(ranked) == 2 else None
    return (
        first[1],
        first[2],
        None if second is None else second[1],
        None if second is None else second[2],
    )


def direct_moment_sheet(
    tables: dict[str, tuple[list[str], list[list[str]]]], aliases: tuple[str, ...]
) -> tuple[bytes, dict[str, int]]:
    """Losslessly denormalize the sealed normalized wall into chronological moment rows."""
    people_rows = _records(tables, "people")
    if any(set(row) != PEOPLE_FIELDS for row in people_rows):
        raise ValueError("direct moment people fields differ from the sealed contract")
    people = _unique_index(people_rows, "id", "people")
    person_links_by_person, all_person_links = _validated_person_links(
        _records(tables, "person_links"), people
    )
    places = _unique_index(_records(tables, "places"), "id", "places")
    episodes = _unique_index(_records(tables, "episodes"), "id", "episodes")
    moments = _unique_index(_records(tables, "moments"), "id", "moments")
    moment_people = _by_moment(_records(tables, "moment_people"), "moment_people")
    moment_places = _by_moment(_records(tables, "moment_places"), "moment_places")
    evidence = _by_moment(_records(tables, "evidence"), "evidence")
    expected = set(aliases)
    if set(moments) != expected or any(
        set(grouped) - expected for grouped in (moment_people, moment_places, evidence)
    ):
        raise ValueError("direct moment tables differ from the sealed alias universe")

    rows: list[str] = []
    evidence_rows = 0
    emitted_person_links: set[PersonLink] = set()
    for alias in aliases:
        moment = moments[alias]
        episode = episodes.get(moment.get("episode", ""))
        if episode is None or not episode.get("meaning"):
            raise ValueError("direct moment has no grounded episode meaning")
        descriptions = _evidence_cells(alias, evidence)
        evidence_rows += 1 + int(descriptions[2] is not None)
        person_link_facts, moment_person_links = _person_link_facts(
            alias, moment_people, person_links_by_person
        )
        emitted_person_links.update(moment_person_links)
        rows.append(
            _tsv_row(
                alias,
                moment.get("taken"),
                *descriptions,
                _person_facts(alias, moment_people, people),
                person_link_facts,
                _place_facts(alias, moment_places, places),
                _optional(moment.get("recognized_people_counts")),
                _optional(moment.get("people_head")),
                _optional(moment.get("children")),
                _optional(moment.get("activity")),
                _optional(moment.get("location_head")),
                _optional(moment.get("stitches")),
                _number(moment.get("visuals"), "visuals"),
                _number(moment.get("photo"), "photo"),
                _number(moment.get("video"), "video"),
                _number(moment.get("live"), "live"),
                _number(moment.get("favorites"), "favorites"),
                _number(moment.get("span_s"), "span_s"),
                episode["meaning"],
            )
        )
    if emitted_person_links != all_person_links:
        raise ValueError("direct moment sheet does not represent every sealed person link")
    header = "\t".join(("@table", "moments", str(len(rows)), *FIELDS))
    sheet = "\n".join((f"@format\t{_json_cell(SHEET_FORMAT)}", header, *rows)).encode()
    if UUID_PATTERN.search(sheet):
        raise ValueError("direct moment sheet contains a private UUID")
    return sheet, {
        "moments": len(rows),
        "evidence_rows": evidence_rows,
        "people": len(people),
        "person_links": len(all_person_links),
        "places": len(places),
        "episodes": len(episodes),
    }


EXPECTED_WALL_FORMAT = "production-moment-wall-v1-tsv"


_MOMENT_ALIAS = re.compile(r"M[0-9]{3,}")


def _table_rows(lines: list[str]) -> dict[str, tuple[list[str], list[list[str]]]]:
    tables: dict[str, tuple[list[str], list[list[str]]]] = {}
    starts = [index for index, line in enumerate(lines) if line.startswith("@table\t")]
    for position, start in enumerate(starts):
        header = next(csv.reader([lines[start]], delimiter="\t"))
        if len(header) < 4 or header[0] != "@table":
            raise ValueError("moment wall table header is invalid")
        name = header[1]
        if name in tables:
            raise ValueError("moment wall contains duplicate tables")
        try:
            declared = int(header[2])
        except ValueError as exc:
            raise ValueError("moment wall table count is invalid") from exc
        stop = starts[position + 1] if position + 1 < len(starts) else len(lines)
        rows = [next(csv.reader([line], delimiter="\t")) for line in lines[start + 1 : stop]]
        if declared != len(rows):
            raise ValueError(f"moment wall table {name!r} count differs from its rows")
        tables[name] = (header[3:], rows)
    return tables


def _wall_lines(wall_bytes: bytes) -> list[str]:
    try:
        text = wall_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("moment wall is not UTF-8") from exc
    if not text or "\r" in text or "\x00" in text:
        raise ValueError("moment wall has an unsafe byte layout")
    lines = text.splitlines()
    if not lines or lines[0] != f'@format\t"{EXPECTED_WALL_FORMAT}"':
        raise ValueError("moment wall format is not the sealed production TSV")
    return lines


def _moment_columns(fields: list[str]) -> tuple[int, int, int]:
    try:
        return fields.index("id"), fields.index("taken"), fields.index("visuals")
    except ValueError as exc:
        raise ValueError("moment wall moments table lacks identity, time, or visuals") from exc


def _moment_row(row: list[str], columns: tuple[int, int, int]) -> tuple[str, datetime, int]:
    alias_column, taken_column, visuals_column = columns
    if max(columns) >= len(row):
        raise ValueError("moment wall moment row is incomplete")
    alias = row[alias_column]
    if _MOMENT_ALIAS.fullmatch(alias) is None:
        raise ValueError("moment wall contains an invalid moment alias")
    try:
        taken = datetime.fromisoformat(row[taken_column])
        available = int(row[visuals_column])
    except ValueError as exc:
        raise ValueError("moment wall contains an invalid timestamp or visual count") from exc
    if available <= 0:
        raise ValueError("moment wall contains a non-positive visual count")
    return alias, taken, available


def _read_wall_index(wall_bytes: bytes) -> tuple[tuple[str, ...], dict[str, int]]:
    tables = _table_rows(_wall_lines(wall_bytes))
    if "moments" not in tables:
        raise ValueError("moment wall has no moments table")
    fields, rows = tables["moments"]
    columns = _moment_columns(fields)
    read = [_moment_row(row, columns) for row in rows]
    aliases = [alias for alias, _taken, _available in read]
    taken_at = [taken for _alias, taken, _available in read]
    visuals = {alias: available for alias, _taken, available in read}
    expected = tuple(f"M{index:03d}" for index in range(1, len(aliases) + 1))
    if tuple(aliases) != expected or tuple(taken_at) != tuple(sorted(taken_at)):
        raise ValueError("moment wall aliases or chronology are not canonical")
    if not aliases:
        raise ValueError("moment wall is empty")
    return tuple(aliases), visuals
