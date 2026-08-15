"""FireRedVAD audio-event detector, used as the default `SpeechDetector`.

Vendors `bundled_models/fireredvad_aed.onnx` from
https://github.com/FireRedTeam/FireRedVAD (Apache-2.0, see
`bundled_models/LICENSE-FireRedVAD`). The feature pipeline below must match
upstream's `fireredvad/core/audio_feat.py` exactly -- CMVN stats, frame
options, and the int16 feature scale all come from a model trained on that
exact distribution; deviating from any of them turns the output into noise
rather than raising an error.

`cmvn.ark` is a 2x81 Kaldi binary matrix (per-mel-bin sum and sum-of-squares
plus a frame count in the last column) and is not vendored -- it was read
once with `kaldiio` and the resulting 80 means / 80 inverse-std-devs are
inlined below as `_CMVN_MEANS` / `_CMVN_INVERSE_STD`, so `kaldiio` is not a
runtime dependency of this package.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.vad import VAD_SAMPLE_RATE

logger = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).parent / "bundled_models" / "fireredvad_aed.onnx"

_FRAME_SHIFT_S = 0.01
_NUM_MEL_BINS = 80

# Kaldi fbank features are extracted from int16-range PCM, not the [-1, 1]
# floats extract_audio_16k() returns -- the model was trained on that scale.
_INT16_SCALE = 32768.0

# Derived once from upstream's cmvn.ark: means[d] = stats[0,d]/count,
# inverse_std[d] = 1/sqrt(max(stats[1,d]/count - means[d]**2, 1e-20)).
_CMVN_MEANS: tuple[float, ...] = (
    10.42295175,
    10.86209741,
    11.76454438,
    12.49016470,
    13.25983008,
    13.89594383,
    14.36494024,
    14.59394835,
    14.74972360,
    14.66831535,
    14.73079672,
    14.77505246,
    14.98905198,
    15.17800493,
    15.25352031,
    15.32863705,
    15.33401859,
    15.28864170,
    15.42766169,
    15.24626616,
    15.09257380,
    15.29042194,
    15.07575009,
    15.18677287,
    15.08867324,
    15.17079740,
    15.07017809,
    15.15079534,
    15.10853283,
    15.11534508,
    15.14127999,
    15.13183236,
    15.14519587,
    15.19151893,
    15.23547867,
    15.30636975,
    15.37302148,
    15.41639463,
    15.45985744,
    15.39143273,
    15.46357624,
    15.39966121,
    15.46290792,
    15.44162912,
    15.48496953,
    15.55240178,
    15.63809193,
    15.70548935,
    15.76700885,
    15.85512378,
    15.86726978,
    15.89153741,
    15.92314483,
    15.97838261,
    16.01480167,
    16.04867494,
    16.08202991,
    16.09680075,
    16.09373669,
    16.07247920,
    16.07550966,
    16.02227088,
    15.97676210,
    15.89786455,
    15.81274164,
    15.71120511,
    15.60419889,
    15.55351944,
    15.51025275,
    15.46002382,
    15.41568436,
    15.37602765,
    15.32834898,
    15.29537080,
    15.18547019,
    15.01704498,
    14.90508003,
    14.62380657,
    14.13809381,
    13.31387035,
)
_CMVN_INVERSE_STD: tuple[float, ...] = (
    0.24949809,
    0.23563235,
    0.23145153,
    0.23322339,
    0.23182660,
    0.22853357,
    0.22434870,
    0.21898920,
    0.21832438,
    0.22082593,
    0.22296736,
    0.22288416,
    0.22234811,
    0.22100643,
    0.21994202,
    0.22005444,
    0.22070092,
    0.22150810,
    0.22236667,
    0.22305292,
    0.22335342,
    0.22438906,
    0.22547702,
    0.22690076,
    0.22823023,
    0.22931472,
    0.23046728,
    0.23083553,
    0.23143383,
    0.23220659,
    0.23257989,
    0.23361970,
    0.23437241,
    0.23508252,
    0.23578079,
    0.23589200,
    0.23602098,
    0.23663800,
    0.23749876,
    0.23798452,
    0.23899378,
    0.23974815,
    0.24030836,
    0.24097694,
    0.24143249,
    0.24135466,
    0.24079938,
    0.24047405,
    0.23995525,
    0.23952288,
    0.23948089,
    0.23936509,
    0.23929339,
    0.23902199,
    0.23857873,
    0.23814702,
    0.23804621,
    0.23824194,
    0.23860096,
    0.23915407,
    0.23922541,
    0.23938308,
    0.23973360,
    0.23960562,
    0.24028503,
    0.24061813,
    0.24067930,
    0.24096202,
    0.24043606,
    0.24021527,
    0.23972514,
    0.23871998,
    0.23744131,
    0.23619509,
    0.23337281,
    0.22680233,
    0.22577503,
    0.22503847,
    0.22631137,
    0.22899493,
)


class FireRedSpeechDetector:
    """FireRedVAD's AED model, restricted to its speech column.

    The AED ONNX outputs `probs[1, T, 3]` at 100 fps -- speech, singing,
    music. Column 0 (speech) is the only one used; `fireredvad_vad.onnx`
    (the alternative binary VAD model, not vendored) defines voice as
    speech-union-singing and fires on sustained tones, which would trade
    the AED head's clean separation of speech from music for false
    positives on singing and held notes -- not an improvement.
    """

    def __init__(self, threshold: float = 0.25, min_silence_ms: int = 200) -> None:
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self._session: Any = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._load()
        return self._available

    def _load(self) -> bool:
        try:
            import kaldi_native_fbank  # noqa: F401
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(_MODEL_PATH), providers=["CPUExecutionProvider"]
            )
            return True
        except (ImportError, RuntimeError, OSError) as exc:
            logger.debug("FireRedVAD unavailable: %s", type(exc).__name__)
            return False

    def detect(self, audio: np.ndarray, sample_rate: int) -> list[SpeechRegion]:
        if not self.available:
            return []

        feat = _extract_features(audio, sample_rate)
        if feat.shape[0] == 0:
            return []

        probs = self._session.run(None, {"feat": feat[np.newaxis, :, :]})[0]
        speech_probs = probs[0, :, 0]
        return regions_from_probs(speech_probs, self.threshold, self.min_silence_ms)


def _extract_features(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """80-bin Kaldi fbank + CMVN, matching upstream's `AudioFeat.extract`."""
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0
    opts.frame_opts.frame_length_ms = 25
    opts.frame_opts.frame_shift_ms = 10
    opts.frame_opts.samp_freq = VAD_SAMPLE_RATE
    opts.mel_opts.num_bins = _NUM_MEL_BINS

    fbank = knf.OnlineFbank(opts)
    scaled = (audio.astype(np.float32) * _INT16_SCALE).tolist()
    fbank.accept_waveform(sample_rate, scaled)

    if fbank.num_frames_ready == 0:
        return np.zeros((0, _NUM_MEL_BINS), dtype=np.float32)

    frames = np.vstack([fbank.get_frame(i) for i in range(fbank.num_frames_ready)])
    means = np.array(_CMVN_MEANS, dtype=np.float32)
    inverse_std = np.array(_CMVN_INVERSE_STD, dtype=np.float32)
    return ((frames - means) * inverse_std).astype(np.float32)


