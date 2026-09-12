"""Factual episode cards and an explicitly omitted thesis, never stored as model answers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_contracts import DecisionProvenance, PeriodInsight
from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
from immich_memories.analysis.text_episode_reader import (
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.analysis.text_period_insight import TextPeriodInsightResult
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeRepresentative,
)
from immich_memories.store.period_insights import (
    PeriodEpisodeGrounding,
    PeriodInsightIdentity,
    PeriodInsightProducer,
)

RULES_VERSION = "rules-v1"


class EpisodeReader(Protocol):
    @property
    def producer(self) -> EpisodeReadingProducer: ...

    def read(self, projections: Sequence[EditorialGroupProjection]) -> TextEpisodeReadResult: ...


class RuleEpisodeReader:
    def __init__(self, annotations: StoredAnnotationLineReader) -> None:
        self.annotations = annotations
        self.producer = EpisodeReadingProducer(
            RULES_VERSION,
            RULES_VERSION,
            RULES_VERSION,
            annotations.contract.renderer_version,
            annotations.contract.producer_versions,
        )

    def read(self, projections: Sequence[EditorialGroupProjection]) -> TextEpisodeReadResult:
        ids = tuple(dict.fromkeys(a for p in projections for a in p.group.candidate_ids))
        batch = self.annotations.lines_for(ids)
        lines = batch.as_mapping()
        episodes = []
        for projection in projections:
            group = projection.group
            identity = EpisodeReadingIdentity.from_annotations(
                group_id=group.group_id,
                producer_key=self.producer.key(),
                annotation_lines={a: lines[a] for a in group.candidate_ids},
            )
            representative = next((c for c in group.candidates if c.favourite), group.candidates[0])
            cities = tuple(
                dict.fromkeys(
                    c.source.exif_info.city
                    for c in group.candidates
                    if c.source.exif_info and c.source.exif_info.city
                )
            )
            account = f"{len(group.candidates)} captures on {group.candidates[0].taken_at:%Y-%m-%d}"
            if cities:
                account += ": " + ", ".join(cities[:3])
            reading = BankedEpisodeReading(
                identity,
                group.candidate_ids,
                account,
                (
                    EpisodeRepresentative(
                        representative.asset_id,
                        "Owner favourite" if representative.favourite else "First capture",
                    ),
                ),
                (),
            )
            episodes.append(EpisodeEditorialEvidence(projection, identity, reading, False, None))
        return TextEpisodeReadResult(tuple(episodes), batch, (), 0)


def rule_period(episodes: TextEpisodeReadResult) -> TextPeriodInsightResult:
    grounding = tuple(
        PeriodEpisodeGrounding(
            e.projection.group.group_id,
            e.reading.identity.evidence_key,
            e.reading.what_happened,
            tuple(r.asset_id for r in e.reading.representatives),
        )
        for e in episodes.episodes
        if e.reading is not None
    )
    producer = PeriodInsightProducer(RULES_VERSION, RULES_VERSION, RULES_VERSION)
    identity = PeriodInsightIdentity.from_grounding(producer_key=producer.key(), episodes=grounding)
    provenance = DecisionProvenance(
        "period",
        RULES_VERSION,
        RULES_VERSION,
        RULES_VERSION,
        episodes.annotation_batch.requested_asset_ids,
        (),
        identity.evidence_key,
        False,
    )
    return TextPeriodInsightResult(
        PeriodInsight(None, (), (), (), None, 0, provenance, reader="rules"),
        grounding,
        (),
        None,
        0,
        identity,
    )
