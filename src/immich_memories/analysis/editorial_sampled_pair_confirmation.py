"""Confirm nominated sampled pictures using conserved pixels and the existing pair contract."""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from immich_memories.analysis.editorial_bound_sample import (
    BoundVideoSample,
    attached_link_digest,
    source_metadata_digest,
)
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.editorial_picture_facts import picture_observation_request
from immich_memories.analysis.editorial_thumbnail_hashes import METHOD, CachedThumbnailHasher
from immich_memories.analysis.llm_providers import resolved_llm_config
from immich_memories.analysis.selection_same_picture import (
    SamePicturePairDecision,
    confirm_same_picture_pairs,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.visual_atlas import AtlasTile, VisualAtlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.models import Asset
from immich_memories.config_models_llm import LLMConfig


def _record_identities(record: Mapping[str, Any]) -> tuple[str, str]:
    image_sha, identity = record.get("image_sha256"), record.get("identity")
    if (
        not isinstance(image_sha, str)
        or not isinstance(identity, str)
        or not all(
            len(value) == 64 and all(character in "0123456789abcdef" for character in value)
            for value in (image_sha, identity)
        )
    ):
        raise ValueError("own picture needs exact image and request identities")
    return image_sha, identity


class CachedSampledPairConfirmer:
    """Observe only explicit pairs; the caller owns nomination and any eventual cut.

    Source IDs must be in the caller's captured selectable material. Existing own-picture
    records must bind the already stored JPEGs through their original gateway provenance.
    No preview acquisition, filmstrip construction or source description is involved.
    A positive establishes sampled-picture sameness, not complete video equality, privacy,
    event membership, a preferred file or permission to discard a protected carrier.
    """

    def __init__(
        self,
        *,
        assets: Mapping[str, Asset],
        allowed_ids: Collection[str],
        llm_config: LLMConfig,
        cache_path: Path,
        image_dir: Path,
        trace: Trace,
        sheet_dir: Path,
        limits: VisionRequestLimits | None = None,
    ) -> None:
        self._assets = dict(assets)
        self._samples: dict[str, tuple[BoundVideoSample, Asset]] = {}
        self._allowed = frozenset(allowed_ids)
        if not self._allowed <= self._assets.keys():
            raise ValueError("sampled pair material must exist in captured source assets")
        if any(asset.id != asset_id for asset_id, asset in self._assets.items()):
            raise ValueError("captured asset keys must retain their source identity")
        self._image_dir, self._sheet_dir, self._trace = image_dir, sheet_dir, trace
        self._hash_cache_path = cache_path.parent / "sampled-preview-hashes.sqlite"
        self._hash_payloads: dict[str, bytes] = {}
        self._preview_hasher: CachedThumbnailHasher | None = None
        self._hash_requests = 0
        self._hash_unavailable = 0
        self._hash_seconds = 0.0
        self._limits = limits or VisionRequestLimits()
        self._metrics: dict[str, int | float] = {
            "nominated_pairs": 0,
            "routed_pairs": 0,
            "logical_requests": 0,
            "actual_http_attempts": 0,
            "cache_hits": 0,
            "preview_downloads": 0,
            "motion_decodes": 0,
            "wall_seconds": 0.0,
        }
        self._config = resolved_llm_config(llm_config).model_copy(deep=True)
        self._gateway = VisualEditorialGateway(
            llm_config=self._config,
            cache_path=cache_path,
            trace=trace,
        )

    def bind_sample(self, sample: BoundVideoSample, source: Asset) -> None:
        """Authorize a sampled entity through real admitted parents, never as a candidate."""
        if not isinstance(sample, BoundVideoSample) or not isinstance(source, Asset):
            raise TypeError("sample registration requires real bound source material")
        if source.id != sample.source_id or not source.is_video:
            raise ValueError("sample source must be its actual companion video")
        if not set(sample.parent_ids) <= self._allowed:
            raise ValueError("attached sample parent is outside admitted selectable material")
        parents = tuple(self._assets[parent] for parent in sample.parent_ids)
        if attached_link_digest(parents, source.id) != sample.link_sha256:
            raise ValueError("attached sample source linkage changed")
        if source_metadata_digest(source) != sample.metadata_sha256:
            raise ValueError("attached sample metadata differs from the captured video")
        if sample.key in self._assets:
            raise ValueError("sample entity collides with a library asset")
        self._samples[sample.key] = (sample, source.model_copy(deep=True))

    def close(self) -> None:
        try:
            if self._preview_hasher is not None:
                self._preview_hasher.close()
        finally:
            self._gateway.close()

    def metrics(self) -> dict[str, int | float | str]:
        """Cumulative adapter work only, excluding other users of the shared trace."""
        hashes = self._preview_hasher.metrics() if self._preview_hasher is not None else {}
        return self._metrics | {
            "wall_seconds": round(self._metrics["wall_seconds"] + self._hash_seconds, 3),
            "preview_hash_method": METHOD,
            "preview_hashes_requested": self._hash_requests,
            "preview_hashes_computed": hashes.get("hashes_computed", 0),
            "preview_hashes_cache_hits": hashes.get("cache_hits", 0),
            "preview_hashes_unavailable": self._hash_unavailable,
            "preview_hash_wall_seconds": round(self._hash_seconds, 3),
        }

    def preview_hashes(
        self,
        asset_ids: Collection[str],
        picture_records: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, str]:
        """Measure conserved sampled images; this neither nominates nor approves cuts."""
        if isinstance(asset_ids, str) or any(
            not isinstance(asset_id, str) or not asset_id for asset_id in asset_ids
        ):
            raise ValueError("sampled preview hashes require source IDs")
        started = time.perf_counter()
        result: dict[str, str] = {}
        try:
            for asset_id in dict.fromkeys(asset_ids):
                self._hash_requests += 1
                value = self._preview_hash(asset_id, picture_records.get(asset_id, {}))
                if value is None:
                    self._hash_unavailable += 1
                else:
                    result[asset_id] = value
            return result
        finally:
            self._hash_seconds += time.perf_counter() - started

    def _preview_hash(self, asset_id: str, record: Mapping[str, Any]) -> str | None:
        try:
            tile = self._tile(asset_id, record)
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        if tile.sha256 is None or tile.jpeg_bytes is None:
            return None
        if self._preview_hasher is None:
            self._preview_hasher = CachedThumbnailHasher(
                self._hash_cache_path, self._hash_payloads.get
            )
        # Hash the exact validated bytes, not a second file read that could have
        # changed. The existing persistent key also binds the hash implementation
        # and size; no asset-only memo is involved.
        self._hash_payloads[tile.sha256] = tile.jpeg_bytes
        try:
            value = self._preview_hasher(tile.sha256)
        finally:
            self._hash_payloads.pop(tile.sha256, None)
        if (
            isinstance(value, str)
            and len(value) == 16
            and all(character in "0123456789abcdef" for character in value)
        ):
            return value
        return None

    def _tile(self, asset_id: str, record: Mapping[str, Any]) -> AtlasTile:
        registered = self._samples.get(asset_id)
        source_id = registered[0].source_id if registered else asset_id
        if (asset_id not in self._allowed and registered is None) or record.get(
            "status"
        ) != "available":
            raise ValueError(
                "source is outside selectable material or its own picture is unavailable"
            )
        image_sha, identity = _record_identities(record)
        self._assert_banked_provenance(record, source_id, image_sha, identity)
        payload = self._stored_picture(image_sha)
        if registered is not None:
            self._assert_bound_sample(record, registered[0], source_id, payload, identity)
        # A video preview remains one sampled picture; never synthesize unseen frames.
        return AtlasTile(asset_id, "photo", payload, image_sha, 1)

    def _assert_banked_provenance(
        self, record: Mapping[str, Any], source_id: str, image_sha: str, identity: str
    ) -> None:
        cached = self._gateway.cache.answer_for(identity)
        if cached is None:
            raise ValueError("own picture lacks its original banked provenance")
        provenance = json.loads(cached[1])
        if (
            provenance.get("input_ids") != [source_id]
            or provenance.get("sheet_hashes") != [image_sha]
            or provenance.get("request_key") != identity
            or provenance.get("pass_version") != record.get("producer", {}).get("pass_version")
            or provenance.get("schema_version") != record.get("producer", {}).get("schema_version")
        ):
            raise ValueError("own picture provenance does not bind this source and stored image")

    def _stored_picture(self, image_sha: str) -> bytes:
        path = self._image_dir / f"{image_sha}.jpg"
        if path.resolve().parent != self._image_dir.resolve():
            raise ValueError("own picture path escaped the conserved image directory")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != image_sha:
            raise ValueError("stored own-picture bytes changed")
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        return payload

    def _assert_bound_sample(
        self,
        record: Mapping[str, Any],
        sample: BoundVideoSample,
        source_id: str,
        payload: bytes,
        identity: str,
    ) -> None:
        if (
            record.get("source_asset_id") != source_id
            or record.get("sample_id") != sample.key
            or record.get("sample_binding") != sample.as_dict()
            or record.get("input_sha256") != sample.frame_sha256
        ):
            raise ValueError("own observation does not describe this bound video sample")
        request = picture_observation_request(
            config=self._config,
            image_dir=self._image_dir,
            asset_id=source_id,
            tile=payload,
            input_sha=sample.frame_sha256,
            sample=sample,
        )
        if self._gateway.request_identity(request).key() != identity:
            raise ValueError("sample observation request does not bind its exact material")

    def _candidate(self, asset_id: str) -> EditorialCandidate:
        # Internal sample entity IDs distinguish intervals; source stays the real Asset.
        asset = self._samples[asset_id][1] if asset_id in self._samples else self._assets[asset_id]
        return EditorialCandidate(
            asset_id=asset_id,
            taken_at=asset.file_created_at,
            media_kind="video"
            if asset.is_video
            else "live_photo"
            if asset.is_live_photo
            else "photo",
            live_photo_stitch_member_ids=(),
            rendering_family_id=None,
            favourite=asset.is_favorite,
            source=asset,
            proposed_segment=None,
            shippable_duration=0.0,  # This relation neither measures nor allocates playable material.
            grounded_annotations=(),
        )

    def _routed_row(
        self,
        index: int,
        pair: tuple[str, str],
        picture_records: Mapping[str, Mapping[str, Any]],
        tiles: dict[str, AtlasTile],
        decisions: dict[int, SamePicturePairDecision],
        eligible: list[int],
    ) -> dict[str, Any]:
        """Conserve one nomination's own pictures, or retain both without comparing."""
        left, right = pair
        row: dict[str, Any] = {"input_ids": [left, right], "status": "pending"}
        try:
            for asset_id in (left, right):
                if asset_id not in tiles:
                    tiles[asset_id] = self._tile(asset_id, picture_records.get(asset_id, {}))
            row["picture_sha256"] = [tiles[asset_id].sha256 for asset_id in (left, right)]
            eligible.append(index)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            row.update(status="unavailable", reason=str(exc))
            decisions[index] = SamePicturePairDecision(
                left, right, False, "!! Sampled picture pair unavailable; both retained"
            )
        return row

    def _compare_routed(
        self,
        nominated: tuple[tuple[str, str], ...],
        eligible: list[int],
        tiles: dict[str, AtlasTile],
        rows: list[dict[str, Any]],
        decisions: dict[int, SamePicturePairDecision],
        *,
        episode_similarity: bool,
    ) -> None:
        results = confirm_same_picture_pairs(
            tuple(
                (self._candidate(nominated[index][0]), self._candidate(nominated[index][1]))
                for index in eligible
            ),
            atlas=VisualAtlas(tuple(tiles.values())),
            requester=self._gateway,
            sheet_output_dir=self._sheet_dir,
            limits=self._limits,
            # No hash shortcut: the second positive arrangement remains necessary.
            concurrency=1,
            episode_similarity=episode_similarity,
        )
        for index, result in zip(eligible, results, strict=True):
            if (result.earlier_asset_id, result.later_asset_id) != nominated[index]:
                raise ValueError("sampled pair primitive changed nomination identity")
            decisions[index] = result
            rows[index].update(
                status="unavailable" if result.warning else "compared",
                same=result.same,
                warning=result.warning,
            )

    def __call__(
        self,
        pairs: Sequence[tuple[str, str]],
        picture_records: Mapping[str, Mapping[str, Any]],
    ) -> tuple[tuple[SamePicturePairDecision, ...], dict[str, Any]]:
        return self._confirm(pairs, picture_records, episode_similarity=False)

    def confirm_episode_pairs(self, pairs, picture_records):
        """Check visual similarity after the caller established nearby episode membership."""
        return self._confirm(pairs, picture_records, episode_similarity=True)

    def _confirm(self, pairs, picture_records, *, episode_similarity):
        nominated = tuple(pairs)
        if any(
            len(pair) != 2
            or any(not isinstance(value, str) or not value for value in pair)
            or pair[0] == pair[1]
            for pair in nominated
        ):
            raise ValueError("sampled pair nominations need two distinct source IDs")
        if len({frozenset(pair) for pair in nominated}) != len(nominated):
            raise ValueError("sampled picture pairs must not repeat, including reversed duplicates")
        started = time.perf_counter()
        trace_start = len(self._trace.requests)
        rows: list[dict[str, Any]] = []
        decisions: dict[int, SamePicturePairDecision] = {}
        tiles: dict[str, AtlasTile] = {}
        eligible: list[int] = []
        for index, pair in enumerate(nominated):
            rows.append(self._routed_row(index, pair, picture_records, tiles, decisions, eligible))
        if eligible:
            self._compare_routed(
                nominated, eligible, tiles, rows, decisions, episode_similarity=episode_similarity
            )
        requests = self._trace.requests[trace_start:]
        audit: dict[str, Any] = {
            "scope": (
                "nearby episode visual similarity; no whole-video equality or cut authority"
                if episode_similarity
                else "sampled picture relation only; no whole-video equality or cut authority"
            ),
            "nominated_pairs": len(nominated),
            "routed_pairs": len(eligible),
            "logical_requests": len(requests),
            "actual_http_attempts": sum(request.actual_calls for request in requests),
            "cache_hits": sum(request.cache_hit for request in requests),
            "pair_votes": "one negative stops; positive requires both arrangements",
            "preview_downloads": 0,
            "motion_decodes": 0,
            "rows": rows,
            "wall_seconds": time.perf_counter() - started,
        }
        for key in self._metrics:
            self._metrics[key] += audit[key]
        return tuple(decisions[index] for index in range(len(nominated))), audit
