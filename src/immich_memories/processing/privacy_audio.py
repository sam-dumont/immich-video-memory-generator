"""Privacy audio processing — segment-wise waveform reversal.

Makes speech unintelligible while preserving rhythm, intonation, and
ambient sounds. Based on segment-wise waveform reversal which achieves
97.9% Word Error Rate in academic evaluation.

See: https://arxiv.org/html/2507.08412
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Tuned for speech intelligibility destruction:
# 200ms segments are long enough to contain a full phoneme (avg ~80ms)
# but short enough to preserve prosodic rhythm at segment boundaries.
DEFAULT_SEGMENT_MS = 200
DEFAULT_OVERLAP_MS = 10


def reverse_speech_segments(
    audio: np.ndarray,
    sample_rate: int,
    segment_ms: int = DEFAULT_SEGMENT_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
) -> np.ndarray:
    """Reverse audio in small segments to destroy speech intelligibility.

    You can hear people talking but can't understand words. Each segment
    is reversed independently, then overlap-added with crossfade to
    avoid clicks at boundaries.
    """
    seg_len = int(sample_rate * segment_ms / 1000)
    overlap = min(int(sample_rate * overlap_ms / 1000), seg_len // 4)

    if seg_len <= 0 or len(audio) == 0:
        return audio.copy()

    is_stereo = audio.ndim == 2
    fade_in = np.linspace(0, 1, overlap)
    fade_out = np.linspace(1, 0, overlap)
    if is_stereo:
        fade_in = fade_in[:, np.newaxis]
        fade_out = fade_out[:, np.newaxis]

    result = np.zeros_like(audio)
    pos = 0
    step = seg_len - overlap

    while pos < len(audio):
        end = min(pos + seg_len, len(audio))
        segment = audio[pos:end][::-1].copy()

        # Crossfade at boundaries to avoid clicks
        if pos > 0 and len(segment) > overlap:
            segment[:overlap] *= fade_in[: len(segment[:overlap])]
        if end < len(audio) and len(segment) > overlap:
            segment[-overlap:] *= fade_out[-len(segment[-overlap:]) :]

        result[pos:end] += segment
        pos += step

    return result


def apply_privacy_audio(input_path: Path, output_path: Path, sample_rate: int = 48000) -> None:
    """Extract audio from a video, apply segment-wise reversal, save as WAV."""
    import subprocess

    import soundfile as sf

    # Extract audio to temp WAV
    raw_wav = output_path.with_suffix(".raw.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            str(raw_wav),
        ],
        capture_output=True,
        check=True,
    )

    audio, sr = sf.read(str(raw_wav))
    reversed_audio = reverse_speech_segments(audio, sr)
    sf.write(str(output_path), reversed_audio, sr)

    raw_wav.unlink(missing_ok=True)
    logger.info(f"Privacy audio: reversed {len(audio) / sr:.1f}s of speech segments")
