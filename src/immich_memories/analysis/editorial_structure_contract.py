"""Conserved inputs and replaceable effects of the structure planner."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
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
from immich_memories.analysis.editorial_people import EditorialPeople
from immich_memories.api.models import Asset
from immich_memories.config_loader import Config
from immich_memories.processing.editorial_timing import EditorialTimingPolicy
from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_final_attached import AttachedMaterialEvidence
    from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
    from immich_memories.analysis.editorial_thin_layer import ThinPolish


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
        accepts: Callable[[str], bool] | None = None,
    ) -> str: ...

    def record_failure(self, stage: str, record: Mapping[str, Any]) -> None:
        """Keep a stage's exhausted recovery beside that stage's recorded calls."""


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
class EpisodeReadingCard:
    """One canonical episode's banked meaning, carried on every moment it covers.

    The wall row truncates the same meaning to 96 characters; this keeps the whole
    reading and the representatives it named, so the story read never re-reads captions.
    """

    episode_id: str
    evidence_key: str
    what_happened: str
    representative_asset_ids: tuple[str, ...]
    cache_hit: bool


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
    # The per-asset fact bank: where a cut's own measurements are read from and written back.
    store_path: Path | None = None
    # What a cut already measured of each clip's speech; a missing clip is not measured.
    speech_regions: Mapping[str, tuple[tuple[float, float], ...]] = field(default_factory=dict)
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
    # The banked 90-minute episode reading behind each moment alias; the story read pages
    # over these instead of over every caption of the period.
    episode_readings: Mapping[str, EpisodeReadingCard] = field(default_factory=dict)
    # Attached evidence is separate from selectable primaries and canonical context.
    companion_assets: Mapping[str, Asset] = field(default_factory=dict)
    # What the detectors banked about each attached clip. A clip has no annotation line --
    # nothing describes it and nothing selects it -- so the audience gate reads its heads
    # from the bank instead. Empty is what every clip carried before one was ever read.
    companion_detectors: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # The frame head's reading of each Live Photo clip (`clip_frames`): a clip that often
    # misses its subject plays as its still. Empty is what every clip carried before.
    clip_frames: Mapping[str, str] = field(default_factory=dict)
    attached_outcome_replay: AttachedOutcomeReplay | None = None
    # Owner ticks after a cut: admitted after the read, so no prompt or digest input changes.
    owner_required_asset_ids: tuple[str, ...] = ()
    render_timing: EditorialTimingPolicy | None = None
    # The people file's facts and links, so a film about people can tell who is close to them
    # rather than to the owner. None reads as it always did: every relation is the owner's.
    people: EditorialPeople | None = None

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
        if self.audience == "sendable" and not self.config.editorial.preparation.demands_models:
            # Refusing outright, rather than clearing what nothing looked at. The gate
            # may only tighten, and this tier has no detector evidence to tighten on.
            raise ValueError(
                "a sendable export needs the detector evidence the "
                f"{self.config.editorial.preparation.tier} preparation tier does not produce"
            )


@dataclass(frozen=True)
class StructurePlannerPorts:
    judge: StructureJudge
    thumbnail_hash: Callable[[str], str | None]
    resolve_motion: (
        Callable[[list[dict[str, Any]]], tuple[list[dict[str, Any]], dict[str, Any]]] | None
    ) = None
    resolve_speech: Callable[[list[dict]], list[dict]] | None = None
    thumbnail_metrics: Callable[[], Mapping[str, Any]] | None = None
    observe_picture: Callable[[str], Mapping[str, Any]] | None = None
    picture_facts_metrics: Callable[[], Mapping[str, Any]] | None = None
    observe_story_motion: Callable[[Mapping[str, Any]], str] | None = None
    story_motion_identity: str = ""
    story_motion_metrics: Callable[[], Mapping[str, Any]] | None = None
    observe_attached_material: Callable[[list[dict[str, Any]]], AttachedMaterialEvidence] | None = (
        None
    )
    attached_material_metrics: Callable[[], Mapping[str, Any]] | None = None
    rules: RuleStructureReader | None = None
    # Set when the model polishes a rules draft instead of planning the film: the reader above
    # builds the draft with no model and this reads the finished cut once.
    thin: ThinPolish | None = None
    # Measured Live companion clock offsets for content-aligned stitch joins
    # (#1012); None keeps the metadata plan. The draft never asks it: only the
    # bursts the cut keeps are measured, and production banks every answer.
    clock_offsets: Callable[[Sequence[str]], list[float | None] | None] | None = None
    # A frame's scene print (`editorial_scene_prints`), so the final review reads the scene a
    # cut repeats and not only the frame; None reads the cached hash alone.
    scene_print: Callable[[str], Any] | None = None


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
