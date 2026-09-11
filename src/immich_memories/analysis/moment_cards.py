"""Deterministic full-moment cards over banked annotations and episode meaning."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_moment_wall import (
    MomentCardEvidence,
    RepresentativeEvidence,
)
from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
from immich_memories.analysis.text_episode_reader import (
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.store.episode_readings import EpisodeRepresentative

MAX_MOMENT_CARD_CHARS = 420
_MAX_CARD_REPRESENTATIVES = 2
_MAX_REPRESENTATIVE_LINE_CHARS = 72
_MAX_REPRESENTATIVE_REASON_CHARS = 36
_MAX_EPISODE_SUMMARY_CHARS = 96
_MAX_ANNOTATION_LINE_CHARS = 88


@dataclass(frozen=True)
class MomentCard:
    """One stable full-moment reading plus request-specific selectable members."""

    moment_id: str
    episode_id: str
    full_asset_ids: tuple[str, ...]
    selectable_asset_ids: tuple[str, ...]
    representative_asset_ids: tuple[str, ...]
    text: str
    evidence: MomentCardEvidence | None = None

    def __post_init__(self) -> None:
        members = set(self.full_asset_ids)
        if not self.full_asset_ids or len(members) != len(self.full_asset_ids):
            raise ValueError("moment card needs unique full membership")
        if not set(self.selectable_asset_ids).issubset(members):
            raise ValueError("selectable card members must belong to the full moment")
        if not set(self.representative_asset_ids).issubset(members):
            raise ValueError("card representatives must belong to the full moment")
        if not self.text.strip():
            raise ValueError("moment card text cannot be blank")


def build_moment_cards(
    projections: Sequence[EditorialGroupProjection],
    *,
    episodes: TextEpisodeReadResult,
) -> tuple[MomentCard, ...]:
    """Build scope-stable cards without another model call or evidence pre-trim."""
    lines = episodes.annotation_batch.as_mapping()
    records = episodes.annotation_batch.records_by_id()
    return tuple(_build_card(projection, episodes, lines, records) for projection in projections)


def _build_card(
    projection: EditorialGroupProjection,
    episodes: TextEpisodeReadResult,
    lines: Mapping[str, str],
    records: Mapping[str, AssetAnnotationLine],
) -> MomentCard:
    full_asset_ids = projection.group.candidate_ids
    missing = tuple(
        asset_id for asset_id in full_asset_ids if not str(lines.get(asset_id, "")).strip()
    )
    if missing:
        raise ValueError(f"moment card missing annotations for {len(missing)} full member(s)")
    episode = _episode_for(full_asset_ids, episodes.episodes)
    representatives = _representatives_for(projection, episode)
    representative_evidence = tuple(
        RepresentativeEvidence(
            description=_bounded(
                _representative_text(records[representative.asset_id]),
                _MAX_REPRESENTATIVE_LINE_CHARS,
            ),
            reason=_bounded(representative.reason, _MAX_REPRESENTATIVE_REASON_CHARS),
        )
        for representative in representatives[:_MAX_CARD_REPRESENTATIVES]
    )
    representative_text = "\n".join(
        "R\t"
        + json.dumps(representative.description, ensure_ascii=False)
        + "\t"
        + json.dumps(representative.reason, ensure_ascii=False)
        for representative in representative_evidence
    )
    episode_summary = _bounded(
        (
            episode.reading.what_happened
            if episode.reading is not None
            else "episode meaning unavailable"
        ),
        _MAX_EPISODE_SUMMARY_CHARS,
    )
    annotation_fields = _moment_annotation_fields(projection, records)
    annotations = tuple(f"{name}={value}" for name, value in annotation_fields)
    annotation_line = (
        f"\nA\t{_bounded_raw(chr(9).join(annotations), _MAX_ANNOTATION_LINE_CHARS)}"
        if annotations
        else ""
    )
    suffix = annotation_line + "\nE\t" + json.dumps(episode_summary, ensure_ascii=False)
    representative_budget = MAX_MOMENT_CARD_CHARS - len(suffix)
    text = _bounded_raw(representative_text, max(representative_budget, 1)) + suffix
    return MomentCard(
        moment_id=projection.group.group_id,
        episode_id=episode.projection.group.group_id,
        full_asset_ids=full_asset_ids,
        selectable_asset_ids=projection.scoped_candidate_ids,
        representative_asset_ids=tuple(item.asset_id for item in representatives),
        text=text,
        evidence=MomentCardEvidence(
            episode_meaning=episode_summary,
            representatives=representative_evidence,
            annotations=annotation_fields,
        ),
    )


def _episode_for(
    moment_asset_ids: tuple[str, ...],
    episodes: tuple[EpisodeEditorialEvidence, ...],
) -> EpisodeEditorialEvidence:
    moment_members = set(moment_asset_ids)
    carrying = tuple(
        episode
        for episode in episodes
        if moment_members.issubset(episode.projection.group.candidate_ids)
    )
    if len(carrying) != 1:
        raise ValueError("each full moment card must inherit exactly one canonical episode")
    return carrying[0]


def _representatives_for(
    projection: EditorialGroupProjection,
    episode: EpisodeEditorialEvidence,
) -> tuple[EpisodeRepresentative, ...]:
    members = set(projection.group.candidate_ids)
    banked = (
        []
        if episode.reading is None
        else [
            representative
            for representative in episode.reading.representatives
            if representative.asset_id in members
        ]
    )
    seen = {representative.asset_id for representative in banked}
    favourites = [
        EpisodeRepresentative(candidate.asset_id, "rule: protected favourite")
        for candidate in projection.group.candidates
        if candidate.favourite and candidate.asset_id not in seen
    ]
    combined = (*banked, *favourites)[:4]
    if combined:
        return combined
    return (
        EpisodeRepresentative(
            projection.group.candidates[0].asset_id,
            "rule fallback: first full-moment frame",
        ),
    )


def _representative_text(record: AssetAnnotationLine) -> str:
    return record.description or record.text


def _moment_annotation_fields(
    projection: EditorialGroupProjection,
    records: Mapping[str, AssetAnnotationLine],
) -> tuple[tuple[str, str], ...]:
    candidates = projection.group.candidates
    annotations: list[tuple[str, str]] = []
    for head in ("people", "children", "activity", "location"):
        votes = Counter(
            label
            for candidate in candidates
            if (label := dict(records[candidate.asset_id].heads).get(head)) is not None
            and label not in ("undetermined", "other")
        )
        if votes:
            annotations.append((head, votes.most_common(1)[0][0]))
    stitching_bursts = len(
        {
            record.stitching_burst_id
            for candidate in candidates
            if (record := records[candidate.asset_id]).stitching_burst_id is not None
        }
    )
    if stitching_bursts:
        annotations.append(("stitch", str(stitching_bursts)))
    return tuple(annotations)


def _bounded(value: str, max_chars: int) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _bounded_raw(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"
