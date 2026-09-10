"""Measured public caption contract used by the store-backed editorial slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

API_MODEL = "smolvlm2-500m-base-public"
DESCRIPTION_MODEL = f"{API_MODEL}@envelope-v3-compact"
SCHEMA_VERSION = "asset-description-v3-compact"
PROMPT_VERSION = "asset-description-prompt-v3-compact"
TILE_VERSION = "asset-description-tile-400px-jpeg-q90-v1"
DESCRIPTION_SOURCE = "public-envelope-v3-compact"
DESCRIPTION_MAX_CHARS = 120
SETTING_MAX_CHARS = 32
MAX_OUTPUT_TOKENS = 140
REPETITION_PENALTY = 1.1
SETTING_HEDGE = "insufficient evidence"

PROMPT = (
    "Return only JSON matching the schema. Describe only plainly visible facts in one "
    "concise sentence. Do not infer an event, purpose, exact place, or relationship. "
    "Use setting only for a short visible scene type; otherwise say insufficient evidence."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "maxLength": DESCRIPTION_MAX_CHARS},
        "setting": {"type": "string", "maxLength": SETTING_MAX_CHARS},
    },
    "required": ["description", "setting"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class DescriptionEnvelope:
    """The two literal facts accepted from one public-model response."""

    description: str
    setting: str


def validate_envelope(value: object) -> DescriptionEnvelope:
    """Accept the measured response shape and hedge unusable setting fragments."""
    if not isinstance(value, dict) or set(value) != {"description", "setting"}:
        raise ValueError("compact description response has the wrong keys")
    raw_setting = value["setting"]
    description = _one_line(value["description"])
    setting = _one_line(raw_setting)
    if not description or len(description) > DESCRIPTION_MAX_CHARS:
        raise ValueError("compact description is blank or over its wire cap")
    if len(setting) > SETTING_MAX_CHARS:
        raise ValueError("compact setting exceeds its wire cap")
    if not _setting_is_usable(setting, raw_chars=len(raw_setting)):
        setting = SETTING_HEDGE
    return DescriptionEnvelope(description=description, setting=setting)


def _one_line(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("compact description fields must be strings")
    return " ".join(value.split())


def _setting_is_usable(value: str, *, raw_chars: int) -> bool:
    if not value or raw_chars == SETTING_MAX_CHARS or not any(char.isalpha() for char in value):
        return False
    if value.endswith((",", ";", ":", "-")):
        return False
    lowered = value.casefold()
    return not lowered.startswith(
        ("the scene ", "this scene ", "the image ", "the photo ", "settings")
    )
