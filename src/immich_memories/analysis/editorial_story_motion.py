"""Literal, source-bound sequence evidence for competing ordinary video choices."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path

import httpx

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_numbers import exact_number
from immich_memories.analysis.selection_descriptions import describe_editorial_assets
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.visual_atlas import AtlasSource, build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset, AssetType
from immich_memories.processing.frame_sampling import probe_duration
from immich_memories.security import write_secret_file


class StoryMotionFacts:
    """Describe sampled action without changing inventory, standing, or audience facts.

    Only an admitted single-source ordinary video can be observed here. Live Photo
    companions retain their separate bound-material and audience-certification path.
    Downloads are keyed by complete source metadata and verified against their saved
    digest. Failed observations are retained in this attempt, never banked as facts.
    """

    def __init__(
        self,
        *,
        assets: Mapping[str, Asset],
        allowed_ids: set[str],
        fetch_playback: Callable[[str], bytes | None],
        requester,
        trace,
        cache_dir: Path,
        output_dir: Path,
    ):
        self.assets = assets
        self.allowed_ids = frozenset(allowed_ids)
        self.fetch = fetch_playback
        self.requester, self.trace = requester, trace
        self.cache_dir, self.output_dir = cache_dir, output_dir
        for directory in (cache_dir, cache_dir / "frames", output_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records: dict[str, dict] = {}
        self._lines: dict[str, str] = {}
        self.downloads = 0

    def observe(self, unit: Mapping) -> str:
        asset_id = unit.get("asset_id")
        asset = self.assets.get(asset_id) if isinstance(asset_id, str) else None
        if (
            asset_id not in self.allowed_ids
            or asset is None
            or asset.type != AssetType.VIDEO
            or unit.get("kind") != "video"
            or unit.get("members") != [asset_id]
            or unit.get("video_ids") != [asset_id]
            or unit.get("trim_points")
        ):
            return ""
        seconds = unit.get("seconds")
        number = exact_number(seconds)
        if number is None or number <= 0:
            return ""
        metadata_sha = source_metadata_digest(asset)
        request = {
            "asset_id": asset_id,
            "source_metadata_sha256": metadata_sha,
            "candidate_interval": [0.0, seconds],
            "version": "story-motion-facts-v1",
        }
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if key in self._lines:
            return self._lines[key]
        record = request | {"status": "unavailable"}
        line = ""
        try:
            line = self._observe(asset, seconds, metadata_sha, key, record)
        except (OSError, ValueError, httpx.HTTPError, ImmichAPIError) as exc:
            record["failure_type"] = type(exc).__name__
        self.records[key] = record
        self._lines[key] = line
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_secret_file(
            self.output_dir / "observations.private.json",
            json.dumps(self.records, indent=2, default=str),
        )
        return line

    def _playback(self, asset_id: str, metadata_sha: str) -> tuple[Path, str]:
        directory = self.cache_dir / metadata_sha
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path, manifest = directory / "playback.private.mp4", directory / "source.private.json"
        if path.exists() and manifest.exists():
            saved = json.loads(manifest.read_text())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if saved == {
                "asset_id": asset_id,
                "metadata_sha256": metadata_sha,
                "playback_sha256": digest,
            }:
                return path, digest
            raise ValueError("Playback cache does not bind its source and bytes")
        payload = self.fetch(asset_id)
        self.downloads += 1
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("No playback bytes")
        digest = hashlib.sha256(payload).hexdigest()
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".mp4")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        write_secret_file(
            manifest,
            json.dumps(
                {"asset_id": asset_id, "metadata_sha256": metadata_sha, "playback_sha256": digest}
            ),
        )
        return path, digest

    def _observe(self, asset, seconds, metadata_sha, key, record):
        path, digest = self._playback(asset.id, metadata_sha)
        duration = probe_duration(path)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("No finite playback duration")
        interval = (0.0, min(seconds, duration))
        visual = AtlasSource(asset, motion_path=path, proposed_segment=interval)
        atlas = build_visual_atlas((visual,), frame_cache_dir=self.cache_dir / "frames")
        tile = atlas.tile_for(asset.id)
        record.update(
            playback_sha256=digest,
            probed_duration=duration,
            sampled_interval=list(interval),
            filmstrip_sha256=tile.sha256,
            frame_count=tile.frame_count,
        )
        if tile.kind != "filmstrip" or tile.frame_count < 2:
            record["reason"] = "No chronological motion evidence; no cover-image substitute"
            return ""
        candidate = EditorialCandidate(
            asset.id,
            asset.file_created_at,
            "video",
            (),
            None,
            asset.is_favorite,
            asset,
            interval,
            interval[1],
            (),
        )
        prepared = PreparedEditorialSource((candidate,), (visual,), (), (), self.trace, (), ())
        directory = self.output_dir / key
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = describe_editorial_assets(
            prepared,
            requester=self.requester,
            output_dir=directory,
            frame_cache_dir=None,
            atlas=atlas,
            limits=VisionRequestLimits(max_output_tokens=800, timeout_seconds=120),
        )
        for page in directory.iterdir():
            if page.is_file():
                page.chmod(0o600)
        description = next(
            (
                d
                for d in result.descriptions
                if d.asset_id == asset.id and d.motion_contribution != "not_observed"
            ),
            None,
        )
        if description is None:
            record["reason"] = "No complete motion description"
            return ""
        record.update(status="observed", description=asdict(description))
        return (
            f"{tile.frame_count} chronological samples within {interval[0]:.2f}–"
            f"{interval[1]:.2f}s of this candidate (final fit may shorten it): "
            f"{description.text} Temporal contribution: {description.motion_reason or description.motion_contribution}. "
            "Sampled evidence only; not a full-video or privacy assessment."
        )

    def metrics(self) -> dict:
        return {
            "requested": len(self.records),
            "downloads": self.downloads,
            "observed": sum(r["status"] == "observed" for r in self.records.values()),
            "records": self.records,
        }
