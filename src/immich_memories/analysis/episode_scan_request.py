"""Provider-visible request for fused episode reading, record, and Cull namespaces."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from immich_memories.analysis.contact_sheets import TileRef
from immich_memories.analysis.cull_answer import (
    CULL_SCOPE_WIRE_KEYS,
    DISCARDED_BUCKETS,
)
from immich_memories.analysis.editorial_contracts import (
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

EPISODE_SCAN_PASS_VERSION = "episode-scan-v4"  # noqa: S105 - editorial pass identity
EPISODE_SCAN_PROMPT_VERSION = "episode-scan-prompt-v4"
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1"


def episode_response_shape(*, episode_alias: int, tile: int) -> str:
    """One complete example envelope, built from the keys the parser demands.

    Both decision lists are shown EMPTY. Everything in an example is
    instruction, values included: shown a populated one, the model copied it
    onto unrelated visuals. An empty list is also the honest default.
    """
    return json.dumps(
        {
            "schema_version": EPISODE_SCAN_SCHEMA_VERSION,
            "pack": 1,
            "episode_readings": [
                {
                    "episode": episode_alias,
                    "page": 1,
                    "visual_summary": "what this episode shows",
                    "representative_tiles": [tile],
                    "representative_reason": "why these read best on a wall",
                }
            ],
            "cull_rejects": [
                dict.fromkeys((*CULL_SCOPE_WIRE_KEYS[1:], *DISCARDED_BUCKETS), [])
                | {"episode": episode_alias}
            ],
        },
        separators=(",", ":"),
    )


def build_episode_request(
    pack: EpisodeScanPack,
    *,
    limits: VisionRequestLimits,
) -> VisualEditorialRequest:
    """Build the one physical request shared by Pass 0 and Pass 1."""
    _validate_v3_one_page_pack(pack)
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


def _validate_v3_one_page_pack(pack: EpisodeScanPack) -> None:
    """Keep v3 aliases pack-local; multi-page requests require a schema/version bump."""
    page_refs = pack.page.tile_refs
    page_numbers = tuple(ref.number for ref in page_refs)
    scoped_refs = tuple(ref for scope in pack.scopes for ref in scope.tile_refs)
    if len(page_numbers) != len(set(page_numbers)):
        raise ValueError("episode-scan-v3 needs unique pack-local tile numbers")
    if any(scope.page_id != pack.page.sheet_id or scope.page_alias != 1 for scope in pack.scopes):
        raise ValueError("episode-scan-v3 supports exactly one physical page per request")
    if sorted(scoped_refs, key=lambda ref: ref.number) != sorted(
        page_refs, key=lambda ref: ref.number
    ):
        raise ValueError("episode-scan-v3 scopes must exactly partition the physical page")


def _episode_prompt(pack: EpisodeScanPack) -> str:
    # The example carries a tile this pack really has, so copying it verbatim
    # costs a wrong mark rather than voiding the namespace on an unknown alias.
    first_tile = pack.page.tile_refs[0].number
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
        "wall legible. All episode and record text must use printable ASCII except double quote "
        "and backslash; use one line with no control characters. Apostrophes and basic punctuation "
        "are allowed. "
        "Representatives reject nothing. Then, for EACH episode above, look only at that "
        "episode's own tiles and sort them into three lists. notes: taken as a note rather than "
        "as a memory, such as a screen, a document, or an object photographed to record what "
        "it is. failed: the picture did not come out, being obstructed, smeared, or "
        "unreadable. ordinary: nothing wrong with it, just unremarkable or one of several "
        "alike. Most tiles are ordinary, and empty notes and failed lists are the normal "
        "answer. Never list a tile "
        "that belongs to another episode. Use exactly these keys and no others:\n"
        + episode_response_shape(episode_alias=pack.scopes[0].episode_alias, tile=first_tile)
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
