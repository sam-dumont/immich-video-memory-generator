"""Small shared decoder for complete, non-repaired model JSON envelopes."""

from __future__ import annotations

import json
from typing import Any, TypeGuard


def is_safe_model_text(value: object, *, max_chars: int) -> TypeGuard[str]:
    """Whether model prose has the exact bounded no-escaping wire alphabet."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= max_chars
        and all(32 <= ord(character) <= 126 and character not in {'"', "\\"} for character in value)
    )


def bounded_model_text(value: object, *, max_chars: int) -> str | None:
    """Model prose fitted to its bound, or None when it is not usable text.

    Length is the one property worth coercing. Everything else here says
    something about whether the text can be trusted or displayed at all, but a
    reason that runs nine characters long still says what it meant, and the
    decision it explains is not improved by discarding it. Measured on the same
    images twice: 91/84/76 characters one run, 103/105/104 the next.
    """
    if not isinstance(value, str):
        return None
    fitted = value.strip()[:max_chars].strip()
    return fitted if is_safe_model_text(fitted, max_chars=max_chars) else None


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
