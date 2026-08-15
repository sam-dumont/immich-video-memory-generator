from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

SMART_TURN_REPO = "pipecat-ai/smart-turn-v3"
SMART_TURN_FILE = "smart-turn-v3.0.onnx"
SMART_TURN_SAMPLE_RATE = 16000
SMART_TURN_WINDOW_S = 8.0


def window_ending_at(
    audio: np.ndarray,
    sample_rate: int,
    end_time: float | None = None,
    window_s: float = SMART_TURN_WINDOW_S,
) -> np.ndarray:
    """Right-aligned window of `window_s` seconds ending at `end_time`.

    smart-turn expects a fixed-length window whose right edge is the candidate
    boundary. Clips shorter than the window are left-padded with silence.
    `end_time=None` scores the tail of `audio` as-is -- `SmartTurnDetector`
    has no time parameter of its own (see `completion_probability` below), so
    callers who want a specific instant slice `audio` to end there first.
    """
    if end_time is None:
        end_time = len(audio) / sample_rate
    target = int(window_s * sample_rate)
    end_sample = min(int(end_time * sample_rate), len(audio))
    start_sample = max(0, end_sample - target)
    chunk = audio[start_sample:end_sample]

    if len(chunk) < target:
        chunk = np.concatenate([np.zeros(target - len(chunk), dtype=np.float32), chunk])

    return chunk.astype(np.float32)


class TurnDetector(Protocol):
    def completion_probability(self, audio: np.ndarray, sample_rate: int) -> float: ...


class SmartTurnDetector:
    """smart-turn-v3 via ONNX Runtime.

    Uses the fp32 build rather than int8: latency is irrelevant offline and fp32
    is worth several accuracy points on Mandarin, Hindi and Spanish.

    Returns 0.5 -- neutral, neither evidence for nor against -- whenever the model
    is unavailable, so callers never special-case a missing dependency.

    The ONNX graph takes `input_features` -- an 80-bin log-mel spectrogram over
    the 8 s window, shape `(1, 80, 800)` -- not raw waveform. `WhisperFeatureExtractor`
    (from `transformers`, which does not require torch for this pure-numpy
    feature path) reproduces upstream's `inference.py` exactly: same class,
    same `chunk_length=8`, same `do_normalize=True`. The output tensor is
    named "logits" but upstream confirms it is already a sigmoid probability
    -- no extra activation is applied here.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = model_dir
        self._session: Any = None
        self._feature_extractor: Any = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._load()
        return self._available

    def _load(self) -> bool:
        try:
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from transformers import WhisperFeatureExtractor

            path = hf_hub_download(
                repo_id=SMART_TURN_REPO,
                filename=SMART_TURN_FILE,
                cache_dir=str(self.model_dir) if self.model_dir else None,
            )
            self._session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
            self._feature_extractor = WhisperFeatureExtractor(chunk_length=int(SMART_TURN_WINDOW_S))
            return True
        except (ImportError, RuntimeError, OSError) as exc:
            logger.debug("smart-turn unavailable: %s", type(exc).__name__)
            return False

    def completion_probability(self, audio: np.ndarray, sample_rate: int) -> float:
        if not self.available or self._session is None or self._feature_extractor is None:
            return 0.5

        window = window_ending_at(audio, sample_rate)
        target_samples = int(SMART_TURN_WINDOW_S * sample_rate)
        inputs = self._feature_extractor(
            window,
            sampling_rate=sample_rate,
            return_tensors="np",
            padding="max_length",
            max_length=target_samples,
            truncation=True,
            do_normalize=True,
        )
        features = inputs.input_features.astype(np.float32)
        name = self._session.get_inputs()[0].name

        try:
            outputs = self._session.run(None, {name: features})
        except (RuntimeError, ValueError) as exc:
            logger.debug("smart-turn inference failed: %s", type(exc).__name__)
            return 0.5

        return float(np.asarray(outputs[0]).ravel()[0])
