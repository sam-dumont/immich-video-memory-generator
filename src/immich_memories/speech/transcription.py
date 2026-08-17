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
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# whisper.cpp writes its non-speech annotations in brackets or parentheses:
# [Music], [BLANK_AUDIO], (sighs). A result made of nothing else is not speech.
_MARKER_RE = re.compile(r"[\[(][^\])]*[\])]")


@dataclass(frozen=True)
class Transcript:
    """What was said in one segment, in one language, with one confidence."""

    text: str
    language: str
    confidence: float


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> Transcript | None: ...


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
