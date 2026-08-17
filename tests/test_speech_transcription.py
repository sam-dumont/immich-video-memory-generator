"""Speech transcription: config, language resolution, and the whisper.cpp adapter."""

from __future__ import annotations

from immich_memories.config_loader import Config
from immich_memories.config_models import TranscriptionConfig


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
