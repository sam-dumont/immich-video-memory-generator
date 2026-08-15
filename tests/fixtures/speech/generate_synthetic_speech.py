"""Regenerate `synthetic_speech_16k.npy`, the FireRedVAD feature-pipeline fixture.

Run with `python tests/fixtures/speech/generate_synthetic_speech.py`.

The signal is a source-filter vocal-tract synthesis -- a glottal pulse train
with a falling/rising F0 contour, shaped by three formant resonators per vowel
and a short consonant burst at each syllable onset. No recording of anyone is
involved, which is the point: the fixture ships in the repo and must contain
no one's voice.

Layout: two ~1.2 s runs of six syllables separated by a 0.5 s pause, padded to
exactly 3.0 s. The pause is what makes the fixture able to prove the detector
still splits utterances rather than returning one blob.

Stored as int16 PCM (~96 KB) rather than float32 to halve the committed size;
callers divide by 32768 to get back the [-1, 1] floats the detector takes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
DURATION_S = 3.0
SEED = 20260815
OUTPUT = Path(__file__).parent / "synthetic_speech_16k.npy"

# Centre frequency and bandwidth (Hz) of the first three formants per vowel.
VOWEL_FORMANTS = {
    "a": ((730, 90), (1090, 110), (2440, 150)),
    "e": ((530, 80), (1840, 110), (2480, 150)),
    "i": ((270, 70), (2290, 110), (3010, 170)),
    "o": ((570, 80), (840, 110), (2410, 150)),
    "u": ((300, 70), (870, 110), (2240, 150)),
}

# (vowel, duration_s, f0_start_hz, f0_end_hz, gap_after_s)
SYLLABLES = (
    ("a", 0.16, 145, 132, 0.045),
    ("i", 0.13, 138, 150, 0.050),
    ("e", 0.19, 152, 128, 0.040),
    ("o", 0.15, 130, 118, 0.050),
    ("u", 0.18, 122, 140, 0.060),
    ("a", 0.17, 142, 120, 0.000),
)


def resonator(x: np.ndarray, centre_hz: float, bandwidth_hz: float) -> np.ndarray:
    """Two-pole formant filter, applied sample by sample."""
    r = np.exp(-np.pi * bandwidth_hz / SAMPLE_RATE)
    a1 = -2 * r * np.cos(2 * np.pi * centre_hz / SAMPLE_RATE)
    a2 = r * r
    y = np.zeros_like(x)
    for n in range(len(x)):
        v = x[n]
        if n >= 1:
            v -= a1 * y[n - 1]
        if n >= 2:
            v -= a2 * y[n - 2]
        y[n] = v
    return y * (1 - r)


def glottal_source(n_samples: int, f0_start: float, f0_end: float) -> np.ndarray:
    """Rosenberg-style glottal pulse train -- richer in harmonics than a sine."""
    f0 = np.linspace(f0_start, f0_end, n_samples)
    frac = (np.cumsum(f0) / SAMPLE_RATE) % 1.0
    opening = np.where(frac < 0.4, 0.5 * (1 - np.cos(np.pi * frac / 0.4)), 0.0)
    closing = np.where((frac >= 0.4) & (frac < 0.56), np.cos(np.pi * (frac - 0.4) / 0.32), 0.0)
    pulse = opening + closing
    return pulse - pulse.mean()


def syllable(
    vowel: str, duration_s: float, f0_start: float, f0_end: float, rng: np.random.Generator
) -> np.ndarray:
    n = int(duration_s * SAMPLE_RATE)
    source = glottal_source(n, f0_start, f0_end) + 0.01 * rng.standard_normal(n)

    voiced = np.zeros(n)
    for centre, bandwidth in VOWEL_FORMANTS[vowel]:
        voiced += resonator(source, centre, bandwidth)

    burst = np.zeros(n)
    burst_len = int(0.012 * SAMPLE_RATE)
    burst[:burst_len] = rng.standard_normal(burst_len) * np.exp(-np.linspace(0, 6, burst_len))
    voiced += resonator(burst, 2500, 900) * 3.0

    envelope = np.ones(n)
    ramp = int(0.02 * SAMPLE_RATE)
    envelope[:ramp] = np.linspace(0, 1, ramp)
    envelope[-ramp:] = np.linspace(1, 0, ramp)
    return voiced * envelope


def utterance(rng: np.random.Generator) -> np.ndarray:
    parts = []
    for vowel, duration_s, f0_start, f0_end, gap_s in SYLLABLES:
        parts.append(syllable(vowel, duration_s, f0_start, f0_end, rng))
        if gap_s:
            parts.append(np.zeros(int(gap_s * SAMPLE_RATE)))
    return np.concatenate(parts)


def main() -> None:
    rng = np.random.default_rng(SEED)
    audio = np.concatenate(
        [
            np.zeros(int(0.05 * SAMPLE_RATE)),
            utterance(rng),
            np.zeros(int(0.5 * SAMPLE_RATE)),
            utterance(rng),
        ]
    )
    total = int(DURATION_S * SAMPLE_RATE)
    audio = np.concatenate([audio, np.zeros(max(0, total - len(audio)))])[:total]
    audio = audio / np.max(np.abs(audio)) * 0.7

    np.save(OUTPUT, (audio * 32767).astype(np.int16))
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
