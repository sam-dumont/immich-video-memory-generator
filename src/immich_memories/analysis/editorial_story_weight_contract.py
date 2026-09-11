"""Validate complete story decisions before any allocation or editorial mutation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

from immich_memories.analysis.editorial_story_weight_audit import weight_reply_audit

WEIGHING_CONTRACT_VERSION = "complete-story-weights-v1"
NONCENTRAL_WEIGHTS = {"major", "minor", "glimpse", "none"}


def _repair_guidance(audit: Mapping, *, candidates: Sequence[str]) -> str:
    """Translate validation findings into corrections without choosing new weights."""
    lines = []
    if audit["ignored_about_entries"]:
        lines.extend(
            [
                'Invalid "about" entries: '
                + json.dumps(audit["ignored_about_entries"])
                + ". These are not eligible central-story candidates, even if they occur in the table.",
                'The only eligible keys for "about" are: '
                + json.dumps(list(candidates))
                + '. Choose at most two of these, or use []. Give every story outside "about" its own weight.',
            ]
        )
        if not candidates:
            lines.append(
                "No central candidates were offered. The corrected object must contain "
                '"about": []. All offered stories still need weights; none can enter about.'
            )
    if audit["ignored_weight_keys"]:
        lines.append(
            "Invalid weight labels for: "
            + json.dumps(audit["ignored_weight_keys"])
            + '. Reassess each using only "major", "minor", "glimpse", or "none". '
            "The memory-worthy reading labels in the input are evidence, not output weights."
        )
    if audit["unknown_weight_keys"]:
        lines.append(
            "Remove unknown weight keys: "
            + json.dumps(audit["unknown_weight_keys"])
            + ". They are not stories in the supplied table."
        )
    if audit["missing_weight_keys"]:
        lines.append(
            "Stories without a valid decision: "
            + json.dumps(audit["missing_weight_keys"])
            + ". Assess each one; do not inherit a decision from the rejected answer."
        )
    lines.extend(audit.get("invalid_fields", []))
    if any(str(error).startswith("join must") for error in audit.get("invalid_fields", [])):
        lines.append(
            'Each join is a nested two-element JSON array: "join": [["key_a", "key_b"]]. '
            "Substitute two distinct keys actually offered above. A string such as "
            '"key_a,key_b" is not a pair. Use "join": [] if no valid joining is intended.'
        )
    return "\n".join(lines)


class StoryWeightDecisionError(ValueError):
    """A transported answer does not assess the offered story table completely."""

    def __init__(self, audit: dict) -> None:
        self.audit = audit
        details = {
            key: value
            for key, value in audit.items()
            if key not in {"coverage_complete", "shape_valid"} and value
        }
        if not audit["shape_valid"]:
            details["invalid_shape"] = "about must be a list and weights an object"
        super().__init__("incomplete story weighting: " + json.dumps(details, ensure_ascii=False))


def _accepted_weights(raw_weights: object, story_keys: Sequence[str]) -> dict[str, str]:
    return {
        key: weight.strip().lower()
        for key, weight in (raw_weights.items() if isinstance(raw_weights, Mapping) else ())
        if key in story_keys
        and isinstance(weight, str)
        and weight.strip().lower() in NONCENTRAL_WEIGHTS
    }


def _accepted_about(
    raw_about: object, story_keys: Sequence[str], candidates: Sequence[str]
) -> list[str]:
    return [
        key
        for key in (raw_about if isinstance(raw_about, list) else [])
        if isinstance(key, str) and key in candidates and key in story_keys
    ]


def _joins_are_pairs(joins: object, story_keys: Sequence[str]) -> bool:
    return isinstance(joins, list) and not any(
        not isinstance(pair, list)
        or len(pair) != 2
        or any(not isinstance(key, str) or key not in story_keys for key in pair)
        or pair[0] == pair[1]
        for pair in joins
    )


def _retitles_name_offered_stories(retitles: object, story_keys: Sequence[str]) -> bool:
    return isinstance(retitles, Mapping) and not any(
        key not in story_keys or not isinstance(title, str) or not title.strip()
        for key, title in retitles.items()
    )


def _invalid_fields(obj: Mapping, about: Sequence[str], story_keys: Sequence[str]) -> list[str]:
    errors = []
    if len(about) > 2 or len(set(about)) != len(about):
        errors.append("about must contain at most two distinct offered candidates")
    if not _joins_are_pairs(obj.get("join", []), story_keys):
        errors.append("join must be a list of pairs of distinct offered story keys")
    if not _retitles_name_offered_stories(obj.get("retitle", {}), story_keys):
        errors.append("retitle must map offered story keys to nonempty titles")
    return errors


def validate_weight_reply(
    value: object,
    *,
    story_keys: Sequence[str],
    candidates: Sequence[str],
    parse_failed: bool = False,
) -> tuple[dict, dict]:
    """A central story needs no separate weight; every other story does.

    Optional joining/retitling may be absent. If supplied, their references must
    belong to the same table. Existing day/group constraints are applied later.
    """
    obj = value if isinstance(value, Mapping) else {}
    weights = _accepted_weights(obj.get("weights"), story_keys)
    about = _accepted_about(obj.get("about"), story_keys, candidates)
    audit = weight_reply_audit(
        value,
        story_keys=story_keys,
        accepted_weights=weights,
        accepted_about=about,
        parse_failed=parse_failed,
    )
    joins, retitles = obj.get("join", []), obj.get("retitle", {})
    errors = _invalid_fields(obj, about, story_keys)
    audit["invalid_fields"] = errors
    audit["coverage_complete"] = audit["coverage_complete"] and not errors
    if not audit["coverage_complete"]:
        raise StoryWeightDecisionError(audit)
    return {"about": about, "weights": weights, "join": joins, "retitle": retitles}, audit


def ask_complete_weights(
    judge,
    *,
    stage: str,
    prompt: str,
    story_keys: Sequence[str],
    candidates: Sequence[str],
    parse: Callable[[str], object],
    record: Callable[[dict], None],
) -> tuple[dict, dict]:
    """Validate cached and fresh answers identically; repair at most once.

    The repair prompt includes the failure so it has its own exact judgment key.
    Neither an invalid initial reply nor an invalid repair can reach allocation.
    """
    request_prompt = prompt
    budget = max(1200, 300 + 24 * len(story_keys))
    for attempt in range(2):
        raw = judge.ask(stage + ("-repair" if attempt else ""), request_prompt, max_tokens=budget)
        parse_failed = False
        try:
            value = parse(raw)
        except (ValueError, TypeError):
            value, parse_failed = {}, True
        try:
            value, audit = validate_weight_reply(
                value,
                story_keys=story_keys,
                candidates=candidates,
                parse_failed=parse_failed,
            )
        except StoryWeightDecisionError as exc:
            record(
                {
                    "stage": stage + "-validation",
                    "attempt": attempt + 1,
                    "contract_version": WEIGHING_CONTRACT_VERSION,
                    "status": "invalid",
                    "judgment_audit": exc.audit,
                }
            )
            if attempt:
                raise
            request_prompt = prompt + (
                "\n\nPREVIOUS ANSWER REJECTED: "
                + str(exc)
                + "\n"
                + _repair_guidance(exc.audit, candidates=candidates)
                + "\nReturn a complete corrected decision for the entire story table above. "
                "Every key outside about needs its own weight; do not return a sample or only the missing entries.\n"
                'The output weight values are ONLY "major", "minor", "glimpse", "none". '
                'Never output the input reading labels "remarkable", "maybe", or "background" as weights. '
                "This rule applies to every weight, even when the previous error concerned about or join.\n"
            )
            continue
        record(
            {
                "stage": stage + "-validation",
                "attempt": attempt + 1,
                "contract_version": WEIGHING_CONTRACT_VERSION,
                "status": "complete",
                "judgment_audit": audit,
            }
        )
        return value, audit
    raise AssertionError("bounded story weighting must return or raise")
