"""Film-time evidence: the annotation store a cut reads, and the preparation it waits on.

Split from editorial_runtime, which composes the planner; this is the one stage of it that
talks to the producers and decides whether the cut may start.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source import FullEditorialSource
from immich_memories.operations.caption_origins import caption_origin_summary
from immich_memories.operations.cut_progress import StageUpdate
from immich_memories.people.context import PersonPromptContext
from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


class EditorialInputsRequired(RuntimeError):
    """Selection needs prepared annotation evidence before it can run."""

    def __init__(self, store_path: Path, *, detail: str = "") -> None:
        self.store_path = store_path
        super().__init__(
            f"Story-first selection needs prepared annotations at {store_path}. "
            "Prepare this library's annotations or set advanced.editorial.annotation_database "
            "to its existing annotation store."
            + (f" Missing or unavailable: {detail}" if detail else "")
        )


def ensure_annotation_store(store_path: Path) -> None:
    if store_path.is_file():
        return
    from immich_memories.store.editorial_preparation import initialize, private_database_path

    with closing(sqlite3.connect(private_database_path(store_path))) as connection:
        initialize(connection)


@dataclass(frozen=True, slots=True)
class AnnotationReadings:
    """One annotation-line contract shared by episode reading and the source gate."""

    store_path: Path
    config: Config
    people: Mapping[str, PersonPromptContext]
    subjects: tuple[str, ...] = ()

    def reader(self, prepared: Any) -> StoredAnnotationLineReader:
        editorial = self.config.editorial
        return StoredAnnotationLineReader(
            store_path=self.store_path,
            candidates=prepared.candidates,
            description_model=editorial.description_model,
            head_versions=editorial.head_versions,
            pixel_producer_key=editorial.pixel_producer_key,
            people_context=self.people,
            subjects=self.subjects,
        )


def _log_preparation(result: Any) -> None:
    """Name the tier and what it cost, in the terminal, on the machine that paid for it.

    A wall-clock total cannot tell a self-hoster which producer their box cannot
    afford, and the artifact holding the same numbers is inside the attempt tree.
    """
    service = result.service_rates()
    rates = " ".join(
        f"{stage} {seconds:.3f}s/pic"
        + (f" ({service[stage]:.3f}s of it in the service)" if stage in service else "")
        for stage, seconds in sorted(result.stage_rates().items())
    )
    logger.info(
        "preparation tier=%s: %d pictures requested%s",
        result.tier,
        result.requested,
        f"; {rates}" if rates else "; nothing to produce",
    )
    if origins := caption_origin_summary(result.caption_provenance):
        logger.info("%s", origins)


@dataclass(frozen=True, slots=True)
class EvidencePreparation:
    """Produce every annotation the story-first read needs, then gate screen documents."""

    readings: AnnotationReadings
    client: FullEditorialSource
    thumbnail_cache: ThumbnailCache
    ports: EditorialRuntimePorts
    artifact_dir: Callable[[], Path]

    def __call__(
        self,
        prepared: Any,
        on_stage: Callable[[StageUpdate], None] | None,
        reach: frozenset[str],
    ) -> dict[str, Any]:
        result = self._produce(prepared, on_stage, reach)
        _log_preparation(result)
        write_secret_file(
            self.artifact_dir() / "preparation.private.json",
            json.dumps(
                asdict(result) | {"seconds_per_picture": result.stage_rates()},
                ensure_ascii=False,
                indent=2,
            ),
        )
        unservable: dict[str, Any] = dict(result.unservable_sources)
        if unservable:
            logger.warning(
                "%d of %d sources leave the film: %s",
                len(unservable),
                result.requested,
                "; ".join(sorted(set(unservable.values()))),
            )
        if not result.complete:
            missing = ", ".join(
                f"{key}: {len(ids)}" for key, ids in result.missing_by_producer.items()
            )
            # A count of missing facts is a symptom. When a producer refused --
            # no model, no endpoint -- its own sentence says why, so it goes in
            # the message rather than only into preparation.private.json.
            raise EditorialInputsRequired(
                self.readings.store_path,
                detail="; ".join(filter(None, (missing, *result.producer_failures))),
            )
        readable = tuple(a for a in prepared.candidate_ids if a not in unservable)
        if not readable:
            return unservable
        return unservable | self._screen_documents(prepared, readable)

    def _produce(
        self, prepared: Any, on_stage: Callable[[StageUpdate], None] | None, reach: frozenset[str]
    ) -> Any:
        from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
        from immich_memories.operations.cut_progress import StageProgressWriter

        config = self.readings.config
        batch_size = config.editorial.preparation.batch_size
        # The sentence and the numbers are published together, on one throttle,
        # so a watcher never sees a bar disagreeing with the row above it.
        live = StageProgressWriter(self.artifact_dir)

        def progress(stage: str, done: int, total: int) -> None:
            if done not in {0, total} and done % batch_size:
                return
            if on_stage is not None:
                on_stage(live.publish(stage, done, total))

        prepare = self.ports.prepare_annotations or prepare_editorial_annotations
        return prepare(
            assets=tuple(c.source for c in prepared.candidates if c.asset_id in reach),
            store_path=self.readings.store_path,
            thumbnail_cache=self.thumbnail_cache,
            preparation_config=config.editorial.preparation,
            triage_config=config.triage,
            head_versions=config.editorial.head_versions,
            inference_config=config.inference,
            description_model=config.editorial.description_model,
            pixel_producer_key=config.editorial.pixel_producer_key,
            fetch_preview=lambda asset_id: self.ports.fetch_preview(self.client, asset_id),
            fetch_faces=lambda asset_id: self.ports.fetch_faces(self.client, asset_id),
            read_playback=partial(self.ports.fetch_playback_range, self.client),
            progress=progress,
            on_asset=live.note_asset,
        )

    def _screen_documents(self, prepared: Any, readable: tuple[str, ...]) -> dict[str, Any]:
        from immich_memories.analysis.editorial_source_gate import (
            SCREEN_DOCUMENT_GATE_VERSION,
            screen_document_rejections,
        )

        batch = self.readings.reader(prepared).lines_for(readable)
        if batch.missing_asset_ids:
            raise EditorialInputsRequired(
                self.readings.store_path,
                detail=f"{len(batch.missing_asset_ids)} unreadable annotation lines; "
                + "; ".join(batch.warnings),
            )
        exclusions: dict[str, Any] = screen_document_rejections(batch)
        write_secret_file(
            self.artifact_dir() / "source-gate.private.json",
            json.dumps(
                {"version": SCREEN_DOCUMENT_GATE_VERSION, "excluded": exclusions},
                ensure_ascii=False,
                indent=2,
            ),
        )
        return exclusions
