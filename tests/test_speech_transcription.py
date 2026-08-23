"""Speech transcription: config, language resolution, and the whisper.cpp adapter."""

from __future__ import annotations

import math
import sys
from unittest.mock import patch

import numpy as np

from immich_memories.config_loader import Config
from immich_memories.config_models_analysis import TranscriptionConfig
from immich_memories.speech.transcription import (
    Transcript,
    WhisperCppTranscriber,
    is_repetition_loop,
    resolve_language,
    select_transcriber,
    strip_non_speech_markers,
)


class FakeSegment:
    def __init__(self, text: str, probability: float):
        self.text = text
        self.probability = probability


class FakeModel:
    """# WHY: replaces the pywhispercpp Model, an external ML model that would
    otherwise download ~148 MB of weights and run inference in a unit test."""

    def __init__(self, segments=None, lang_probs=None):
        self._segments = segments if segments is not None else [FakeSegment("bonjour", 0.9)]
        self._lang_probs = lang_probs or {"fr": 0.7, "en": 0.2, "ja": 0.1}
        self.transcribe_calls: list[dict] = []
        self.detect_calls = 0

    def transcribe(self, audio, **params):
        self.transcribe_calls.append(params)
        return self._segments

    def auto_detect_language(self, audio):
        self.detect_calls += 1
        best = max(self._lang_probs, key=lambda k: self._lang_probs[k])
        return (best, self._lang_probs[best]), self._lang_probs


def _audio(seconds: float = 3.0) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def test_transcription_defaults_to_off_with_no_languages():
    """The feature ships inert: no languages configured means no transcripts."""
    config = TranscriptionConfig()

    assert config.enabled is False
    assert config.languages == []


def test_transcription_is_a_tier2_section_on_config():
    """advanced.transcription in YAML must land flat on Config at runtime."""
    config = Config()

    assert isinstance(config.transcription, TranscriptionConfig)


def test_transcription_reads_from_the_advanced_block(tmp_path):
    """A Tier 2 section is written under `advanced:` and flattened on load."""
    path = tmp_path / "config.yaml"
    path.write_text("advanced:\n  transcription:\n    enabled: true\n    languages: [fr, en]\n")

    config = Config.from_yaml(path)

    assert config.transcription.enabled is True
    assert config.transcription.languages == ["fr", "en"]


def test_resolve_language_ignores_the_global_winner_outside_the_configured_set():
    """The reason the language config exists.

    Whisper's own top-1 detection put French audio in Japanese and in German, two
    attempts out of two. Restricting the argmax to the languages the library
    actually contains turns a 99-way guess into a two-way decision.
    """
    lang_probs = {"ja": 0.55, "de": 0.20, "fr": 0.15, "en": 0.10}

    assert resolve_language(lang_probs, ["fr", "en"]) == "fr"


def test_resolve_language_returns_none_when_nothing_is_configured():
    assert resolve_language({"fr": 0.9}, []) is None


def test_resolve_language_ignores_codes_the_model_never_scored():
    """A configured code absent from the probability mapping is not a candidate."""
    assert resolve_language({"fr": 0.4, "en": 0.6}, ["xx", "fr"]) == "fr"


def test_strip_non_speech_markers_removes_bracketed_annotations():
    assert strip_non_speech_markers("[Music] hello there (sighs)") == "hello there"


def test_strip_non_speech_markers_empties_a_marker_only_transcript():
    """whisper.cpp answers [BLANK_AUDIO] on silence; that is not a transcript."""
    assert strip_non_speech_markers("[BLANK_AUDIO]") == ""


def test_single_configured_language_is_forced_without_detection():
    """One language is a property of the library, not a question for the model."""
    model = FakeModel()
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"]), model=model
    )

    result = transcriber.transcribe(_audio())

    assert model.detect_calls == 0, "detection must be skipped entirely"
    assert model.transcribe_calls[0]["language"] == "fr"
    assert result == Transcript(text="bonjour", language="fr", confidence=0.9)


def test_several_configured_languages_detect_within_the_set():
    model = FakeModel(lang_probs={"ja": 0.6, "fr": 0.3, "en": 0.1})
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr", "en"]), model=model
    )

    result = transcriber.transcribe(_audio())

    assert model.detect_calls == 1
    assert model.transcribe_calls[0]["language"] == "fr"
    assert result is not None
    assert result.language == "fr"


def test_low_confidence_transcript_is_discarded():
    """whisper.cpp exposes no no_speech_prob, so mean token probability is the gate."""
    model = FakeModel(segments=[FakeSegment("mmm", 0.3)])
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"], min_confidence=0.6), model=model
    )

    assert transcriber.transcribe(_audio()) is None


