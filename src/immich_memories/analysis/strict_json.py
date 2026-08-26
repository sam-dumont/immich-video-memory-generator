"""Small shared decoder for complete, non-repaired model JSON envelopes."""

from __future__ import annotations

import json
from typing import Any, TypeGuard


def is_safe_model_text(value: object, *, max_chars: int) -> TypeGuard[str]:
    """Whether model prose is bounded, single-line, and free of escaping hazards.

    The guard used to demand every character sit in 32..126, which threw away
    good decisions over punctuation the model produces on its own: measured
    verbatim, "the baby's most alert expression -- eyes wide" carries a curly
    apostrophe and an em dash, and one of those voided a whole episode reading.
    A Belgian library adds cafe, Noel and Liege to the same fate.

    What still earns its place is the quote, the backslash, the length and the
    single line. `json.loads` has already decoded this text and it reaches no
    shell and no SQL, so the letter range was protecting nothing. `isprintable`
    keeps out control characters, newlines and zero-width format characters
    without having an opinion about alphabets.
    """
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= max_chars
        and value.isprintable()
        and not any(character in {'"', "\\"} for character in value)
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
