"""Conserved inputs and replaceable effects of the structure planner."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_attached_outcomes import AttachedOutcomeReplay
from immich_memories.analysis.editorial_case import Case
from immich_memories.analysis.editorial_contracts import InsightEvidence
from immich_memories.analysis.editorial_intent import EditorialIntent, build_editorial_intent
from immich_memories.analysis.editorial_motion_outcomes import MotionOutcomeReplay
from immich_memories.api.models import Asset
from immich_memories.config_loader import Config
from immich_memories.processing.editorial_timing import EditorialTimingPolicy
from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_final_attached import AttachedMaterialEvidence


class StructureJudge(Protocol):
    calls: list[dict[str, Any]]

    def ask(
        self,
        stage: str,
        prompt: str,
        max_tokens: int = 260,
        *,
        json_object: bool = False,
        json_fields: tuple[str, ...] = (),
        json_empty_array_pairs: tuple[tuple[str, str], ...] = (),
    ) -> str: ...


def _check_render_timing(render_timing: EditorialTimingPolicy | None, case: Case) -> None:
    if render_timing is not None and (
        not isinstance(render_timing, EditorialTimingPolicy)
        or render_timing.target_seconds != case.target_seconds
        or render_timing.memory_type != case.product
    ):
        raise ValueError("Rendering policy disagrees with the captured memory")


def _check_wall_membership(
    wall_bytes: bytes,
    moment_asset_ids: Mapping[str, tuple[str, ...]],
    assets: Mapping[str, Asset],
) -> list[str]:
    from immich_memories.analysis.editorial_wall_rows import _read_wall_index

    aliases, _ = _read_wall_index(wall_bytes)
    if tuple(moment_asset_ids) != aliases:
        raise ValueError("captured asset membership must follow the complete wall alias order")
    members = list(chain.from_iterable(moment_asset_ids.values()))
    if any(not ids for ids in moment_asset_ids.values()) or len(members) != len(set(members)):
        raise ValueError("captured moments need unique, nonempty selectable membership")
    if not set(members).issubset(assets):
        raise ValueError("captured wall selects assets absent from its source evidence")
    return members


def _check_companions(companion_assets: Mapping[str, Asset], assets: Mapping[str, Asset]) -> None:
    linked = {asset.live_photo_video_id for asset in assets.values() if asset.is_live_photo}
    if any(
        key not in linked or key != asset.id or not asset.is_video
        for key, asset in companion_assets.items()
    ):
        raise ValueError("captured companion metadata must describe a declared attached video")


def _check_case_scope(case: Case, assets: Mapping[str, Asset], members: list[str]) -> None:
    if case.person_expression is not None:
        from immich_memories.analysis.editorial_source import filter_named_expression

        matching = {
            asset.id
            for asset in filter_named_expression(tuple(assets.values()), case.person_expression)
        }
        if not set(members).issubset(matching):
            raise ValueError("captured wall selects outside the grouped people condition")
    if case.special_event_id is not None and not set(assets).issubset(case.event_asset_ids):
        raise ValueError("captured source exceeds exact special event membership")


def _check_contract(case: Case, intent: EditorialIntent) -> None:
    if case.product != intent.product:
        raise ValueError("captured product and editorial contract disagree")
    if case.product == "special_day":
        expected = build_editorial_intent(
            case.product,
            case.ranges,
            brief=case.brief,
            people=case.people,
            event_admission=case.event_admission,
        )
        if intent.prompt_block() != expected.prompt_block():
            raise ValueError("special event admission and editorial contract disagree")


@dataclass(frozen=True)
class StructurePlanningInput:
    """All evidence is captured before planning; source IDs never enter model prompts."""

    case: Case
    intent: EditorialIntent
    config: Config
    wall_bytes: bytes
    moment_asset_ids: Mapping[str, tuple[str, ...]]
    assets: Mapping[str, Asset]
    annotations: Mapping[str, str]
    gps: Mapping[str, tuple[float, float]]
    pixel_facts: Mapping[str, tuple[float, float]]
    shareability_flags: Mapping[str, tuple[Any, ...]]
    motion_residuals: Mapping[str, dict[str, Any]]
    lineage: Mapping[str, Any]
    bank_dir: Path
    artifact_dir: Path
    motion_outcome_replay: MotionOutcomeReplay | None = None
    prior_plan: Mapping[str, Any] | None = None
    prior_plan_ref: Path | None = None
    allow_live_motion: bool = True
    audience: str = (
        "family"  # a home video is for the household; "sendable" is the explicit stricter export
    )
    audience_annotations: Mapping[str, AssetAnnotationLine] = field(default_factory=dict)
    # Canonical support is private context, separate from the prompt-serialized reading.
    # A cited source need not be selectable in the current request.
    period_evidence: tuple[InsightEvidence, ...] = ()
    # Attached evidence is separate from selectable primaries and canonical context.
    companion_assets: Mapping[str, Asset] = field(default_factory=dict)
    attached_outcome_replay: AttachedOutcomeReplay | None = None
    render_timing: EditorialTimingPolicy | None = None

    def __post_init__(self) -> None:
        _check_render_timing(self.render_timing, self.case)
        members = _check_wall_membership(self.wall_bytes, self.moment_asset_ids, self.assets)
        _check_companions(self.companion_assets, self.assets)
        _check_case_scope(self.case, self.assets, members)
        _check_contract(self.case, self.intent)
        if not isinstance(self.allow_live_motion, bool):
            raise ValueError("Live motion request must be boolean")
        if self.audience not in {"sendable", "family"}:
            raise ValueError("unknown structure export audience")


@dataclass(frozen=True)
class StructurePlannerPorts:
    judge: StructureJudge
    thumbnail_hash: Callable[[str], str | None]
    rank: Callable[[str, tuple[str, ...]], Mapping[int, float]]
    reranker_identity: Mapping[str, str]
    resolve_motion: (
        Callable[[list[dict[str, Any]]], tuple[list[dict[str, Any]], dict[str, Any]]] | None
    ) = None
    thumbnail_metrics: Callable[[], Mapping[str, Any]] | None = None
    observe_picture: Callable[[str], Mapping[str, Any]] | None = None
    picture_facts_metrics: Callable[[], Mapping[str, Any]] | None = None
    observe_story_motion: Callable[[Mapping[str, Any]], str] | None = None
    story_motion_metrics: Callable[[], Mapping[str, Any]] | None = None
    confirm_sampled_pairs: (
        Callable[
            [tuple[tuple[str, str], ...], Mapping[str, Mapping[str, Any]]],
            tuple[Any, Mapping[str, Any]],
        ]
        | None
    ) = None
    sampled_pair_metrics: Callable[[], Mapping[str, Any]] | None = None
    sampled_preview_hashes: (
        Callable[[tuple[str, ...], Mapping[str, Mapping[str, Any]]], Mapping[str, str]] | None
    ) = None
    observe_attached_material: Callable[[list[dict[str, Any]]], AttachedMaterialEvidence] | None = (
        None
    )
    attached_material_metrics: Callable[[], Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class StructurePlanningResult:
    plan: dict[str, Any]
    contract: str
    selection_sheet: str
    summary: dict[str, Any]

    def write(self, output: Path) -> None:
        """Keep the same private artifacts for product runs and sealed evaluations."""
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_secret_file(
            output / "plan.private.json", json.dumps(self.plan, indent=2, default=str)
        )
        write_secret_file(output / "contract.private.txt", self.contract)
        write_secret_file(output / "selection-sheet.private.md", self.selection_sheet)
