"""Production structure-planning seam shared by the product route and sealed replays."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
from itertools import chain
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.editorial_case import Case
from immich_memories.analysis.editorial_demanded_previews import DemandedPreviewReader
from immich_memories.analysis.editorial_motion_facts import production_motion_resolver
from immich_memories.analysis.editorial_orchestration import TextEditorialWorkprint
from immich_memories.analysis.editorial_people import EditorialPeople
from immich_memories.analysis.editorial_picture_facts import PictureFactsProvider
from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.editorial_product_brief import build_editorial_brief
from immich_memories.analysis.editorial_runtime_ports import (
    EditorialRuntimePorts,
    production_attached_pictures,
    production_live_clock_offsets,
    production_sampled_pair_confirmer,
    production_speech_resolver,
    production_story_motion,
)
from immich_memories.analysis.editorial_scene_prints import CachedScenePrints, pinned_encoder
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
    StructurePlanningResult,
)
from immich_memories.analysis.editorial_structure_io import StructureTextJudge
from immich_memories.analysis.editorial_structure_source import capture_structure_input
from immich_memories.analysis.editorial_thin_layer import ThinPolish, catalogued_period
from immich_memories.analysis.editorial_thin_short import ShortReads
from immich_memories.analysis.editorial_thumbnail_hashes import CachedThumbnailHasher
from immich_memories.analysis.episode_demand import DemandEpisodeReadings
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.thumbnail_prefetch import cached_preview_bytes
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.security import write_secret_file
from immich_memories.store.library_catalogue import LibraryAccount
from immich_memories.store.library_overviews import library_period_account

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_runtime import EditorialRunContext
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


class ProductionPostCardBackend:
    """Run the shared structure planner behind one synchronous production seam."""

    def __init__(
        self,
        *,
        config: Config,
        context: EditorialRunContext,
        people: EditorialPeople,
        thumbnail_cache: ThumbnailCache,
        store_path: Path,
        ports: EditorialRuntimePorts,
        fetch_preview: Callable[[str], bytes | None] | None = None,
        attached_sources: Callable[[], Sequence[Asset | VideoClipInfo]] | None = None,
        episode_demand: DemandEpisodeReadings | None = None,
    ) -> None:
        self._episode_demand = episode_demand
        self._fetch_preview = fetch_preview
        self._attached_sources = attached_sources
        self._config = config
        self._context = context
        self._people = people
        self._thumbnail_cache = thumbnail_cache
        self._store_path = store_path
        self._ports = ports
        self.last_structure_result: StructurePlanningResult | None = None
        self.last_companion_assets: dict[str, Asset] = {}
        self.allow_live_motion: bool | None = None
        # One measurement engine per run: planning and the render projection
        # must re-derive the same Live stitch material (#1012).
        self._clock_offsets_provider = None

    def clock_offsets(self, source: StructurePlanningInput, resources):
        """The run's Live companion clock measurements, built once and memoised."""
        if self._clock_offsets_provider is None:
            self._clock_offsets_provider = production_live_clock_offsets(
                source, resources=resources
            )
        return self._clock_offsets_provider

    @property
    def clock_offsets_provider(self):
        """The engine planning used, for the render projection that must agree with it."""
        return self._clock_offsets_provider

    def edit(
        self, workprint: TextEditorialWorkprint | StructurePlanningInput, *, trace: Trace
    ) -> EditorialPlan:
        """Run one structure algorithm for product workprints and sealed matrix evidence."""
        source, allowed_ids = self._structure_source(workprint)
        source = self._apply_runtime_policy(source)
        rules = self._config.editorial.resolve_reader(self._config.llm.model) == "rules"
        if rules:
            source = replace(
                source,
                allow_live_motion=False,
                lineage={
                    **source.lineage,
                    "reader": "rules-v1",
                    "render_policy": {"allow_live_motion": False},
                },
            )
        request_start = len(trace.requests)
        self.last_companion_assets = dict(source.companion_assets)
        resources = ExitStack()
        try:
            effects = (
                self._ports.structure_ports_factory(source)
                if self._ports.structure_ports_factory
                else self._production_effects(source, trace=trace, resources=resources)
            )
            result = self._ports.structure_planner(source, effects)
        finally:
            try:
                resources.close()
            finally:
                _write_visual_requests(source.artifact_dir, trace, request_start)
        if rules:
            result.plan["reader"] = "rules-v1"
            result.plan.setdefault("lineage", {})["reader"] = "rules-v1"
            result.plan["semantic_reuse"] = "none; rules are recomputed from captured facts"
        return self._adopt(result, source.artifact_dir, allowed_ids)

    def _structure_source(
        self, workprint: TextEditorialWorkprint | StructurePlanningInput
    ) -> tuple[StructurePlanningInput, set[str]]:
        if isinstance(workprint, StructurePlanningInput):
            return workprint, _moment_members(workprint)
        context = self._context
        case = Case(
            key=context.key,
            label=context.label,
            product=context.product,
            ranges=context.case_ranges,
            target_seconds=context.target_seconds,
            brief=build_editorial_brief(
                context.product,
                context.case_ranges,
                base=context.base_brief,
                hemisphere=context.hemisphere,
            ),
            target_source=context.target_source,
            people=context.people,
            person_match=context.person_match,
            person_expression=context.person_expression,
            accept_any_provenance=context.accept_any_provenance,
            trip=context.trip,
            album_ref=context.album_ref,
            special_event_id=context.special_event_id,
            event_asset_ids=context.event_asset_ids,
            event_admission=context.event_admission,
        )
        source = capture_structure_input(
            workprint,
            case=case,
            config=self._config,
            people=self._people,
            store_path=self._store_path,
            artifact_dir=context.artifact_dir,
            motion_outcome_replay=context.motion_outcome_replay,
            attached_sources=self._attached_sources() if self._attached_sources else (),
            attached_outcome_replay=context.attached_outcome_replay,
        )
        return source, {candidate.clip.asset.id for candidate in workprint.input_candidates}

    def _apply_runtime_policy(self, source: StructurePlanningInput) -> StructurePlanningInput:
        if self._context.render_timing is not None:
            if (
                source.render_timing is not None
                and source.render_timing != self._context.render_timing
            ):
                raise ValueError("Editorial source and runtime timing disagree")
            source = replace(source, render_timing=self._context.render_timing)
        if self.allow_live_motion is not None:
            source = replace(
                source,
                allow_live_motion=self.allow_live_motion,
                lineage={
                    **source.lineage,
                    "render_policy": {"allow_live_motion": self.allow_live_motion},
                },
            )
        return source

    def _production_effects(
        self, source: StructurePlanningInput, *, trace: Trace, resources: ExitStack
    ) -> StructurePlannerPorts:
        rules = self._config.editorial.resolve_reader(self._config.llm.model) == "rules"
        demanded_previews = (
            DemandedPreviewReader(
                self._thumbnail_cache, self._fetch_preview, allowed_ids=_moment_members(source)
            )
            if self.allow_live_motion is not None and self._fetch_preview is not None
            else None
        )
        read_preview = demanded_previews or (
            lambda asset_id: cached_preview_bytes(self._thumbnail_cache, asset_id)
        )
        thumbnail_hasher = CachedThumbnailHasher(
            source.bank_dir.parent / "thumbnail-hashes.sqlite", read_preview
        )
        resources.callback(thumbnail_hasher.close)
        scene_prints = CachedScenePrints(
            source.bank_dir.parent / "scene-prints.sqlite",
            read_preview,
            open_encoder=pinned_encoder(
                self._config.triage.encoder_path, self._config.triage.provider
            ),
        )
        resources.callback(scene_prints.close)
        if rules:
            from immich_memories.analysis.editorial_rule_reader import (
                NoModelJudge,
                RuleStructureReader,
            )

            return StructurePlannerPorts(
                judge=NoModelJudge(),
                thumbnail_hash=thumbnail_hasher,
                scene_print=scene_prints,
                thumbnail_metrics=thumbnail_hasher.metrics,
                rules=RuleStructureReader(source),
                resolve_speech=production_speech_resolver(source, resources=resources),
            )
        picture_facts = PictureFactsProvider(
            llm_config=self._config.llm,
            cache_path=self._store_path,
            trace=trace,
            preview_bytes=read_preview,
        )
        resources.callback(picture_facts.close)
        sampled_pairs = production_sampled_pair_confirmer(
            source, cache_path=self._store_path, trace=trace
        )
        resources.callback(sampled_pairs.close)
        final_pictures, attached_samples = production_attached_pictures(
            source,
            cache_path=self._store_path,
            pictures=picture_facts,
            pairs=sampled_pairs,
            resources=resources,
        )
        story_motion = production_story_motion(source, cache_path=self._store_path)
        return StructurePlannerPorts(
            judge=StructureTextJudge(
                self._config, source.artifact_dir, cache_path=self._store_path
            ),
            thumbnail_hash=thumbnail_hasher,
            scene_print=scene_prints,
            thumbnail_metrics=thumbnail_hasher.metrics,
            resolve_motion=production_motion_resolver(
                source, on_playback=attached_samples.remember_playback
            ),
            resolve_speech=production_speech_resolver(source, resources=resources),
            observe_attached_material=final_pictures,
            attached_material_metrics=attached_samples.metrics,
            observe_picture=picture_facts.observe,
            observe_story_motion=story_motion.observe,
            story_motion_identity=story_motion.producer,
            story_motion_metrics=story_motion.metrics,
            confirm_sampled_pairs=sampled_pairs,
            confirm_episode_pairs=sampled_pairs.confirm_episode_pairs,
            confirm_story_pairs=sampled_pairs.confirm_story_pairs,
            sampled_pair_metrics=sampled_pairs.metrics,
            sampled_preview_hashes=sampled_pairs.preview_hashes,
            picture_facts_metrics=(
                lambda: (
                    picture_facts.metrics() | {"preview_acquisition": demanded_previews.metrics()}
                )
            )
            if demanded_previews is not None
            else picture_facts.metrics,
            clock_offsets=self.clock_offsets(source, resources),
            **self._thin_polish(source),
        )

    def _thin_polish(self, source: StructurePlanningInput) -> dict[str, Any]:
        """Build the draft with the no-model reader and let the model polish it, when asked.

        Nothing is read here. The draft is built from facts alone, and the period is read when
        the layer asks for its account -- by then the draft has chosen its stories, and only
        those stories' episodes are ever read.
        """
        if not self._config.editorial.thin_model_layer or source.store_path is None:
            return {}
        period = catalogued_period(source.case.ranges)
        config = self._config
        if not period or config.editorial.resolve_reader(config.llm.model) == "rules":
            return {}
        from immich_memories.analysis.editorial_rule_reader import RuleStructureReader

        rules = RuleStructureReader(source)
        return {
            "rules": rules,
            "thin": ThinPolish(
                bank_dir=source.bank_dir,
                read_period=lambda asset_ids_of: self._read_period(source, period, asset_ids_of),
                short=self._short_reads(source, rules.standing),
            ),
        }

    def _short_reads(
        self, source: StructurePlanningInput, standing: Callable[[str], int]
    ) -> ShortReads | None:
        """What a film the polish left short may still read: the run's unread episodes."""
        demand = self._episode_demand
        if demand is None:
            return None
        return ShortReads(
            unread=demand.unread_episodes,
            records=lambda asset_ids: self._records_for(source, asset_ids),
            standing=standing,
        )

    def _records_for(
        self, source: StructurePlanningInput, asset_ids: Sequence[str]
    ) -> Mapping[str, str]:
        """Read and bank these pictures' episodes; what the readings recorded, by picture.

        A failed reading is a film that stays short, never a failed film.
        """
        from immich_memories.analysis.catalogue_runtime import banked_notable_records

        assert source.store_path is not None and self._episode_demand is not None
        try:
            readings = self._episode_demand.readings_for(list(asset_ids))
            return banked_notable_records(
                [reading.identity for reading in readings.values()], store_path=source.store_path
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Could not read more of this period for a short film (%s)", exc)
            return {}

    def _read_period(
        self, source: StructurePlanningInput, period: str, asset_ids_of: Mapping[str, Sequence[str]]
    ) -> tuple[str, Mapping[str, str]]:
        """The account of this period and its records, read from the episodes of the draft's shots.

        A failure here is not a failed film: with no account the layer does not run and the
        run plans the way it always has.
        """
        try:
            return self._demanded_period(source, period, asset_ids_of)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Could not read an account of this period (%s); planning as before", exc)
            return "", {}

    def _demanded_period(
        self, source: StructurePlanningInput, period: str, asset_ids_of: Mapping[str, Sequence[str]]
    ) -> tuple[str, Mapping[str, str]]:
        from immich_memories.analysis.catalogue_runtime import (
            banked_notable_records,
            catalogue_banked_episodes,
        )

        assert source.store_path is not None
        readings = self._demand_readings(asset_ids_of)
        identities = [reading.identity for reading in readings.values()]
        account = library_period_account(source.store_path, period)
        facts = _unread_facts(source, set(readings))
        if not account and (identities or facts):
            catalogue_banked_episodes(
                identities,
                store_path=source.store_path,
                capture_dates={key: asset.file_created_at for key, asset in source.assets.items()},
                config=self._config,
                requester=self._ports.catalogue_requester_factory(self._config),
                unread_facts=facts,
                period=period,
            )
            account = library_period_account(source.store_path, period)
        return account, banked_notable_records(identities, store_path=source.store_path)

    def _demand_readings(self, asset_ids_of: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        """Read the episodes the draft's shots sit in, and nothing else."""
        if self._episode_demand is None:
            return {}
        return self._episode_demand.readings_for(list(chain.from_iterable(asset_ids_of.values())))

    def _adopt(
        self, result: StructurePlanningResult, artifact_dir: Path, allowed_ids: set[str]
    ) -> EditorialPlan:
        if result.plan.get("status") == "planning_incomplete":
            # Preserve an incomplete attempt only after the ordinary source/order
            # checks. Conversion below raises; it cannot yield an empty refusal.
            _validate_structure_carriers(result, allowed_ids=allowed_ids)
            result.write(artifact_dir)
            self.last_structure_result = result
        plan = _plan_from_structure_result(result, allowed_ids=allowed_ids)
        result.write(artifact_dir)
        self.last_structure_result = result
        return plan


def _unread_facts(source: StructurePlanningInput, read: set[str]) -> list[LibraryAccount]:
    """What the draft's own cards say about each episode this cut did not read.

    The no-model reader wrote them to build the draft, so passing them on costs nothing, and
    the account then speaks for the whole period rather than for the shots alone.
    """
    cards = {card.episode_id: card for card in source.episode_readings.values()}
    facts = []
    for episode_id, card in sorted(cards.items()):
        dates = [
            source.assets[asset].file_created_at
            for asset in card.representative_asset_ids
            if asset in source.assets
        ]
        if episode_id in read or not dates or not card.what_happened.strip():
            continue
        material = f"{episode_id}\n{card.evidence_key}\n{card.what_happened}"
        facts.append(
            LibraryAccount(
                sha256(material.encode()).hexdigest(),
                "episode-facts",
                min(dates).strftime("%Y-%m"),
                card.what_happened,
                (),
            )
        )
    return facts


def _moment_members(source: StructurePlanningInput) -> set[str]:
    return set(chain.from_iterable(source.moment_asset_ids.values()))


def _write_visual_requests(artifact_dir: Path, trace: Trace, request_start: int) -> None:
    write_secret_file(
        artifact_dir / "picture-facts-requests.private.json",
        json.dumps(
            {
                "scope": "all structure-stage visual requests; provenance.pass_name "
                "distinguishes picture facts from sampled pair confirmation",
                "requests": trace.as_dict()["requests"][request_start:],
            },
            indent=2,
        ),
    )


def _validate_structure_carriers(result: StructurePlanningResult, *, allowed_ids: set[str]) -> None:
    carriers = result.plan["carriers"]
    selected_ids = tuple(row["asset_id"] for row in carriers)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("structure planner selected the same source more than once")
    if not set(selected_ids).issubset(allowed_ids):
        raise ValueError("structure planner selected outside the conserved input")
    if carriers != sorted(carriers, key=itemgetter("taken")):
        raise ValueError("structure planner carriers must be chronological")


def _plan_from_structure_result(
    result: StructurePlanningResult, *, allowed_ids: set[str]
) -> EditorialPlan:
    _validate_structure_carriers(result, allowed_ids=allowed_ids)
    carriers = result.plan["carriers"]
    if result.plan.get("status") == "planning_incomplete":
        reason = result.plan.get("intent_report", {}).get("reason") or "bounded planning stopped"
        raise RuntimeError(f"Editorial planning incomplete: {reason}")
    if result.plan.get("status") == "insufficient_material":
        # A deliberate empty cut stops generation. Unavailability would invoke
        # legacy selection; diagnostic carriers remain in the written result.
        return EditorialPlan()
    return EditorialPlan(
        selections=tuple(
            EditorialSelection(
                asset_id=row["asset_id"],
                render_mode="motion" if row["kind"] in ("live-motion", "video") else "still",
            )
            for row in carriers
        )
    )
