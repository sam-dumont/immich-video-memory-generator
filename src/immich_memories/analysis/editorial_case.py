"""The editorial case and text-request contracts the story-first planner and runtime share.

Extracted from the retired post-card moment editor; only the contracts survive here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_moment_contract import Moment, MomentCard
from immich_memories.analysis.llm_providers import resolved_llm_config
from immich_memories.analysis.llm_text_identity import COMPLETE_RETRY_POLICY, text_judgment_key
from immich_memories.analysis.moment_cards import MomentCard as ProductionMomentCard
from immich_memories.analysis.special_event_scope import SpecialEventAdmission
from immich_memories.api.person_expression import PersonExpression
from immich_memories.timeperiod import DateRange


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    product: str
    ranges: tuple[DateRange, ...]
    target_seconds: float
    brief: str
    target_source: str = "manifest"
    people: tuple[str, ...] = ()
    person_match: str = "and"
    accept_any_provenance: bool = False
    trip: bool = False
    album_ref: str | None = None
    special_event_id: str | None = None
    event_asset_ids: tuple[str, ...] = ()
    event_admission: SpecialEventAdmission | None = None
    person_expression: PersonExpression | None = None

    def __post_init__(self) -> None:
        from immich_memories.analysis.special_event_scope import validate_special_event_scope

        if self.person_expression is not None:
            if not isinstance(self.person_expression, PersonExpression):
                raise ValueError("case people condition must be a validated expression")
            if self.people and set(self.people) != set(self.person_expression.leaf_values):
                raise ValueError("case people names and grouped condition disagree")
            object.__setattr__(self, "people", self.person_expression.leaf_values)
        members = validate_special_event_scope(
            self.special_event_id, self.event_asset_ids, product=self.product
        )
        object.__setattr__(self, "event_asset_ids", members)
        if self.event_admission is not None:
            if not isinstance(self.event_admission, SpecialEventAdmission):
                raise ValueError("special event admission must be an explicit validated record")
            self.event_admission.validate_scope(
                self.special_event_id, members, product=self.product
            )


@dataclass(frozen=True)
class TextCall:
    prompt: str
    raw: str
    wall_seconds: float
    cache_hit: bool
    thinking: bool
    warning: str | None = None


@dataclass(frozen=True)
class TextRequest:
    prompt: str
    llm_config: Any
    cache_path: Path
    max_tokens: int
    timeout_seconds: int
    thinking: bool = False
    json_object: bool = False
    refresh: bool = False
    json_fields: tuple[str, ...] = ()
    json_empty_array_pairs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        from immich_memories.analysis.editorial_json_completion import validate_empty_array_pairs

        if self.json_fields and not self.json_object:
            raise ValueError("JSON fields require the JSON response contract")
        validate_empty_array_pairs(self.json_fields, self.json_empty_array_pairs)

    @property
    def judgment_key(self) -> str:
        from immich_memories.analysis.editorial_json_completion import (
            JSON_EMPTY_ARRAY_POLICY,
            JSON_FIELDS_POLICY,
            JSON_RECOVERY_POLICY,
        )

        return text_judgment_key(
            resolved_llm_config(self.llm_config),
            self.prompt,
            thinking=self.thinking,
            max_tokens=self.max_tokens,
            temperature=0.0,
            require_complete=True,
            policy=COMPLETE_RETRY_POLICY
            + (f"/{JSON_RECOVERY_POLICY}" if self.json_object else "")
            + (
                f"/{JSON_FIELDS_POLICY}:" + json.dumps(sorted(set(self.json_fields)))
                if self.json_fields
                else ""
            )
            + (
                f"/{JSON_EMPTY_ARRAY_POLICY}:" + json.dumps(sorted(self.json_empty_array_pairs))
                if self.json_empty_array_pairs
                else ""
            ),
        )


def _adapt_production_cards(
    prepared: Any,
    cards: tuple[ProductionMomentCard, ...],
) -> tuple[tuple[MomentCard, ...], dict[str, tuple[str, ...]]]:
    """Join scope-stable production cards back to their canonical source groups."""
    groups_by_id = {group.group_id: group for group in prepared.moment_groups}
    if len(groups_by_id) != len(prepared.moment_groups):
        raise ValueError("prepared source contains duplicate moment group IDs")
    if len({card.moment_id for card in cards}) != len(cards):
        raise ValueError("production card wall contains duplicate moment IDs")

    adapted: list[MomentCard] = []
    selectable_by_group: dict[str, tuple[str, ...]] = {}
    for index, card in enumerate(cards, start=1):
        group = groups_by_id.get(card.moment_id)
        if group is None:
            raise ValueError("production card does not belong to the prepared moment groups")
        if card.full_asset_ids != group.candidate_ids:
            raise ValueError("production card full membership differs from its canonical moment")
        selectable = card.selectable_asset_ids
        if not selectable or len(set(selectable)) != len(selectable):
            raise ValueError("production card needs unique selectable members")
        selectable_set = set(selectable)
        canonical_selectable = tuple(
            asset_id for asset_id in group.candidate_ids if asset_id in selectable_set
        )
        if selectable != canonical_selectable:
            raise ValueError("production card selectable members are not in canonical order")

        moment = Moment(alias=f"M{index:03d}", group=group, descriptions=())
        adapted.append(MomentCard(moment, card.text, None))
        selectable_by_group[group.group_id] = selectable
    return tuple(adapted), selectable_by_group
