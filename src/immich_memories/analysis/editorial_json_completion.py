"""Complete decision payloads are distinct from trailing model commentary."""

from __future__ import annotations

import json

JSON_RECOVERY_POLICY = "complete-final-json-v2-format-repair"
JSON_FIELDS_POLICY = "exact-fields-v2-complete-sequence"
JSON_EMPTY_ARRAY_POLICY = "explicit-empty-array-pairs-v1"


class JSONDecisionError(ValueError):
    """A received response failed the complete-object contract, not the HTTP transport."""

    def __init__(self, message: str, *, raw: str = ""):
        super().__init__(message)
        self.raw = raw


def json_format_repair_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\nThe previous answer was not one complete valid JSON object. Answer again using "
        "the original evidence and requested schema. Use ONE outer pair of braces for the "
        "complete object. Separate its properties with commas; do not close the outer object "
        "between properties. Include every required field, and return only the complete JSON object."
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def validate_empty_array_pairs(fields: tuple[str, ...], pairs: tuple[tuple[str, str], ...]) -> None:
    """Require disjoint, explicitly declared fields for conditional empty arrays."""
    if not isinstance(pairs, tuple):
        raise ValueError("JSON empty-array pairs must be an immutable tuple")
    used: set[str] = set()
    for pair in pairs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(key, str) or not key or key not in fields for key in pair)
            or pair[0] == pair[1]
            or used.intersection(pair)
        ):
            raise ValueError("JSON empty-array pairs require disjoint declared fields")
        used.update(pair)


def complete_final_json(
    raw: str,
    *,
    fields: tuple[str, ...] = (),
    empty_array_pairs: tuple[tuple[str, str], ...] = (),
) -> str:
    """Return the final whole object, rejecting an unfinished object at any depth.

    A draft followed by a complete revision is allowed. An unfinished revision
    never falls back to the draft, and an inner object cannot stand in for its
    unfinished parent. With declared fields only, a complete comma-separated
    object sequence occupying the entire response can supply disjoint fields.
    Normalize its punctuation without dropping or overwriting values. An opted-in
    pair may add a missing empty array only beside an explicitly empty string.
    """
    validate_empty_array_pairs(fields, empty_array_pairs)
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object) if fields else json.JSONDecoder()
    pieces, found, cursor, sequence_start = _scan_object_sequence(raw, decoder)
    if found is None or "}" in raw[cursor:]:
        raise ValueError("no complete final JSON decision")
    if fields:
        return _declared_fields_object(
            raw, pieces, found, cursor, sequence_start, fields, empty_array_pairs
        )
    return found


def _scan_object_sequence(
    raw: str, decoder: json.JSONDecoder
) -> tuple[list[dict[str, object]], str | None, int, int]:
    cursor = 0
    found = None
    pieces: list[dict[str, object]] = []
    sequence_start = 0
    while (start := raw.find("{", cursor)) >= 0:
        try:
            value, end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON decision or revision is incomplete") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON decision must be an object")
        if not pieces or raw[cursor:start].strip() != ",":
            pieces = []
            sequence_start = start
        pieces.append(value)
        found = raw[start:end]
        cursor = end
    return pieces, found, cursor, sequence_start


def _merged_sequence(
    raw: str, pieces: list[dict[str, object]], cursor: int, sequence_start: int
) -> dict[str, object]:
    if raw[:sequence_start].strip() or raw[cursor:].strip():
        raise ValueError("JSON field sequence must occupy the complete response")
    return _unique_object([item for piece in pieces for item in piece.items()])


def _fill_empty_arrays(
    value: dict[str, object],
    fields: tuple[str, ...],
    empty_array_pairs: tuple[tuple[str, str], ...],
) -> bool:
    if set(value) - set(fields):
        raise ValueError("final JSON decision does not contain exactly the requested fields")
    added = False
    for claim, anchors in sorted(empty_array_pairs):
        if anchors not in value and value.get(claim) == "":
            value[anchors] = []
            added = True
    return added


def _declared_fields_object(
    raw: str,
    pieces: list[dict[str, object]],
    found: str,
    cursor: int,
    sequence_start: int,
    fields: tuple[str, ...],
    empty_array_pairs: tuple[tuple[str, str], ...],
) -> str:
    value = pieces[-1]
    if len(pieces) > 1:
        value = _merged_sequence(raw, pieces, cursor, sequence_start)
    added = False
    if empty_array_pairs:
        added = _fill_empty_arrays(value, fields, empty_array_pairs)
    if set(value) != set(fields):
        raise ValueError("final JSON decision does not contain exactly the requested fields")
    if len(pieces) > 1 or added:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return found
