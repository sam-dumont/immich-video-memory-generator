"""Measure motion only for chosen Live Photo carriers, with exact source reuse."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_motion_outcomes import MotionAttemptOutcomes
from immich_memories.api.immich import ImmichAPIError
from immich_memories.store.cut_measurements import (
    banked_motion_residuals,
    open_cut_measurements,
    remember_motion_residual,
)

METHOD = "median-flow-v1-12frames-320x240"
# The bank key carries what produced the number, so a changed method retires its own rows.
RESIDUAL_PRODUCER = f"motion-residual-v1@{METHOD}"


def measure_motion(payload: bytes) -> dict:
    """The existing matrix optical-flow measurement, on a temporary playback preview."""
    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="editorial-motion-") as directory:
        path = Path(directory) / "preview.mp4"
        path.write_bytes(payload)
        path.chmod(0o600)
        capture = cv2.VideoCapture(str(path))
        frames = []
        try:
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            seen = set()
            for index in (
                np.linspace(0, total - 1, min(12, total)).astype(int) if total >= 2 else ()
            ):
                if index in seen:
                    continue
                seen.add(index)
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
                ok, frame = capture.read()
                if ok:
                    frames.append(cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY))
        finally:
            capture.release()
    if len(frames) < 2:
        return {"unreadable": True, "frames": len(frames)}
    means, residuals = [], []
    # OpenCV allocates the flow array itself; the stubs cannot express that None.
    unallocated: Any = None
    for first, second in zip(frames, frames[1:], strict=False):
        flow = cv2.calcOpticalFlowFarneback(first, second, unallocated, 0.5, 3, 15, 3, 5, 1.2, 0)
        means.append(float(np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2).mean()))
        residual = flow - np.median(flow.reshape(-1, 2), axis=0)
        residuals.append(float(np.sqrt(residual[..., 0] ** 2 + residual[..., 1] ** 2).mean()))
    return {
        "mean_motion": round(float(np.mean(means)), 3),
        "residual": round(float(np.mean(residuals)), 3),
        "residual_peak": round(float(np.max(residuals)), 3),
        "frames": len(frames),
    }


def motion_source_key(asset) -> str:
    """A reused asset ID alone cannot authenticate a changed source or linked video."""
    material = {"method": METHOD, "asset": asset.model_dump(mode="json")}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sample_motion_members(members, *, maximum=3):
    if maximum < 1:
        raise ValueError("motion sample limit must be positive")
    values = list(dict.fromkeys(members))
    if len(values) <= maximum:
        return values
    if maximum == 1:
        return [values[len(values) // 2]]
    return [values[round(i * (len(values) - 1) / (maximum - 1))] for i in range(maximum)]


def _unavailable_outcome(exc: Exception, phase: str) -> dict:
    """Record what actually failed, keeping only a plausible transport status."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return {
        "status": "unavailable",
        "error_type": type(exc).__name__,
        "phase": phase,
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
    }


@dataclass
class _MotionAttempt:
    """One call's open bank, running metrics and the sources it actually sampled."""

    connection: sqlite3.Connection
    metrics: dict
    sampled_keys: set[str] = field(default_factory=set)


