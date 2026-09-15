"""Cache local VAD measurements by captured source metadata and detector settings."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.processing.probe_cache import ProbeCache
from immich_memories.security import write_secret_file
from immich_memories.speech.fireredvad import FireRedSpeechDetector
from immich_memories.speech.vad import VAD_SAMPLE_RATE, extract_audio_16k


class SpeechFacts:
    """Only retained motion pays for audio extraction; successful facts survive reruns."""

    def __init__(self, *, assets, cache_dir: Path, fetch, config):
        self.assets, self.cache_dir, self.fetch = assets, cache_dir, fetch
        self.detector = FireRedSpeechDetector(config.vad_threshold, config.min_silence_ms)
        self.settings = config.model_dump()
        self.memo: dict[str, list[tuple[float, float]]] = {}

    def __call__(self, asset_id: str) -> list[tuple[float, float]]:
        identity = {
            "method": "firered-aed-utterances-v1",
            "source": source_metadata_digest(self.assets[asset_id]),
            "settings": self.settings,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if key in self.memo:
            return self.memo[key]
        cache = self.cache_dir / f"{key}.json"
        if cache.exists():
            record = json.loads(cache.read_text())
            if record["identity"] != identity:
                raise ValueError("Speech cache source changed")
            regions = [tuple(pair) for pair in record["regions"]]
        else:
            regions = self._measure(asset_id)
            write_secret_file(cache, json.dumps({"identity": identity, "regions": regions}))
        self.memo[key] = regions
        return regions

    def _measure(self, asset_id: str) -> list[tuple[float, float]]:
        payload = self.fetch(asset_id)
        if not payload:
            raise ValueError("Selected video is unavailable for speech detection")
        with tempfile.TemporaryDirectory(prefix="editorial-speech-") as directory:
            path = Path(directory) / "source.mp4"
            path.write_bytes(payload)
            if not ProbeCache().get(path).has_audio:
                return []
            audio = extract_audio_16k(path)
            if audio is None:
                raise ValueError("Could not extract selected video audio for speech detection")
            return [(r.start, r.end) for r in self.detector.detect(audio, VAD_SAMPLE_RATE)]