def test_marker_only_transcript_is_discarded():
    model = FakeModel(segments=[FakeSegment("[BLANK_AUDIO]", 0.95)])
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"]), model=model
    )

    assert transcriber.transcribe(_audio()) is None


def test_confidence_is_the_unweighted_mean_of_segment_probabilities():
    """Weighting by duration would mean reading t0/t1, which this design refuses."""
    model = FakeModel(segments=[FakeSegment("un", 0.8), FakeSegment("deux", 1.0)])
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"]), model=model
    )

    result = transcriber.transcribe(_audio())

    assert result is not None
    assert math.isclose(result.confidence, 0.9)
    assert result.text == "un deux"


def test_select_transcriber_returns_none_when_disabled():
    assert select_transcriber(TranscriptionConfig(enabled=False, languages=["fr"])) is None


def test_select_transcriber_returns_none_without_languages():
    assert select_transcriber(TranscriptionConfig(enabled=True, languages=[])) is None


def test_select_transcriber_returns_none_without_pywhispercpp():
    """The extra is optional: absent means no transcripts, not a crash."""
    # WHY: replaces the pywhispercpp import itself. A None entry in sys.modules
    # makes `from pywhispercpp.model import Model` raise ImportError, which is what
    # a machine without the extra installed produces.
    with patch.dict(sys.modules, {"pywhispercpp": None, "pywhispercpp.model": None}):
        assert select_transcriber(TranscriptionConfig(enabled=True, languages=["fr"])) is None


def test_repetition_loop_detected_from_a_repeated_phrase():
    """Real output from the library: whisper looped one phrase three times.

    `no_context=True` does not prevent this -- the loop happens inside a single
    decode window, not across windows.
    """
    text = (
        "Je vais vous faire un petit peu. Je vais vous faire un petit peu. "
        "Je vais vous faire un petit peu."
    )
    assert is_repetition_loop(text) is True


def test_repetition_loop_detected_from_a_doubled_fragment():
    """Real output: a child babbling, transcribed as one fragment twice."""
    assert is_repetition_loop("- La, bi, ho. - La, bi, ho.") is True


def test_repetition_loop_detected_from_a_stuttered_word():
    """Real output: one word emitted three times in a row."""
    assert is_repetition_loop("Tu te te te") is True


def test_short_genuine_repetition_is_not_a_loop():
    """ "Merci, merci." and "No. No!" are real utterances, not loops."""
    assert is_repetition_loop("Merci, merci.") is False
    assert is_repetition_loop("No. No!") is False


def test_ordinary_speech_is_not_a_loop():
    assert is_repetition_loop("Tu fais quoi ?") is False
    assert is_repetition_loop("Les enfants jouent sur la plage au bord de la mer.") is False


def test_looping_transcript_is_discarded_by_the_transcriber():
    """A confident loop must not survive: these arrive at conf 0.90+."""
    model = FakeModel(
        segments=[FakeSegment("bonjour bonjour bonjour bonjour", 0.94)],
    )
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"]), model=model
    )

    assert transcriber.transcribe(_audio()) is None


def test_strip_non_speech_markers_removes_asterisk_annotations():
    """Real output: whisper labelled crowd noise rather than inventing speech."""
    assert strip_non_speech_markers("*bruits de foule*") == ""


def test_strip_non_speech_markers_removes_music_notes():
    """Real output: whisper wraps sung content in music notes."""
    assert strip_non_speech_markers("♪ I'm a champion ♪ ♪ Champion ♪") == ""


def test_strip_non_speech_markers_keeps_speech_around_an_annotation():
    assert strip_non_speech_markers("*rires* Tu fais quoi ?") == "Tu fais quoi ?"


def test_punctuation_only_transcript_is_discarded():
    """Real output: on digital silence whisper returns '...' at confidence 0.83.

    The marker stripper leaves it alone (no brackets), the loop detector sees no
    words, and min_confidence no longer catches it by accident.
    """
    model = FakeModel(segments=[FakeSegment("...", 0.83)])
    transcriber = WhisperCppTranscriber(
        TranscriptionConfig(enabled=True, languages=["fr"], min_confidence=0.0), model=model
    )

    assert transcriber.transcribe(_audio()) is None


def test_defaults_match_the_measured_operating_point():
    """base produced "- Dear." and "La papa." where medium produced whole sentences.

    The confidence floor defaults to 0.0 because it is inverted on this audio:
    correct transcripts measured 0.63-0.71, fluent nonsense 0.84-0.95.
    """
    config = TranscriptionConfig()

    assert config.model == "medium"
    assert config.min_confidence == 0.0
    assert config.min_voiced_seconds == 1.0, "the voice-activity gate still filters"