class DemandedMotionResolver:
    """Bound playback downloads after selection; never fetch the unselected wall."""

    def __init__(
        self,
        *,
        assets,
        cache_path: Path,
        fetch_video,
        measure=measure_motion,
        threshold=1.5,
        sample_limit=3,
        on_playback=None,
        outcomes: MotionAttemptOutcomes | None = None,
    ) -> None:
        self.assets = assets
        self.cache_path = cache_path
        self.fetch_video = fetch_video
        self.measure = measure
        self.threshold = threshold
        self.sample_limit = sample_limit
        self.on_playback = on_playback
        self.outcomes = outcomes

    def __call__(self, carriers):
        started = time.monotonic()
        metrics = {
            "method": METHOD,
            "candidate_carriers": 0,
            "candidate_stills": 0,
            "sampled_sources": 0,
            "unsampled_stills": 0,
            "fetch_attempts": 0,
            "new_motion_downloads": 0,
            "playback_cache_write_failures": 0,
            "cache_hits": 0,
            "outcome_replay_hits": 0,
            "download_bytes": 0,
            "unavailable_sources": 0,
            "decode_seconds": 0.0,
            "sample_limit_per_carrier": self.sample_limit,
        }
        with closing(open_cut_measurements(self.cache_path)) as connection:
            attempt = _MotionAttempt(connection, metrics)
            output = [self._resolved_carrier(carrier, attempt) for carrier in carriers]
        metrics["sampled_sources"] = len(attempt.sampled_keys)
        metrics["decode_seconds"] = round(metrics["decode_seconds"], 3)
        metrics["wall_seconds"] = round(time.monotonic() - started, 3)
        return output, metrics

    def _resolved_carrier(self, carrier, attempt: _MotionAttempt) -> dict:
        current = dict(carrier)
        if not carrier.get("motion_candidate"):
            return current
        metrics = attempt.metrics
        members = [
            key
            for key in carrier["members"]
            if key in self.assets and self.assets[key].live_photo_video_id
        ]
        sampled = sample_motion_members(members, maximum=self.sample_limit)
        metrics["candidate_carriers"] += 1
        metrics["candidate_stills"] += len(members)
        metrics["unsampled_stills"] += len(members) - len(sampled)
        unit_key = (
            self.outcomes.begin(carrier, [motion_source_key(self.assets[key]) for key in sampled])
            if self.outcomes is not None
            else ""
        )
        facts = [self._source_fact(self.assets[key], unit_key, attempt) for key in sampled]
        residuals = [float(fact["residual"]) for fact in facts if "residual" in fact]
        residual = max(residuals) if residuals else None
        moving = residual is not None and residual >= self.threshold
        current.update(
            kind="live-motion" if moving else "live-still",
            residual=residual,
            seconds=round(min(float(current["raw_seconds"]), 6.0), 2) if moving else 4.0,
            motion_assessed=bool(residuals),
            motion_evidence={
                "method": METHOD,
                "sampled": len(sampled),
                "available": len(residuals),
                "members": len(members),
            },
        )
        return current

    def _source_fact(self, asset, unit_key: str, attempt: _MotionAttempt) -> dict:
        """One sampled source: a replayed refusal, the banked measurement, or a download."""
        metrics = attempt.metrics
        source_key = motion_source_key(asset)
        attempt.sampled_keys.add(source_key)
        prior = self.outcomes.prior(unit_key, source_key) if self.outcomes else None
        if prior is not None:
            metrics["outcome_replay_hits"] += 1
        if self.outcomes is not None and prior is not None and prior["status"] == "unavailable":
            fact = {"unavailable": prior["error_type"]}
            self.outcomes.observed(unit_key, source_key, fact, error=prior)
            metrics["unavailable_sources"] += 1
            return fact
        banked = banked_motion_residuals(
            attempt.connection, {asset.id: source_metadata_digest(asset)}, RESIDUAL_PRODUCER
        )
        if asset.id in banked:
            fact = banked[asset.id]
            metrics["cache_hits"] += 1
            if self.outcomes is not None:
                self.outcomes.observed(unit_key, source_key, fact)
        else:
            if prior is not None:
                raise ValueError("recorded motion measurement is missing from its bank")
            fact = self._measured_fact(asset, source_key, unit_key, attempt)
        if "residual" not in fact:
            metrics["unavailable_sources"] += 1
        return fact

    def _measured_fact(
        self, asset, source_key: str, unit_key: str, attempt: _MotionAttempt
    ) -> dict:
        metrics = attempt.metrics
        metrics["fetch_attempts"] += 1
        phase = "fetch"
        try:
            payload = self.fetch_video(asset.live_photo_video_id)
            metrics["new_motion_downloads"] += 1
            metrics["download_bytes"] += len(payload)
            self._keep_playback(asset.live_photo_video_id, payload, metrics)
            decode_started = time.monotonic()
            phase = "decode"
            fact = {
                **self.measure(payload),
                "playback_sha256": hashlib.sha256(payload).hexdigest(),
            }
            metrics["decode_seconds"] += time.monotonic() - decode_started
        except (
            OSError,
            ValueError,
            httpx.HTTPError,
            ImmichAPIError,
        ) as exc:  # Missing motion leaves the selected photograph intact.
            fact = {"unavailable": type(exc).__name__}
            if self.outcomes is not None:
                self.outcomes.observed(
                    unit_key, source_key, fact, error=_unavailable_outcome(exc, phase)
                )
            return fact
        # Each completed measurement survives an interrupted run, and answers the next plan.
        remember_motion_residual(
            attempt.connection,
            asset_id=asset.id,
            producer=RESIDUAL_PRODUCER,
            source_digest=source_metadata_digest(asset),
            measured=fact,
        )
        if self.outcomes is not None:
            self.outcomes.observed(unit_key, source_key, fact)
        return fact

    def _keep_playback(self, video_id, payload: bytes, metrics: dict) -> None:
        if self.on_playback is None:
            return
        try:
            self.on_playback(video_id, payload)
        except (OSError, ValueError):
            # Optional sample persistence cannot erase a completed download or
            # change the ordinary motion measurement.
            metrics["playback_cache_write_failures"] += 1


def production_motion_resolver(source, *, on_playback=None):
    """Create the product transport lazily, so a warm replay opens no network client."""

    threshold, sample_limit = 1.5, 3
    material = {
        "case": asdict(source.case),
        "wall_sha256": hashlib.sha256(source.wall_bytes).hexdigest(),
        "moments": list(source.moment_asset_ids.items()),
        "source_keys": [
            (key, motion_source_key(asset)) for key, asset in sorted(source.assets.items())
        ],
    }
    input_key = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    outcomes = MotionAttemptOutcomes(
        output=source.artifact_dir,
        scope={
            "input_key": input_key,
            "method": METHOD,
            "threshold": threshold,
            "sample_limit": sample_limit,
        },
        replay=source.motion_outcome_replay,
    )

    def resolve(carriers):
        from immich_memories.api.sync_client import SyncImmichClient

        client = None

        def fetch(video_id):
            nonlocal client
            if client is None:
                config = source.config.immich
                client = SyncImmichClient(
                    base_url=config.url, api_key=config.api_key, api_version=config.api_version
                )
            return client.get_video_playback(video_id)

        try:
            return DemandedMotionResolver(
                assets=source.assets,
                cache_path=source.store_path,
                fetch_video=fetch,
                threshold=threshold,
                sample_limit=sample_limit,
                on_playback=on_playback,
                outcomes=outcomes,
            )(carriers)
        finally:
            if client is not None:
                client.close()

    return resolve
