"""Factual episode cards, never stored as model answers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from operator import attrgetter
from typing import Protocol

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_rule_quality import picture_facts, representative_key
from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
from immich_memories.analysis.text_episode_reader import (
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeRepresentative,
)

RULES_VERSION = "rules-v1"


class EpisodeReader(Protocol):
    @property
    def producer(self) -> EpisodeReadingProducer: ...

    def read(self, projections: Sequence[EditorialGroupProjection]) -> TextEpisodeReadResult: ...


class RuleEpisodeReader:
    def __init__(
        self, annotations: StoredAnnotationLineReader, *, by_quality: bool = False
    ) -> None:
        self.annotations = annotations
        self._by_quality = by_quality
        self.producer = EpisodeReadingProducer(
            RULES_VERSION,
            RULES_VERSION,
            RULES_VERSION,
            annotations.contract.renderer_version,
            annotations.contract.producer_versions,
        )

    def _representative(self, group, lines: Mapping[str, str]):
        """The picture this card names. Standing in for a model, that is the first capture;
        answering for the whole film, it is the group's best on the rules quality order."""
        if not self._by_quality:
            return next((c for c in group.candidates if c.favourite), group.candidates[0])
        place = {
            c.asset_id: i
            for i, c in enumerate(sorted(group.candidates, key=attrgetter("taken_at")))
        }
        return min(
            group.candidates,
            key=lambda c: representative_key(
                picture_facts(
                    c.source,
                    line=lines.get(c.asset_id, ""),
                    place=place[c.asset_id],
                    of=len(group.candidates),
                    residual=None,
                )
            ),
        )

    def read(self, projections: Sequence[EditorialGroupProjection]) -> TextEpisodeReadResult:
        ids = tuple(dict.fromkeys(a for p in projections for a in p.group.candidate_ids))
        batch = self.annotations.lines_for(ids)
        lines = batch.as_mapping()
        episodes = []
        for projection in projections:
            group = projection.group
            if any(asset not in lines for asset in group.candidate_ids):
                episodes.append(
                    EpisodeEditorialEvidence(
                        projection, None, None, False, "complete annotation evidence unavailable"
                    )
                )
                continue
            identity = EpisodeReadingIdentity.from_annotations(
                group_id=group.group_id,
                producer_key=self.producer.key(),
                annotation_lines={a: lines[a] for a in group.candidate_ids},
            )
            representative = self._representative(group, lines)
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
                        "Owner favourite"
                        if representative.favourite
                        else ("Best of its capture group" if self._by_quality else "First capture"),
                    ),
                ),
                (),
            )
            episodes.append(EpisodeEditorialEvidence(projection, identity, reading, False, None))
        return TextEpisodeReadResult(tuple(episodes), batch, (), 0)
