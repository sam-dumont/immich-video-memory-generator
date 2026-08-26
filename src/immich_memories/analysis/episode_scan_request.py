"""Provider-visible request for fused episode reading, record, and Cull namespaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from immich_memories.analysis.contact_sheets import TileRef
from immich_memories.analysis.editorial_contracts import (
    RECORD_SHOT_FUNCTION_MAX_CHARS,
    RECORD_SHOT_REASON_MAX_CHARS,
    EditorialCandidate,
)
from immich_memories.analysis.editorial_gateway import VisualEditorialRequest
from immich_memories.analysis.period_insight_answer import (
    EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
    EPISODE_SCAN_SCHEMA_VERSION,
    EPISODE_VISUAL_SUMMARY_MAX_CHARS,
)
from immich_memories.analysis.subject_policy import subject_evidence
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

if TYPE_CHECKING:
    from immich_memories.analysis.period_insight import EpisodeScanPack

EPISODE_SCAN_PASS_VERSION = "episode-scan-v3"  # noqa: S105 - editorial pass identity
EPISODE_SCAN_PROMPT_VERSION = "episode-scan-prompt-v3"
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1"


def build_episode_request(
    pack: EpisodeScanPack,
    *,
    limits: VisionRequestLimits,
) -> VisualEditorialRequest:
    """Build the one physical request shared by Pass 0 and Pass 1."""
    return VisualEditorialRequest(
        pass_name="episode-scan",  # noqa: S106 - versioned editorial pass identity
        pass_version=EPISODE_SCAN_PASS_VERSION,
        prompt=_episode_prompt(pack),
        prompt_version=EPISODE_SCAN_PROMPT_VERSION,
        schema_version=EPISODE_SCAN_SCHEMA_VERSION,
        pages=(pack.page,),
        ordered_input_ids=tuple(ref.entity_id for ref in pack.page.tile_refs),
        ordered_group_ids=tuple(scope.episode_id for scope in pack.scopes),
        grounded_annotations=_episode_annotations(pack),
        upstream_material=(),
        render_version=_RENDER_VERSION,
        limits=limits,
        continuation_number=pack.continuation_number,
        continuation_count=pack.continuation_count,
    )


def _episode_prompt(pack: EpisodeScanPack) -> str:
    scopes = "\n".join(
        f"episode={scope.episode_alias} page={scope.page_alias} "
        f"tiles=[{','.join(str(ref.number) for ref in scope.tile_refs)}]"
        for scope in pack.scopes
    )
    return (
        "Read every numbered visual on this chronological episode pack. The explicit tile map "
        "below preserves complete episodes even when their members interleave in time:\n"
        + scopes
        + "\nReturn only one complete "
        f'JSON object with schema_version="{EPISODE_SCAN_SCHEMA_VERSION}", pack=1, and one '
        "episode_readings entry for every mapped episode. Each entry needs the integer episode "
        "and page aliases, visual_summary (at most "
        f"{EPISODE_VISUAL_SUMMARY_MAX_CHARS} characters), representative_tiles from that episode, "
        "and a representative_reason grounded in the visible pixels (at most "
        f"{EPISODE_REPRESENTATIVE_REASON_MAX_CHARS} characters). Representatives make a later "
        "wall legible; they reject nothing. First return record_shots for visuals that function "
        "as evidence, proof, a ticket, a sign, a document, or another necessary factual record. "
        "Each entry needs only the pack-local integer tile, function (at most "
        f"{RECORD_SHOT_FUNCTION_MAX_CHARS} characters), and visible reason (at most "
        f"{RECORD_SHOT_REASON_MAX_CHARS} characters). "
        "Then return cull_rejects only for clearly unusable non-record visuals. Each entry needs "
        "only the pack-local integer tile, a defect code from accidental_capture, "
        "unusable_motion_blur, unusable_exposure, or corrupt_or_obscured_pixels, and a reason "
        "grounded in the pixels. Subject, repetition, relative weakness, duration, resolution, "
        "similarity, and thesis relevance are never Cull reasons. Return both arrays even when empty."
    )


def _episode_annotations(pack: EpisodeScanPack) -> tuple[str, ...]:
    candidate_by_id = {
        candidate.asset_id: candidate for scope in pack.scopes for candidate in scope.candidates
    }
    episode_alias_by_id = {
        ref.entity_id: scope.episode_alias for scope in pack.scopes for ref in scope.tile_refs
    }
    return tuple(
        _candidate_annotation(
            ref,
            candidate_by_id[ref.entity_id],
            episode_alias_by_id[ref.entity_id],
        )
        for ref in pack.page.tile_refs
    )


def _candidate_annotation(
    ref: TileRef,
    candidate: EditorialCandidate,
    episode_alias: int,
) -> str:
    category = next(
        (
            annotation.removeprefix("subject:")
            for annotation in candidate.grounded_annotations
            if annotation.startswith("subject:")
        ),
        None,
    )
    return " | ".join(
        (
            f"tile:{ref.number}",
            f"episode:{episode_alias}",
            f"taken:{candidate.taken_at.isoformat()}",
            f"media:{candidate.media_kind}",
            f"favourite:{str(candidate.favourite).lower()}",
            subject_evidence(
                tagged_people=len(candidate.source.people or ()),
                category=category,
            ),
            *candidate.grounded_annotations,
        )
    )
