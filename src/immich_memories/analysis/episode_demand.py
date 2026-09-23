"""Read an episode when a cut needs its meaning, not because it is in the period.

A month's episodes are the expensive half of a model-tier cut: 283 readings over four months
cost 23 minutes, and a year of them is most of an hour before a single shot is chosen. The
polish layer does not need them to choose: the no-model reader drafts the film from dates,
places, people and the preparation facts, and only the episodes its shots sit in ever get read.

So this stands where the scope-wide reader stood. Asked to read the period it answers with the
factual cards the no-model reader already produces, free. Asked later for the pictures a story
holds, it reads exactly those episodes through the real text reader and banks them, once.

A film therefore pays for the episodes it shows. `prepare --overviews` remains the way to fill
the rest in advance, and nothing here needs it to have run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.store.episode_readings import BankedEpisodeReading, EpisodeReadingProducer

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_rule_episodes import EpisodeReader
    from immich_memories.analysis.selection_source import PreparedEditorialSource
    from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
    from immich_memories.analysis.text_episode_reader import TextEpisodeReadResult

logger = logging.getLogger(__name__)


class DemandEpisodeReadings:
    """The draft's free reader, and the paid one behind it for the stories that are chosen."""

    def __init__(
        self,
        rules: Callable[[PreparedEditorialSource], EpisodeReader],
        on_demand: Callable[[PreparedEditorialSource], EpisodeReader],
    ) -> None:
        self._rules = rules
        self._on_demand = on_demand
        self._prepared: PreparedEditorialSource | None = None
        self.demanded: list[str] = []

    @property
    def producer(self) -> EpisodeReadingProducer:
        """The draft is built from the factual cards, so their producer is what made it."""
        return self._reader(self._rules).producer

    def read(self, projections: Sequence[EditorialGroupProjection]) -> TextEpisodeReadResult:
        """The period, answered from facts alone: no model call, nothing banked."""
        return self._reader(self._rules).read(projections)

    def remember(self, prepared: PreparedEditorialSource) -> None:
        """Keep the canonical source the draft was built from; a demand projects onto it."""
        self._prepared = prepared

    def _reader(self, build: Callable[[PreparedEditorialSource], EpisodeReader]) -> EpisodeReader:
        if self._prepared is None:
            raise RuntimeError("the demand reader has no prepared source yet")
        return build(self._prepared)

    def unread_episodes(self, asset_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """The canonical episodes these pictures sit in that no demand of this run has read.

        Each maps to its members among the pictures handed in. Nothing is read to answer it.
        """
        prepared = self._prepared
        if prepared is None:
            return {}
        known = frozenset(prepared.candidate_ids)
        wanted = [asset for asset in dict.fromkeys(asset_ids) if asset in known]
        return {
            projection.group.group_id: projection.scoped_candidate_ids
            for projection in project_episode_groups(prepared, wanted)
            if projection.group.group_id not in self.demanded
        }

    def readings_for(self, asset_ids: Sequence[str]) -> dict[str, BankedEpisodeReading]:
        """Read and bank every canonical episode these pictures belong to, by episode id.

        The whole episode is read, not the pictures handed in: an episode is the unit the
        reading is banked at, and half of one is not a cheaper question. An episode already
        banked costs nothing, so a story asked about twice in one run is paid for once.
        """
        prepared = self._prepared
        if prepared is None:
            return {}
        known = frozenset(prepared.candidate_ids)
        wanted = [asset for asset in dict.fromkeys(asset_ids) if asset in known]
        if not wanted:
            return {}
        projections = project_episode_groups(prepared, wanted)
        self.demanded.extend(
            projection.group.group_id
            for projection in projections
            if projection.group.group_id not in self.demanded
        )
        logger.info(
            "Reading %d of the period's %d episodes, the ones the draft's shots sit in",
            len(projections),
            len(prepared.episode_groups),
        )
        result = self._reader(self._on_demand).read(projections)
        return {
            evidence.projection.group.group_id: evidence.reading
            for evidence in result.episodes
            if evidence.reading is not None
        }


ReaderFactory = Callable[["PreparedEditorialSource"], "EpisodeReader"]


def demand_reader_factory(
    rules: ReaderFactory, text: ReaderFactory, *, mode: str, on_demand: bool
) -> tuple[ReaderFactory, DemandEpisodeReadings | None]:
    """The episode reader a run's event pass is built with, and the demand behind it, if any.

    A film of a whole calendar month, a whole year or a window over several years on the model
    tier is the polish layer's route, and the only one whose draft needs no reading at all: it
    is built from facts, and the episodes its shots sit in are read afterwards. Every other
    span reads as it always has.
    """
    if mode == "rules":
        return rules, None
    if not on_demand:
        return text, None
    demand = DemandEpisodeReadings(rules, text)

    def remembered(prepared: PreparedEditorialSource) -> EpisodeReader:
        demand.remember(prepared)
        return demand

    return remembered, demand
