"""An old config naming a key the editor no longer has still loads, and says so.

Section models drop unknown keys silently, so without this a file carrying the
legacy scorer's dials would keep loading while every one of them did nothing and
nobody was told. The key is dropped before validation and named in one warning:
the value is dead either way, and refusing to start locked an upgrade out of its
own app over a line that no longer means anything.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from immich_memories.config_loader import Config


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_a_removed_section_key_is_named_and_ignored(tmp_path: Path, caplog) -> None:
    path = _write(tmp_path, {"advanced": {"analysis": {"max_refinement_passes": 3}}})

    with caplog.at_level(logging.WARNING):
        config = Config.from_yaml(path)

    assert config is not None
    message = "\n".join(r.getMessage() for r in caplog.records)
    assert "analysis.max_refinement_passes" in message
    assert str(path) in message


def test_a_removed_top_level_section_is_named_and_ignored(tmp_path: Path, caplog) -> None:
    path = _write(tmp_path, {"advanced": {"transcription": {"enabled": False}}})

    with caplog.at_level(logging.WARNING):
        Config.from_yaml(path)

    assert "transcription" in "\n".join(r.getMessage() for r in caplog.records)


def test_speech_settings_are_used_after_the_scorer_migration(tmp_path: Path, caplog) -> None:
    path = _write(tmp_path, {"advanced": {"speech": {"enabled": False}}})
    config = Config.from_yaml(path)
    assert config.speech.enabled is False
    assert not [r for r in caplog.records if "speech" in r.getMessage()]


def test_every_removed_key_in_the_file_is_named_at_once(tmp_path: Path, caplog) -> None:
    path = _write(
        tmp_path,
        {
            "photos": {"max_ratio": 0.25, "duration": 4.0},
            "advanced": {"content_analysis": {"enabled": True}},
        },
    )

    with caplog.at_level(logging.WARNING):
        config = Config.from_yaml(path)

    message = "\n".join(r.getMessage() for r in caplog.records)
    assert "photos.max_ratio" in message
    assert "content_analysis" in message
    # The keys beside them are still read: dropping one must not cost the others.
    assert config.photos.duration == 4.0


def test_a_file_without_removed_keys_loads_without_a_warning(tmp_path: Path, caplog) -> None:
    path = _write(tmp_path, {"photos": {"duration": 4.0}, "advanced": {"analysis": {}}})

    with caplog.at_level(logging.WARNING):
        config = Config.from_yaml(path)

    assert config.photos.duration == 4.0
    assert not [r for r in caplog.records if "no longer exist" in r.getMessage()]


def test_the_description_llm_section_is_named_and_ignored(tmp_path: Path, caplog) -> None:
    """Nothing read it: descriptions come from `editorial.description_model`."""
    path = _write(tmp_path, {"description_llm": {"model": "student", "base_url": "http://x/v1"}})

    with caplog.at_level(logging.WARNING):
        Config.from_yaml(path)

    assert "description_llm" in "\n".join(r.getMessage() for r in caplog.records)