def regions_from_probs(
    speech_probs: np.ndarray, threshold: float, min_silence_ms: int
) -> list[SpeechRegion]:
    """Threshold + hysteresis.

    A region only closes after `min_silence_ms` of consecutive sub-threshold
    frames -- short dips (a breath, a plosive) don't fragment one utterance
    into several regions.
    """
    min_silence_frames = max(1, round(min_silence_ms / 1000 / _FRAME_SHIFT_S))
    regions: list[SpeechRegion] = []
    speech_start: int | None = None
    last_speech_frame = 0
    silence_run = 0

    for frame_index, prob in enumerate(speech_probs):
        if prob >= threshold:
            if speech_start is None:
                speech_start = frame_index
            last_speech_frame = frame_index
            silence_run = 0
        elif speech_start is not None:
            silence_run += 1
            if silence_run >= min_silence_frames:
                regions.append(
                    SpeechRegion(
                        speech_start * _FRAME_SHIFT_S,
                        (last_speech_frame + 1) * _FRAME_SHIFT_S,
                    )
                )
                speech_start = None
                silence_run = 0

    if speech_start is not None:
        regions.append(
            SpeechRegion(
                speech_start * _FRAME_SHIFT_S,
                (last_speech_frame + 1) * _FRAME_SHIFT_S,
            )
        )
    return regions
