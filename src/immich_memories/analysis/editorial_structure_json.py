"""Bounded JSON envelopes shared by the production structure editor."""

from __future__ import annotations

import json
from collections.abc import Iterator


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
