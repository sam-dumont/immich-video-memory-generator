"""Bounded JSON envelopes shared by the production structure editor."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

REASON_WORDS = 15


MAX_CHAPTERS = 15


def _words(text: str) -> int:
    return len(text.split())


def _fit(text: str, counter: dict) -> str:
    words = text.split()
    if len(words) > REASON_WORDS:
        counter["fitted"] += 1
        return " ".join(words[:REASON_WORDS])
    return " ".join(words)


def json_scan(text: str) -> Iterator[tuple[str, bool]]:
    """Each character, with whether the scanner sits inside a string literal after it.

    Both repairs below have to tell a brace that structures the answer from one the
    model wrote inside a caption, and neither can trust the text to decode.
    """
    inside = escape = False
    for character in text:
        if inside:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                inside = False
        elif character == '"':
            inside = True
        yield character, inside


def _open_containers(text: str) -> list[str]:
    stack: list[str] = []
    for character, inside in json_scan(text):
        if inside:
            continue
        if character in "[{":
            stack.append(character)
        elif character in "]}" and stack:
            stack.pop()
    return stack


def balance_json(text: str, attempts: int = 3) -> str:
    """D12c: a small model drops a closing bracket now and then (`...]},"one_offs":` with the array left open). At the
    decoder's error position, if an array is open and a key follows, close the array and try again. Bounded."""
    for _ in range(attempts):
        try:
            json.JSONDecoder().raw_decode(text)
            return text
        except json.JSONDecodeError as exc:
            pos = exc.pos
            stack = _open_containers(text[:pos])
            # the decoder has read the next key ("one_offs") as an array element and now stands on its ':' — the array
            # should have closed before the comma that introduced that key
            if stack and stack[-1] == "[" and text[pos : pos + 1] == ":":
                comma = text.rfind(',"', 0, pos)
                if comma > 0:
                    text = text[:comma] + "]" + text[comma:]
                    continue
            return text
    return text


def close_inner_containers(stack: list[str], closer: str) -> list[str]:
    """The closers owed to structures nested inside the one this closer ends.

    A closer that does not match the innermost opener means the model forgot to close
    the inner structure (an object inside an array, most often); it is closed first.
    Pops what it closes off `stack`.
    """
    inserted: list[str] = []
    want = "]" if closer == "}" else "}"
    while stack and ((closer == "]" and stack[-1] == "{") or (closer == "}" and stack[-1] == "[")):
        inserted.append(want)
        stack.pop()
        want = "]" if stack and stack[-1] == "[" else "}"
    return inserted


def _first_object(raw: str) -> tuple[dict, bool]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    text = balance_json(text[text.index("{") :])
    obj, end = json.JSONDecoder().raw_decode(text)
    return obj, bool(text[end:].strip())


def _chapter(row: object, *, anchors: list[str], used: set[str], diag: dict) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != {"anchors", "share", "show"}:
        raise ValueError("chapter shape")
    if (
        not isinstance(row["anchors"], list)
        or not row["anchors"]
        or any(a not in anchors or a in used for a in row["anchors"])
    ):
        raise ValueError("chapter anchors unknown or repeated")
    share = row["share"]
    if not isinstance(share, (int, float)) or share < 0 or share > 100:
        raise ValueError("share out of range")
    if not isinstance(row["show"], str) or not row["show"].strip():
        raise ValueError("chapter show line missing")
    used.update(row["anchors"])
    return {"anchors": list(row["anchors"]), "share": float(share), "show": _fit(row["show"], diag)}


def _strict_structure(raw: str, *, anchors: list[str]) -> dict:
    obj, trailing = _first_object(raw)
    if (
        not isinstance(obj, dict)
        or set(obj) != {"chapters"}
        or not isinstance(obj["chapters"], list)
    ):
        raise ValueError("structure envelope must be {chapters:[...]}")
    if not 3 <= len(obj["chapters"]) <= MAX_CHAPTERS:
        raise ValueError("chapter count out of range (3..15)")
    used: set[str] = set()
    diag = {"fitted": 0, "trailing_prose": int(trailing)}
    chapters = [_chapter(row, anchors=anchors, used=used, diag=diag) for row in obj["chapters"]]
    total = sum(chapter["share"] for chapter in chapters)
    if total <= 0:
        raise ValueError("shares sum to zero")
    for chapter in chapters:
        chapter["share"] = round(100.0 * chapter["share"] / total, 2)
    # anchors the model left out belong to no chapter: budget 0, recorded
    left_out = [a for a in anchors if a not in used]
    return {"chapters": chapters, "left_out": left_out, "diagnostics": diag}


def _strict_weighing(raw: str, *, chapter_labels: list[str]) -> dict:
    """The weighing answer: shares per beat. A beat the model left out is weighed 0 (D27); an unknown
    or repeated label, or no row at all, is a parse failure."""
    obj, trailing = _first_object(raw)
    rows = obj.get("chapters") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError("weighing envelope must be {chapters:[...]} with at least one row")
    labels = [r.get("chapter") for r in rows]
    if len(set(labels)) != len(labels) or not set(labels) <= set(chapter_labels):
        raise ValueError("weighing rows must name known beats, each once")
    by_label = {r["chapter"]: r for r in rows}
    diag = {
        "fitted": 0,
        "trailing_prose": int(trailing),
        "omitted_by_model": [c for c in chapter_labels if c not in by_label],
    }
    shares = {}
    for label in chapter_labels:
        share = float(by_label.get(label, {}).get("share", 0) or 0)
        if share < 0 or share > 100:
            raise ValueError("share out of range")
        shares[label] = (share, str(by_label.get(label, {}).get("show", "")))
    return {"shares": shares, "diagnostics": diag}
