"""Acquire a frame of explicitly displayed attached material, without admitting it."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from immich_memories.analysis.editorial_attached_outcomes import AttachedAttemptOutcomes
from immich_memories.analysis.editorial_bound_sample import (
    BoundVideoSample,
    attached_link_digest,
    source_metadata_digest,
)
from immich_memories.analysis.editorial_numbers import exact_number
from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset, AssetType
from immich_memories.processing.frame_sampling import extract_frame_at
from immich_memories.security import write_secret_file

EXTRACTOR_VERSION = "extract-frame-at-v1"
_KINDS = frozenset({"transcoded-playback", "original"})


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _cached_digest(value: Any, message: str) -> str:
    """A cache manifest names its bytes with an exact lowercase SHA-256, or it is void."""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(message)
    return value


def _bytes(path: Path, expected: str) -> bytes:
    payload = path.read_bytes()
    if not payload or hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("attached sample cache bytes differ from their provenance")
    return payload


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class AttachedVideoSamples:
    """Positive playback/frame cache, with failures confined to this provider attempt.

    The caller supplies real captured video metadata separately from admitted primary
    assets. Nothing here fetches metadata, changes primary eligibility, observes a model
    or assigns a sharing verdict. A new provider does not inherit failed acquisitions;
    an explicitly selected attempt journal can conserve an earlier unavailable outcome.
    """

    def __init__(
        self,
        *,
        assets: Mapping[str, Asset],
        allowed_ids: Collection[str],
        companion_assets: Mapping[str, Asset],
        cache_dir: Path,
        fetch_playback: Callable[[str], bytes | None],
        extract: Callable[..., Path | None] = extract_frame_at,
        extractor_version: str = EXTRACTOR_VERSION,
        width: int = 800,
        payload_kind: str = "transcoded-playback",
        outcomes: AttachedAttemptOutcomes | None = None,
    ) -> None:
        self._assets = {key: asset.model_copy(deep=True) for key, asset in assets.items()}
        self._companions = {
            key: asset.model_copy(deep=True) for key, asset in companion_assets.items()
        }
        self._allowed = frozenset(allowed_ids)
        if not self._allowed <= self._assets.keys() or any(
            asset.id != key for key, asset in (*self._assets.items(), *self._companions.items())
        ):
            raise ValueError("attached material requires consistent captured source identities")
        if any(not asset.is_video for asset in self._companions.values()):
            raise ValueError("companion metadata must describe the real video, not a parent still")
        if type(width) is not int or width <= 0 or payload_kind not in _KINDS:
            raise ValueError("attached sample needs a positive width and explicit playback kind")
        if not isinstance(extractor_version, str) or not extractor_version.strip():
            raise ValueError("attached sample requires a versioned extractor")
        self._directory = cache_dir
        self._fetch, self._extract = fetch_playback, extract
        self._kind, self._width = payload_kind, width
        self._extractor = f"{extractor_version};width={width};source={payload_kind}"
        self._payload_memo: dict[str, tuple[Path, str] | None] = {}
        self._frame_memo: dict[str, bytes | None] = {}
        self._unavailable: dict[str, str] = {}
        self._payload_errors: dict[str, dict[str, Any]] = {}
        self._frame_errors: dict[str, dict[str, Any]] = {}
        self._outcomes = outcomes
        self._expected_requests: dict[str, dict[str, Any]] | None = None
        self._seen_requests: set[str] = set()
        self._material_finished = False
        self._metrics: dict[str, int | float] = {
            "requested_samples": 0,
            "playback_cache_hits": 0,
            "playback_memo_hits": 0,
            "remembered_playbacks": 0,
            "frame_cache_hits": 0,
            "fetch_attempts": 0,
            "new_playback_downloads": 0,
            "download_bytes": 0,
            "frame_decodes": 0,
            "decode_seconds": 0.0,
            "unavailable_samples": 0,
            "outcome_replay_hits": 0,
            "wall_seconds": 0.0,
        }

    def metrics(self) -> dict[str, int | float]:
        return self._metrics.copy()

    def unavailable(self) -> dict[str, str]:
        """Attempt-local evidence gaps, keyed by the exact requested sample identity."""
        return self._unavailable.copy()

    def begin_material(self, material: Any, requests: Sequence[Mapping[str, Any]]) -> None:
        """Validate the complete final demand set before any acquisition effects."""
        if self._expected_requests is not None or isinstance(requests, str):
            raise ValueError("attached material must begin exactly once")
        expected = {}
        for row in requests:
            if not isinstance(row, Mapping) or set(row) != {
                "video_id",
                "parent_ids",
                "start",
                "end",
            }:
                raise ValueError("attached demand must declare its source, parents and interval")
            request, _source = self._request(**row)
            expected[_digest(request)] = request
        envelope = {
            "materials": material,
            "extractor": self._extractor,
            "width": self._width,
            "payload_kind": self._kind,
        }
        _digest(envelope)  # Reject non-JSON or nonfinite material before any effects.
        if self._outcomes is not None:
            self._outcomes.begin(envelope, expected)
        self._expected_requests = expected

    def finish_material(self) -> None:
        """Seal only after every declared request has an explicit actual outcome."""
        if self._expected_requests is None or self._material_finished:
            raise ValueError("attached material must finish exactly once after beginning")
        if self._seen_requests != self._expected_requests.keys():
            raise ValueError("attached material is missing demanded outcomes")
        if self._outcomes is not None:
            self._outcomes.finish()
        self._material_finished = True

    def remember_playback(self, video_id: str, payload: bytes) -> bool:
        """Keep a successful ordinary playback fetch, without counting or fetching it twice.

        This callback accepts transcoded playback only. Missing captured metadata or a
        source without an admitted still link cannot be invented from the supplied bytes.
        The caller remains responsible for its actual original download counters.
        """
        source = self._companions.get(video_id)
        if (
            self._kind != "transcoded-playback"
            or source is None
            or not any(self._assets[key].live_photo_video_id == video_id for key in self._allowed)
            or not isinstance(payload, bytes)
            or not payload
        ):
            return False
        self._store_playback(source, payload)
        self._metrics["remembered_playbacks"] += 1
        return True

    def _admitted_parents(self, video_id: str, parent_ids: Sequence[str]) -> tuple[Asset, ...]:
        if (
            not isinstance(video_id, str)
            or not video_id.strip()
            or not isinstance(parent_ids, Sequence)
            or isinstance(parent_ids, str)
            or not parent_ids
            or any(not isinstance(key, str) for key in parent_ids)
            or not set(parent_ids) <= self._allowed
        ):
            raise ValueError("sample parents must be explicitly admitted primary assets")
        parents = tuple(self._assets[key] for key in parent_ids)
        if any(parent.type != AssetType.IMAGE for parent in parents):
            raise ValueError("attached sample parents must be admitted still images")
        return parents

    def _request(
        self, video_id: str, *, parent_ids: Sequence[str], start: float, end: float
    ) -> tuple[dict[str, Any], Asset | None]:
        link = attached_link_digest(self._admitted_parents(video_id, parent_ids), video_id)
        begin, finish = exact_number(start), exact_number(end)
        if begin is None or finish is None or not 0 <= start < end:
            raise ValueError("sample requires a positive finite displayed interval")
        source = self._companions.get(video_id)
        if (
            source is not None
            and source.duration_seconds is not None
            and source.duration_seconds > 0
            and finish > source.duration_seconds
        ):
            raise ValueError("displayed interval exceeds the captured video duration")
        return {
            "source_id": video_id,
            "parent_ids": list(parent_ids),
            "link_sha256": link,
            "metadata_sha256": source_metadata_digest(source) if source else None,
            "start": begin,
            "end": finish,
            "extractor": self._extractor,
        }, source

    def acquire(
        self, video_id: str, *, parent_ids: Sequence[str], start: float, end: float
    ) -> tuple[BoundVideoSample, bytes, Asset] | None:
        """Return the midpoint frame and its real source; never fetch an unknown ID."""
        request, source = self._request(video_id, parent_ids=parent_ids, start=start, end=end)
        key = _digest(request)
        if self._material_finished or (
            self._expected_requests is not None and key not in self._expected_requests
        ):
            raise ValueError("attached acquisition is outside the open declared material")
        prior = self._outcomes.prior(key) if self._outcomes is not None else None
        begun = time.monotonic()
        self._metrics["requested_samples"] += 1
        try:
            if prior is not None:
                return self._replayed_outcome(key, prior, source)
            return self._acquired_sample(key, request, source)
        finally:
            if key in self._unavailable:
                self._metrics["unavailable_samples"] += 1
            self._metrics["wall_seconds"] += time.monotonic() - begun

    def _replayed_outcome(
        self, key: str, prior: dict[str, Any], source: Asset | None
    ) -> tuple[BoundVideoSample, bytes, Asset] | None:
        self._metrics["outcome_replay_hits"] += 1
        if prior["status"] == "unavailable":
            self._unavailable[key] = prior["reason"]
            result = None
        else:
            if source is None:
                raise ValueError("replayed sample lost its captured source")
            result = self._replay_sample(prior["sample"], source)
        self._record_outcome(key, prior)
        return result

    def _acquired_sample(
        self, key: str, request: dict[str, Any], source: Asset | None
    ) -> tuple[BoundVideoSample, bytes, Asset] | None:
        if source is None:
            self._record_unavailable(key, "metadata", "missing_captured_video_metadata")
            return None
        payload = self._playback(source)
        if payload is None:
            self._record_unavailable(
                key,
                "fetch",
                "playback_unavailable_this_attempt",
                details=self._payload_errors.get(_digest(self._playback_identity(source))),
            )
            return None
        path, payload_sha = payload
        start, end = request["start"], request["end"]
        frame_request = self._frame_request(source, payload_sha, start, end)
        frame = self._frame(path, frame_request)
        if frame is None:
            self._record_unavailable(
                key,
                "decode",
                "frame_unavailable_this_attempt",
                payload_sha=payload_sha,
                details=self._frame_errors.get(_digest(frame_request)),
            )
            return None
        sample = BoundVideoSample(
            source_id=request["source_id"],
            parent_ids=tuple(request["parent_ids"]),
            link_sha256=request["link_sha256"],
            metadata_sha256=source_metadata_digest(source),
            payload_sha256=payload_sha,
            start=start,
            end=end,
            timestamp=start + (end - start) / 2,
            frame_sha256=hashlib.sha256(frame).hexdigest(),
            extractor_version=self._extractor,
        )
        self._record_outcome(key, {"status": "available", "sample": sample.as_dict()})
        return sample, frame, source.model_copy(deep=True)

    def _record_outcome(self, key: str, outcome: dict[str, Any]) -> None:
        if self._outcomes is not None:
            self._outcomes.observed(key, outcome)
        self._seen_requests.add(key)

    def _record_unavailable(
        self,
        key: str,
        phase: str,
        reason: str,
        *,
        payload_sha: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._unavailable[key] = reason
        self._record_outcome(
            key,
            {
                "status": "unavailable",
                "reason": reason,
                "phase": phase,
                "error_type": (details or {}).get("error_type"),
                "http_status": (details or {}).get("http_status"),
                "payload_sha256": payload_sha,
            },
        )

    def _frame_request(
        self, source: Asset, payload_sha: str, start: float, end: float
    ) -> dict[str, Any]:
        return {
            "schema": "attached-frame-v1",
            "source_id": source.id,
            "metadata_sha256": source_metadata_digest(source),
            "payload_sha256": payload_sha,
            "payload_kind": self._kind,
            "start": start,
            "end": end,
            "timestamp": start + (end - start) / 2,
            "extractor_version": self._extractor,
        }

    def _replay_sample(
        self, record: dict[str, Any], source: Asset
    ) -> tuple[BoundVideoSample, bytes, Asset]:
        sample = BoundVideoSample.from_dict(record)
        request = self._frame_request(source, sample.payload_sha256, sample.start, sample.end)
        frame_record = json.loads(
            (self._directory / "frames" / f"{_digest(request)}.json").read_text()
        )
        if (
            frame_record.get("request") != request
            or frame_record.get("frame_sha256") != sample.frame_sha256
        ):
            raise ValueError("replayed frame manifest changed")
        _bytes(self._directory / "playback" / f"{sample.payload_sha256}.mp4", sample.payload_sha256)
        frame = _bytes(
            self._directory / "frames" / f"{sample.frame_sha256}.jpg", sample.frame_sha256
        )
        self._metrics["playback_cache_hits"] += 1
        self._metrics["frame_cache_hits"] += 1
        return sample, frame, source.model_copy(deep=True)

    @staticmethod
    def _error_details(error: Exception) -> dict[str, Any]:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status is None:
            status = getattr(error, "status_code", None)
        return {
            "error_type": type(error).__name__,
            "http_status": status if type(status) is int and 100 <= status <= 599 else None,
        }

    def _playback(self, source: Asset) -> tuple[Path, str] | None:
        identity = self._playback_identity(source)
        key = _digest(identity)
        if key in self._payload_memo:
            if self._payload_memo[key] is not None:
                self._metrics["playback_memo_hits"] += 1
            return self._payload_memo[key]
        directory = self._directory / "playback"
        manifest = directory / f"{key}.json"
        if manifest.exists():
            record = json.loads(manifest.read_text())
            if record.get("identity") != identity:
                raise ValueError("playback cache does not bind the captured source")
            digest = _cached_digest(record["payload_sha256"], "invalid playback cache digest")
            path = directory / f"{digest}.mp4"
            _bytes(path, digest)
            self._metrics["playback_cache_hits"] += 1
        else:
            self._payload_memo[key] = None  # A failure is retried only by a new provider attempt.
            self._metrics["fetch_attempts"] += 1
            try:
                payload = self._fetch(source.id)
            except (OSError, ValueError, httpx.HTTPError, ImmichAPIError) as exc:
                self._payload_errors[key] = self._error_details(exc)
                return None
            if not isinstance(payload, bytes) or not payload:
                return None
            self._metrics["new_playback_downloads"] += 1
            self._metrics["download_bytes"] += len(payload)
            return self._store_playback(source, payload)
        result = (path, digest)
        self._payload_memo[key] = result
        return result

    def _playback_identity(self, source: Asset) -> dict[str, Any]:
        return {
            "schema": "attached-playback-v1",
            "source_id": source.id,
            "metadata": source.model_dump(mode="json"),
            "payload_kind": self._kind,
        }

    def _store_playback(self, source: Asset, payload: bytes) -> tuple[Path, str]:
        identity = self._playback_identity(source)
        key, digest = _digest(identity), hashlib.sha256(payload).hexdigest()
        directory = self._directory / "playback"
        path = directory / f"{digest}.mp4"
        _write_bytes(path, payload)
        write_secret_file(
            directory / f"{key}.json", json.dumps({"identity": identity, "payload_sha256": digest})
        )
        result = (path, digest)
        self._payload_memo[key] = result
        return result

    def _frame(self, video: Path, request: dict[str, Any]) -> bytes | None:
        key = _digest(request)
        if key in self._frame_memo:
            return self._frame_memo[key]
        directory = self._directory / "frames"
        manifest = directory / f"{key}.json"
        if manifest.exists():
            frame = self._banked_frame(directory, manifest, request)
        else:
            self._frame_memo[key] = None
            decoded = self._decoded_frame(video, request, manifest, key)
            if decoded is None:
                return None
            frame = decoded
        self._frame_memo[key] = frame
        return frame

    def _banked_frame(self, directory: Path, manifest: Path, request: dict[str, Any]) -> bytes:
        record = json.loads(manifest.read_text())
        if record.get("request") != request:
            raise ValueError("frame cache does not bind the displayed interval")
        digest = _cached_digest(record["frame_sha256"], "invalid frame cache digest")
        frame = _bytes(directory / f"{digest}.jpg", digest)
        self._metrics["frame_cache_hits"] += 1
        return frame

    def _decoded_frame(
        self, video: Path, request: dict[str, Any], manifest: Path, key: str
    ) -> bytes | None:
        directory = self._directory / "frames"
        _bytes(video, request["payload_sha256"])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = time.monotonic()
        self._metrics["frame_decodes"] += 1
        try:
            frame = self._extracted_jpeg(video, request, directory)
        except (OSError, ValueError) as exc:
            self._frame_errors[key] = self._error_details(exc)
            return None
        finally:
            self._metrics["decode_seconds"] += time.monotonic() - started
        if frame is None:
            return None
        digest = hashlib.sha256(frame).hexdigest()
        _write_bytes(directory / f"{digest}.jpg", frame)
        write_secret_file(manifest, json.dumps({"request": request, "frame_sha256": digest}))
        return frame

    def _extracted_jpeg(
        self, video: Path, request: dict[str, Any], directory: Path
    ) -> bytes | None:
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            output = Path(temporary) / "frame.jpg"
            result = self._extract(
                video, timestamp=request["timestamp"], width=self._width, output_path=output
            )
            if result is None:
                return None
            if result != output:
                raise ValueError("extractor returned a different output path")
            frame = result.read_bytes()
            with Image.open(io.BytesIO(frame)) as image:
                if image.format != "JPEG" or image.width != self._width:
                    return None
                image.verify()
        return frame
