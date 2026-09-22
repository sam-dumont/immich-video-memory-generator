"""Closed-vocabulary specifications for the active v1 triage heads.

``venue`` is intentionally absent.  It was dropped by the owner after the
architecture draft and must fail loudly instead of returning as an accidental
fifth field.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

HEAD_SET_VERSION = "triage-active-heads-v1"
VENUE_HEAD = "venue"


@dataclass(frozen=True)
class HeadSpec:
    """One independently versioned, single-label triage fact."""

    name: str
    field: str
    classes: tuple[str, ...]
    escape_class: str

    def __post_init__(self) -> None:
        if not self.name or not self.field:
            raise ValueError("head name and schema field must be non-empty")
        if len(self.classes) < 2 or len(set(self.classes)) != len(self.classes):
            raise ValueError(f"head {self.name!r} must have unique classes")
        if self.escape_class not in self.classes:
            raise ValueError(f"head {self.name!r} escape must be one of its classes")

    @property
    def decidable_classes(self) -> tuple[str, ...]:
        return tuple(label for label in self.classes if label != self.escape_class)

    def json_schema(self) -> dict[str, Any]:
        """Keep the costless escape in the grammar, not merely in prose."""
        return {
            "anyOf": [
                {"enum": list(self.decidable_classes)},
                {"const": self.escape_class},
            ]
        }


LOCATION = HeadSpec(
    name="location",
    field="visible_location",
    classes=("indoor", "outdoor", "undetermined"),
    escape_class="undetermined",
)
PEOPLE = HeadSpec(
    name="people",
    field="visible_people",
    classes=("none", "one", "two", "small-group", "crowd", "undetermined"),
    escape_class="undetermined",
)
CHILDREN = HeadSpec(
    name="children",
    field="visible_children",
    classes=("yes", "no", "undetermined"),
    escape_class="undetermined",
)
ACTIVITY = HeadSpec(
    name="activity",
    field="visible_activity",
    classes=(
        "eating-drinking",
        "sport-active",
        "performing",
        "sightseeing",
        "playing",
        "celebration",
        "posing",
        "working",
        "animal-nature",
        "other",
    ),
    escape_class="other",
)


class OffVocabularyError(ValueError):
    """The teacher answered with a word outside a head's closed vocabulary."""

    def __init__(self, spec: HeadSpec, value: Any) -> None:
        super().__init__(f"teacher returned invalid {spec.name} class")
        self.spec = spec
        self.value = value


ACTIVE_HEAD_SPECS: tuple[HeadSpec, ...] = (LOCATION, PEOPLE, CHILDREN, ACTIVITY)
ACTIVE_HEAD_NAMES: tuple[str, ...] = tuple(spec.name for spec in ACTIVE_HEAD_SPECS)
_BY_NAME = {spec.name: spec for spec in ACTIVE_HEAD_SPECS}


def resolve_head_specs(names: Iterable[str] | None = None) -> tuple[HeadSpec, ...]:
    """Resolve a stable ordered subset, rejecting the retired venue head."""
    if names is None:
        return ACTIVE_HEAD_SPECS
    requested = tuple(str(name) for name in names)
    if not requested:
        raise ValueError("at least one active triage head is required")
    if len(set(requested)) != len(requested):
        raise ValueError("triage head names must be unique")
    if VENUE_HEAD in requested:
        raise ValueError("venue was dropped by owner decision and is not an active v1 head")
    unknown = sorted(set(requested) - set(_BY_NAME))
    if unknown:
        raise ValueError(f"unknown triage heads: {', '.join(unknown)}")
    wanted = set(requested)
    return tuple(spec for spec in ACTIVE_HEAD_SPECS if spec.name in wanted)


def labels_schema(specs: Sequence[HeadSpec] = ACTIVE_HEAD_SPECS) -> dict[str, Any]:
    """Return the strict response envelope for one or more active heads."""
    resolved = resolve_head_specs(spec.name for spec in specs)
    if tuple(specs) != resolved:
        raise ValueError("head specs must use canonical active-v1 order and definitions")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [spec.field for spec in resolved],
        "properties": {spec.field: spec.json_schema() for spec in resolved},
    }


def parse_labels(payload: Any, specs: Sequence[HeadSpec] = ACTIVE_HEAD_SPECS) -> dict[str, str]:
    """Validate and normalize a constrained response to head-name keys."""
    resolved = resolve_head_specs(spec.name for spec in specs)
    if tuple(specs) != resolved:
        raise ValueError("head specs must use canonical active-v1 order and definitions")
    if not isinstance(payload, dict):
        raise ValueError("teacher response must be a JSON object")
    expected_fields = {spec.field for spec in resolved}
    if set(payload) != expected_fields:
        raise ValueError("teacher response does not match the active-head envelope")
    labels: dict[str, str] = {}
    for spec in resolved:
        value = payload[spec.field]
        if not isinstance(value, str) or value not in spec.classes:
            raise OffVocabularyError(spec, value)
        labels[spec.name] = value
    return labels
