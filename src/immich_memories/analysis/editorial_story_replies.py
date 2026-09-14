"""Read what a small model answered about a period's stories.

The vocabulary here is the one the answers are allowed to use, and every reader is
lenient in the same direction: a malformed fragment is dropped, never the reading.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from immich_memories.analysis.editorial_structure_json import (
    balance_json,
    close_inner_containers,
    json_scan,
)
from immich_memories.analysis.strict_json import model_text_rows

STORY_VERSION = "period-story-v3-stories"
ROLES = {"central", "supporting", "texture", "incidental"}
# A story's weight in THIS memory, in the model's words; the arithmetic lives in the planner.
WEIGHTS = ("dominant", "major", "minor", "glimpse", "none")
WEIGHT_ROLE = {
    "dominant": "central",
    "major": "central",
    "minor": "supporting",
    "glimpse": "texture",
    "none": "incidental",
}
GATE_WEIGHT = {"remarkable": "minor", "maybe": "glimpse", "background": "none"}
EPISODE_ID = r"S\d{4}"

_RELATION = re.compile(r"\(([^;()]+(?:\([^()]*\))?)")


def relations_on(line: str) -> list[str]:
    """The relation of each known person on an annotation line, as the people file states it
    ("partner", "son", "grandmother", "friend"), in order, without names."""
    if not line:
        return []
    match = re.search(r"\| with ([^|]+)", line)
    if not match:
        return []
    out = []
    for rel in _RELATION.findall(match.group(1)):
        rel = rel.strip().rstrip(";").strip()
        if rel and rel not in out and not rel.startswith("aged"):
            out.append(rel)
    return out


def _text(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"story {name} requires text")
    return value.strip()


def _unfenced(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    return text.replace("```", "").strip()


def _closed(text: str) -> str:
    """The same text with every structure the model left open closed."""
    stack: list[str] = []
    out: list[str] = []
    inside = False
    for character, inside in json_scan(text):
        if not inside:
            if character in "[{":
                stack.append(character)
            elif character in "]}":
                out.extend(close_inner_containers(stack, character))
                if stack:
                    stack.pop()
        out.append(character)
    if inside:  # the answer stopped in the middle of a string literal
        out.append('"')
    return "".join(out) + "".join("]" if ch == "[" else "}" for ch in reversed(stack))


def _lenient_object(raw: str) -> dict:
    """A small model closes its arrays and forgets the outer brace, or wraps JSON in a fence.
    Take the first object, close whatever is still open outside strings, then decode."""
    text = _unfenced(raw)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    # strict=False: the model writes raw newlines and tabs inside strings now and then
    decoder = json.JSONDecoder(strict=False)
    text = _closed(text[start:])
    try:
        return decoder.raw_decode(text)[0]  # the first object wins; trailing chatter is ignored
    except json.JSONDecodeError:
        return decoder.raw_decode(balance_json(text))[0]


def _new_episode_definitions(obj) -> dict[str, dict[str, str]]:
    definitions: dict[str, dict[str, str]] = {}
    for row in obj.get("new_episodes") or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        role = row["role"] if row.get("role") in ROLES else "supporting"
        definitions[row["id"]] = {
            "title": str(row.get("title") or "Untitled episode")[:120],
            "account": str(row.get("account") or "")[:700],
            "role": role,
        }
    return definitions


def _reopened(episode: str, closed, new_defs) -> str:
    """The model continued an episode that is past its 36-hour span: same kind of occasion
    recurring later. Open a new episode with that title rather than dropping the fragment."""
    again = f"again:{episode}"
    new_defs.setdefault(
        again, {"title": closed[episode].title, "account": "", "role": closed[episode].role}
    )
    return again


def _placements(rows, *, offered, existing, closed, new_defs) -> dict[str, list[str]]:
    placed: dict[str, list[str]] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        reading, episode = row.get("reading"), row.get("episode")
        if not isinstance(reading, str) or reading not in offered or reading in seen:
            continue
        if isinstance(episode, str) and episode in closed and episode not in existing:
            episode = _reopened(episode, closed, new_defs)
        if not isinstance(episode, str) or (episode not in existing and episode not in new_defs):
            continue
        seen.add(reading)
        placed.setdefault(episode, []).append(reading)
    return placed


def _continuing_row(key: str, carried, refs) -> dict[str, Any]:
    return {
        "continues": key,
        "title": carried.title,
        "account": carried.account,
        "significance": carried.significance,
        "role": carried.role,
        "uncertainty": carried.uncertainty,
        "readings": refs,
    }


def _new_episode_row(definition, refs) -> dict[str, Any]:
    return {
        "continues": None,
        "title": definition["title"],
        "account": definition["account"],
        "significance": "",
        "role": definition["role"],
        "uncertainty": "",
        "readings": refs,
    }


def read_episode_page(raw, *, offered, existing, closed=None):
    """Place the NEW fragments only. The model never re-lists earlier episodes.

    Answer shape: {"fragments": [{"reading": "M064/1", "episode": "S0023" | "new-1"}],
                   "new_episodes": [{"id": "new-1", "title", "account", "role"}]}.
    Unknown episode ids, unknown fragments and repeats are dropped; the caller carries any
    unplaced fragment into the next page. Returns rows in the older row shape.
    """
    obj = _lenient_object(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("fragments"), list):
        raise ValueError("story page needs a fragments list")
    new_defs = _new_episode_definitions(obj)
    placed = _placements(
        obj["fragments"],
        offered=offered,
        existing=existing,
        closed=closed or {},
        new_defs=new_defs,
    )
    return [
        _continuing_row(episode, existing[episode], refs)
        if episode in existing
        else _new_episode_row(new_defs[episode], refs)
        for episode, refs in placed.items()
    ]


def _title_match(name, titles):
    """The model answers with the title it wrote as often as with the id it was given."""
    query = name.strip().lower()
    if not query:
        return None
    for key, title in titles.items():
        if title.strip().lower() == query:
            return key
    for key, title in titles.items():
        low = title.strip().lower()
        if low and (low in query or query in low):
            return key
    return None


def _candidate_refs(row):
    """Where an episode reference can sit: the row itself, an episode/episodes field, a title."""
    if isinstance(row, str):
        return [row]
    if not isinstance(row, Mapping):
        return []
    refs: list[Any] = []
    for value in (row.get("episode"), row.get("episodes"), row.get("title")):
        refs.extend(value if isinstance(value, list) else [value])
    return refs


def _reference_ids(row, titles):
    """Ids first, then the title they were written as. Unresolvable references return nothing."""
    ids: list[str] = []
    for ref in _candidate_refs(row):
        if not isinstance(ref, str):
            continue
        found = [i for i in re.findall(EPISODE_ID, ref) if i in titles]
        if not found:
            match = _title_match(ref, titles)
            found = [match] if match else []
        ids.extend(i for i in found if i not in ids)
    return ids


def _synthesis_object(raw: str) -> dict:
    """The synthesis is long prose in JSON; one dropped quote in a hundred-row connections list
    must not lose the thesis and roles. Repair the id-list slip, then drop connections, then give up."""
    try:
        return _lenient_object(raw)
    except (ValueError, json.JSONDecodeError):
        pass
    repaired = re.sub(r'"(S\d{4})(?=[\]\},])', r'"\1"', raw)  # "S0039] -> "S0039"]
    try:
        return _lenient_object(repaired)
    except (ValueError, json.JSONDecodeError):
        pass
    without = re.sub(
        r'"connections"\s*:\s*\[.*?\]\s*,\s*(?=")',
        '"connections": [], ',
        repaired,
        flags=re.DOTALL,
    )
    return _lenient_object(without)


def _connections(obj, titles) -> list[dict[str, Any]]:
    rows = []
    for row in obj.get("connections") or []:
        text = row.get("relationship") if isinstance(row, dict) else str(row)
        ids = [
            i
            for i in dict.fromkeys(re.findall(EPISODE_ID, json.dumps(row, ensure_ascii=False)))
            if i in titles
        ]
        ids = ids or _reference_ids(row, titles)
        if ids:
            rows.append({"episodes": ids, "relationship": str(text or "")[:300]})
    return rows


def _priorities(obj, titles) -> tuple[list[dict[str, Any]], int]:
    rows, unresolved = [], 0
    for row in obj.get("priorities") or []:
        ids = _reference_ids(row, titles)
        purpose = str(row.get("purpose") or "")[:300] if isinstance(row, Mapping) else ""
        if ids:
            rows.append({"episodes": ids, "purpose": purpose})
        else:
            unresolved += 1
    return rows, unresolved


def _roles(obj, titles) -> dict[str, str]:
    raw_roles = obj.get("roles") or {}
    rows = (
        raw_roles.items()
        if isinstance(raw_roles, Mapping)
        else [(r.get("episode"), r.get("role")) for r in raw_roles if isinstance(r, Mapping)]
    )
    roles: dict[str, str] = {}
    for key, role in rows:
        ids = _reference_ids(key, titles) if not (isinstance(key, str) and key in titles) else [key]
        if ids and isinstance(role, str) and role.strip().lower() in ROLES:
            roles[ids[0]] = role.strip().lower()
    return roles


def _retitles(obj, titles) -> dict[str, str]:
    raw_titles = obj.get("retitle") or {}
    if not isinstance(raw_titles, Mapping):
        return {}
    return {
        key: title.strip()[:120]
        for key, title in raw_titles.items()
        if isinstance(key, str) and key in titles and isinstance(title, str) and title.strip()
    }


def _read_synthesis(raw, valid):
    """Lenient: connections may be prose, and either list may name an episode by its title.

    `valid` maps episode id to title; a bare set of ids is accepted, and then only ids resolve.
    """
    obj = _synthesis_object(raw)
    if (
        not isinstance(obj, dict)
        or not isinstance(obj.get("thesis"), str)
        or not obj["thesis"].strip()
    ):
        raise ValueError("period story synthesis needs a thesis")
    titles = dict(valid) if isinstance(valid, Mapping) else dict.fromkeys(valid, "")
    priorities, unresolved = _priorities(obj, titles)
    uncertainties = [
        str(u)[:300]
        for u in (model_text_rows(obj.get("uncertainties")) or [])
        if isinstance(u, str) and u.strip()
    ]
    stories, unresolved = _read_stories(obj, titles, priorities, unresolved)
    about = obj.get("about")
    about_ids = _reference_ids({"episodes": about}, titles) if isinstance(about, list) else []
    return {
        "thesis": obj["thesis"].strip(),
        "connections": _connections(obj, titles),
        "priorities": priorities,
        "uncertainties": uncertainties,
        "unresolved_priorities": unresolved,
        "roles": _roles(obj, titles),
        "retitle": _retitles(obj, titles),
        "stories": stories,
        "about": about_ids,
    }


def _weight(value) -> str:
    text = str(value or "").strip().lower()
    return next((name for name in WEIGHTS if text.startswith(name)), "")


def _story(key: str, ids, *, title, weight, purpose) -> dict[str, Any]:
    return {
        "key": key,
        "title": str(title or ids[0]).strip()[:120],
        "episodes": ids,
        "weight": weight,
        "purpose": str(purpose or "")[:300],
    }


def _read_stories(obj, titles, priorities, unresolved):
    """Stories group episodes and carry a weight. An answer in the older priorities shape still
    yields stories: the head third of the ranking major, the rest minor. An episode named twice
    stays where it was first placed."""
    stories: list[dict[str, Any]] = []
    placed: set[str] = set()
    for row in obj.get("stories") or []:
        if not isinstance(row, Mapping):
            continue
        ids = [i for i in _reference_ids(row, titles) if i not in placed]
        if not ids:
            unresolved += 1
            continue
        placed.update(ids)
        stories.append(
            _story(
                f"K{len(stories) + 1:02d}",
                ids,
                title=row.get("title") or titles.get(ids[0]),
                weight=_weight(row.get("weight")),
                purpose=row.get("purpose"),
            )
        )
    if not stories and priorities:
        head = max(1, len(priorities) // 3)
        for index, row in enumerate(priorities):
            ids = [i for i in row["episodes"] if i not in placed]
            if ids:
                placed.update(ids)
                stories.append(
                    _story(
                        f"K{len(stories) + 1:02d}",
                        ids,
                        title=titles.get(ids[0]),
                        weight="major" if index < head else "minor",
                        purpose=row.get("purpose"),
                    )
                )
    return stories, unresolved
