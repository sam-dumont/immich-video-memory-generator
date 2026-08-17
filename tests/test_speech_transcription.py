"""Speech transcription: config, language resolution, and the whisper.cpp adapter."""

from __future__ import annotations

from immich_memories.config_loader import Config
from immich_memories.config_models import TranscriptionConfig
from immich_memories.speech.transcription import (
    resolve_language,
    strip_non_speech_markers,
)


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
