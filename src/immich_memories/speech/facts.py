"""Measure speech on a cut's carriers once, and bank it per picture for the next cut."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.api.models import Asset
from immich_memories.processing.probe_cache import ProbeCache, ProbeError
from immich_memories.speech.fireredvad import FireRedSpeechDetector
from immich_memories.speech.vad import VAD_SAMPLE_RATE, extract_audio_16k
from immich_memories.store.cut_measurements import (
    banked_speech_regions,
    open_cut_measurements,
    reading_cut_measurements,
    remember_speech_regions,
)

logger = logging.getLogger(__name__)

METHOD = "firered-aed-utterances-v1"

Regions = list[tuple[float, float]]


def speech_producer(config: Any) -> str:
    """The detector and the exact settings behind an answer, composed into its bank key."""
    settings = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":"))
    return f"speech-regions-v1@{METHOD}/{hashlib.sha256(settings.encode()).hexdigest()[:12]}"


def read_speech_regions(
    store_path: Path, assets: Iterable[Asset], producer: str
) -> dict[str, tuple[tuple[float, float], ...]]:
    """The speech a cut already measured in these clips, keyed by clip."""
    digests = {asset.id: source_metadata_digest(asset) for asset in assets}
    if not digests:
        return {}
    return reading_cut_measurements(
        store_path, lambda c: banked_speech_regions(c, digests, producer)
    )


class SpeechMeasurementUnavailable(RuntimeError):
    """A selected source could not be measured for speech.

    Speech-boundary protection is a best-effort refinement on top of an already
    valid cut: a playback, download or audio-extraction failure means that one
    carrier keeps its selected interval, not that the memory is unrenderable.
    """


class SpeechFacts:
    """Only retained motion pays for audio extraction; what it measures is banked per clip."""

    def __init__(
        self,
        *,
        assets: Mapping[str, Asset],
        store_path: Path,
        fetch,
        config,
        measure: Callable[[str], Regions] | None = None,
    ):
        self.assets, self.store_path, self.fetch = assets, Path(store_path), fetch
        self.detector = FireRedSpeechDetector(config.vad_threshold, config.min_silence_ms)
        self.producer = speech_producer(config)
        self.memo: dict[tuple[str, str], Regions] = {}
        self._measure_source = measure or self._measure

    def __call__(self, asset_id: str) -> Regions:
        digest = source_metadata_digest(self.assets[asset_id])
        if (asset_id, digest) in self.memo:
            return self.memo[(asset_id, digest)]
        banked = read_speech_regions(self.store_path, (self.assets[asset_id],), self.producer)
        if asset_id in banked:
            regions = list(banked[asset_id])
        else:
            regions = self._measure_source(asset_id)
            self._remember(asset_id, digest, regions)
        self.memo[(asset_id, digest)] = regions
        return regions

    def _remember(self, asset_id: str, digest: str, regions: Regions) -> None:
        try:
            with closing(open_cut_measurements(self.store_path)) as connection:
                remember_speech_regions(
                    connection,
                    asset_id=asset_id,
                    producer=self.producer,
                    source_digest=digest,
                    regions=regions,
                )
        except (OSError, sqlite3.Error) as error:
            # An unwritable bank costs the next cut a measurement, never this cut.
            logger.debug(
                "Speech regions for %s were not banked: %s", asset_id, type(error).__name__
            )

    def _measure(self, asset_id: str) -> Regions:
        try:
            payload = self.fetch(asset_id)
        except Exception as error:
            # WHY: any transport failure is "this source cannot be measured",
            # which degrades the refinement; only a caller seeing this class
            # may treat it as non-fatal.
            raise SpeechMeasurementUnavailable(
                f"playback for {asset_id} could not be fetched: {type(error).__name__}"
            ) from error
        if not payload:
            raise SpeechMeasurementUnavailable(f"playback for {asset_id} was empty")
        with tempfile.TemporaryDirectory(prefix="editorial-speech-") as directory:
            path = Path(directory) / "source.mp4"
            path.write_bytes(payload)
            try:
                probe = ProbeCache().get(path)
            except ProbeError as error:
                raise SpeechMeasurementUnavailable(
                    f"playback for {asset_id} could not be probed: {type(error).__name__}"
                ) from error
            if not probe.has_audio:
                return []
            audio = extract_audio_16k(path)
            if audio is None:
                raise SpeechMeasurementUnavailable(
                    f"audio for {asset_id} could not be extracted for speech detection"
                )
            return [(r.start, r.end) for r in self.detector.detect(audio, VAD_SAMPLE_RATE)]
