"""Small shared decoder for complete, non-repaired model JSON envelopes."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, TypeGuard

# A curly double quote standing exactly where a JSON string terminator belongs:
# nothing but whitespace separates it from the next comma or closing bracket.
_TYPOGRAPHIC_TERMINATOR = re.compile(r"[“”](?=\s*[,}\]])")


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


def final_json_object(
    raw: str, *, allow_trailing_commentary: bool = False
) -> dict[str, Any] | None:
    """Return one complete final object, allowing only a trailing Markdown fence.

    A model instructed to keep double quotes out of its prose can keep that
    promise one character too far and close a string value with a typographic
    quote. Measured 2026-09-02 on the 30B: a single U+201D where the
    terminator belonged, the decoder then ran into the pretty printer's
    newline, and the whole chapter cut was voided — `finish_reason=stop` at
    1111 of 4000 tokens, and the repair ask reproduced it byte-for-byte at
    temperature 0, so re-asking recovers nothing. Only a curly quote in
    terminator position is rewritten, and only a clean re-parse is accepted;
    anything else still fails closed.
    """
    found = _first_complete_object(raw, allow_trailing_commentary=allow_trailing_commentary)
    if found is not None:
        return found
    repaired = _TYPOGRAPHIC_TERMINATOR.sub('"', raw)
    return (
        _first_complete_object(repaired, allow_trailing_commentary=allow_trailing_commentary)
        if repaired != raw
        else None
    )


def _first_complete_object(
    raw: str, *, allow_trailing_commentary: bool = False
) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            continue
        trailing = raw[end:].strip()
        plain_commentary = allow_trailing_commentary and not any(c in trailing for c in "{}")
        if isinstance(value, dict) and (trailing in ("", "```") or plain_commentary):
            return value
    return None


def model_text_rows(value: object) -> list[Any] | None:
    """The rows of a list-of-strings field, reading the bare string a model answers with.

    Measured 2026-09-14 on a local 35B reading the demo month: both period answers
    wrote `tensions` and `recurring_threads` as one sentence instead of a one-item
    list, and the repair ask that named the shape got the same shape back. Nothing
    is ambiguous about one sentence where a list of sentences belongs, so the
    structure carries the rule the instruction could not: one string is one row, an
    empty string is no rows. Anything that is not a list or a string keeps its shape
    and fails where it always did — this widens the container, never the contents.
    """
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value if isinstance(value, list) else None


_NAMED_KEYS = 12
_NAMED_KEY_CHARS = 80


def named_keys(keys: Iterable[str]) -> str:
    """The keys a repair request names, as a JSON list: sorted, at most twelve, each cut to
    eighty characters, with a count of the rest. A reply that invents two hundred keys must
    not turn the repair request into a copy of itself."""
    ordered = sorted(set(keys))
    shown = json.dumps(
        [key[:_NAMED_KEY_CHARS] for key in ordered[:_NAMED_KEYS]], ensure_ascii=False
    )
    rest = len(ordered) - _NAMED_KEYS
    return shown + (f" and {rest} more" if rest > 0 else "")
