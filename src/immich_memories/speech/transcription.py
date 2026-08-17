"""Speech transcription for candidate clips.

Transcription consumes an audio *slice* for one segment, never the whole video,
so whisper is only ever asked what was said and never when. Its word timestamps
measured as unusable -- zero inter-word gaps across 30 words, one word spanning
1.56 to 11.66 seconds -- and slicing means no code path can depend on them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from immich_memories.config_models import TranscriptionConfig

logger = logging.getLogger(__name__)

# whisper.cpp writes its non-speech annotations in brackets or parentheses:
# [Music], [BLANK_AUDIO], (sighs). A result made of nothing else is not speech.
_MARKER_RE = re.compile(r"[\[(][^\])]*[\])]")

# Below this, repetition is normal speech: "Merci, merci." and "No. No!" are real
# utterances from the library, not loops.
_MIN_LOOP_WORDS = 4


@dataclass(frozen=True)
class Transcript:
    """What was said in one segment, in one language, with one confidence."""

    text: str
    language: str
    confidence: float


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> Transcript | None: ...


# The injected whisper model is typed `Any` rather than given a Protocol.
# pywhispercpp ships a stub that no honest Protocol matches: its audio parameter
# is `str | ndarray[..., dtype[float32]]`, narrower than `np.ndarray`, so a
# precise annotation fails contravariance; a blanket `**params: Any` promises any
# keyword of any type, which the stub does not offer; and naming the keywords
# individually trips Vulture, which reads a Protocol's keyword-only parameters as
# unused variables. Three gates disagreeing about one seam is not worth a fourth
# workaround -- the two methods used are `transcribe(audio, language=...,
# extract_probability=True)` and `auto_detect_language(audio)`, and the tests pin
# both.
WhisperModel = Any


def resolve_language(lang_probs: Mapping[str, float], configured: Sequence[str]) -> str | None:
    """Most probable language among the ones the library actually contains.

    Whisper's own top choice is never consulted. Detection across all 99
    languages put French audio in Japanese and in German, twice out of two, so
    the languages the library does not contain are not candidates at all.
    """
    candidates = [lang for lang in configured if lang in lang_probs]
    if not candidates:
        return None
    return max(candidates, key=lambda lang: lang_probs[lang])


def strip_non_speech_markers(text: str) -> str:
    """Drop whisper's bracketed non-speech annotations and normalise whitespace."""
    return " ".join(_MARKER_RE.sub(" ", text).split())


def is_repetition_loop(text: str) -> bool:
    """True when the text is whisper looping rather than reporting speech.

    Measured on a real library: loops arrive at confidence 0.90 and above, so the
    token-probability floor never sees them. `no_context=True` does not help --
    the loop happens inside a single decode window, not across windows.

    Two shapes are rejected: the whole text being one block repeated, and a single
    word running three or more times. A doubled two-word-or-longer block counts,
    which does discard the occasional genuine chant; a memory description loses
    little by dropping one, and keeps a lot by dropping the rest.
    """
    words = re.findall(r"\w+", text.lower())
    if len(words) < _MIN_LOOP_WORDS:
        return False
    return _is_repeated_block(words) or _has_stuttered_word(words)


def _is_repeated_block(words: list[str]) -> bool:
    total = len(words)
    for period in range(1, total // 2 + 1):
        if total % period:
            continue
        block = words[:period]
        if any(words[start : start + period] != block for start in range(0, total, period)):
            continue
        repeats = total // period
        if (period == 1 and repeats >= 3) or (period >= 2 and repeats >= 2):
            return True
    return False


def _has_stuttered_word(words: list[str]) -> bool:
    run = 1
    for previous, current in zip(words, words[1:], strict=False):
        run = run + 1 if current == previous else 1
        if run >= 3:
            return True
    return False


def _mean_probability(segments: list) -> float:
    """Unweighted mean of the per-segment token probabilities.

    Unweighted because weighting by duration would mean reading t0/t1. A three-to-
    five second slice usually returns one segment, so there is little to weight.
    """
    values = [
        float(segment.probability)
        for segment in segments
        if segment.probability == segment.probability  # NaN when not extracted
    ]
    if not values:
        return 0.0
    return sum(values) / len(values)


class WhisperCppTranscriber:
    """Transcriber backed by whisper.cpp through pywhispercpp.

    The model is injected so tests never import pywhispercpp, matching how
    SpeechAnalysisService takes its audio analyzer.
    """

    def __init__(self, config: TranscriptionConfig, model: WhisperModel | None = None):
        self._config = config
        self._model = model

    def _load_model(self) -> WhisperModel:
        model = self._model
        if model is None:
            from pywhispercpp.model import Model

            model = Model(
                self._config.model,
                context_params={"use_gpu": self._config.use_gpu},
                # Each of these is set against a pywhispercpp default that is wrong
                # here: print_progress defaults to True and would write a progress
                # line per segment per video, and no_context off lets whisper fall
                # into repetition loops.
                print_progress=False,
                no_context=True,
                temperature=0.0,
            )
            self._model = model
        return model

    def _resolve(self, model: WhisperModel, audio: np.ndarray) -> str | None:
        configured = self._config.languages
        if not configured:
            return None
        if len(configured) == 1:
            return configured[0]
        _, lang_probs = model.auto_detect_language(audio)
        return resolve_language(lang_probs, configured)

    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        model = self._load_model()
        language = self._resolve(model, audio)
        if language is None:
            return None

        segments = model.transcribe(audio, language=language, extract_probability=True)
        if not segments:
            return None

        text = strip_non_speech_markers(" ".join(segment.text for segment in segments))
        if not text:
            return None

        if is_repetition_loop(text):
            logger.debug("Discarding looping transcript: %s", text[:80])
            return None

        confidence = _mean_probability(segments)
        if confidence < self._config.min_confidence:
            logger.debug(
                "Discarding transcript at confidence %.2f (floor %.2f)",
                confidence,
                self._config.min_confidence,
            )
            return None

        return Transcript(text=text, language=language, confidence=confidence)


def select_transcriber(config: TranscriptionConfig) -> Transcriber | None:
    """A whisper.cpp transcriber, or `None` when transcription cannot run.

    `None` is the null implementation: callers skip transcription entirely rather
    than holding an object that always declines.
    """
    if not config.enabled:
        return None

    if not config.languages:
        logger.warning(
            "transcription.enabled is true but transcription.languages is empty -- "
            "no transcripts will be produced. Set the languages your library contains, "
            "e.g. languages: [fr, en]"
        )
        return None

    try:
        from pywhispercpp.model import Model
    except ImportError:
        logger.info(
            "pywhispercpp is not installed -- transcription unavailable "
            "(pip install 'immich-memories[transcribe]')"
        )
        return None

    known = set(Model.available_languages())
    languages = [lang for lang in config.languages if lang in known]
    unknown = [lang for lang in config.languages if lang not in known]
    if unknown:
        logger.warning("Ignoring unknown language codes: %s", ", ".join(unknown))
    if not languages:
        logger.warning("No usable language codes configured -- transcription disabled")
        return None

    return WhisperCppTranscriber(config.model_copy(update={"languages": languages}))
