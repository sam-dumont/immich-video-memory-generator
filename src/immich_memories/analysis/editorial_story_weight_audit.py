"""Describe missing weighting judgments without changing editorial decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _ignored_weights(
    weights: Mapping[str, object],
    story_keys: Sequence[str],
    accepted_weights: Mapping[str, str],
) -> list[str]:
    """Offered keys the parser saw but did not accept as their own weight."""
    return [
        key
        for key in weights
        if key in story_keys
        and (key not in accepted_weights or accepted_weights[key] == "dominant")
    ]


def _missing_weights(
    story_keys: Sequence[str],
    accepted_about: Sequence[str],
    accepted_weights: Mapping[str, str],
    ignored: Sequence[str],
) -> list[str]:
    return [
        key
        for key in story_keys
        if key not in accepted_about and (key not in accepted_weights or key in ignored)
    ]


def weight_reply_audit(
    value: object,
    *,
    story_keys: Sequence[str],
    accepted_weights: Mapping[str, str],
    accepted_about: Sequence[str],
    parse_failed: bool,
) -> dict:
    """Report coverage of the existing parser's answer, not its editorial quality."""
    obj = value if isinstance(value, Mapping) else {}
    raw_weights = obj.get("weights")
    raw_about = obj.get("about")
    weights = raw_weights if isinstance(raw_weights, Mapping) else {}
    about = raw_about if isinstance(raw_about, list) else []
    unknown_weights = [key for key in weights if key not in story_keys]
    ignored_weights = _ignored_weights(weights, story_keys, accepted_weights)
    ignored_about = [key for key in about if key not in accepted_about]
    missing_weights = _missing_weights(
        story_keys, accepted_about, accepted_weights, ignored_weights
    )
    shape_valid = isinstance(raw_weights, Mapping) and isinstance(raw_about, list)
    return {
        "parse_failed": parse_failed,
        "shape_valid": shape_valid,
        "missing_weight_keys": missing_weights,
        "ignored_weight_keys": ignored_weights,
        "unknown_weight_keys": unknown_weights,
        "ignored_about_entries": ignored_about,
        "coverage_complete": bool(
            not parse_failed
            and shape_valid
            and not (missing_weights or ignored_weights or unknown_weights or ignored_about)
        ),
    }
