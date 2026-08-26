"""Small shared decoder for complete, non-repaired model JSON envelopes."""

from __future__ import annotations

import json
from typing import Any


def final_json_object(raw: str) -> dict[str, Any] | None:
    """Return one complete final object, allowing only a trailing Markdown fence."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and raw[end:].strip() in ("", "```"):
            return value
    return None
