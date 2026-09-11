"""An old config naming a key the editor no longer has is refused, and told which key.

Section models drop unknown keys silently, so without this a file carrying the
legacy scorer's dials would keep loading while every one of them did nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from immich_memories.config_loader import Config, RemovedConfigKeyError


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_a_removed_section_key_is_refused_by_name(tmp_path: Path) -> None:
    path = _write(tmp_path, {"advanced": {"analysis": {"max_refinement_passes": 3}}})

    with pytest.raises(RemovedConfigKeyError) as excinfo:
        Config.from_yaml(path)

    message = str(excinfo.value)
    assert "analysis.max_refinement_passes" in message
    assert str(path) in message


def test_a_removed_top_level_section_is_refused_by_name(tmp_path: Path) -> None:
    path = _write(tmp_path, {"advanced": {"speech": {"enabled": False}}})

    with pytest.raises(RemovedConfigKeyError, match="speech"):
        Config.from_yaml(path)


def test_every_removed_key_in_the_file_is_named_at_once(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "photos": {"max_ratio": 0.25, "duration": 4.0},
            "advanced": {"content_analysis": {"enabled": True}},
        },
    )

    with pytest.raises(RemovedConfigKeyError) as excinfo:
        Config.from_yaml(path)

    message = str(excinfo.value)
    assert "photos.max_ratio" in message
    assert "content_analysis" in message


def test_a_file_without_removed_keys_still_loads(tmp_path: Path) -> None:
    path = _write(tmp_path, {"photos": {"duration": 4.0}, "advanced": {"analysis": {}}})

    assert Config.from_yaml(path).photos.duration == 4.0


def test_the_description_llm_section_is_refused_by_name(tmp_path: Path) -> None:
    """Nothing read it: descriptions come from `editorial.description_model`."""
    path = _write(tmp_path, {"description_llm": {"model": "student", "base_url": "http://x/v1"}})

    with pytest.raises(RemovedConfigKeyError, match="description_llm"):
        Config.from_yaml(path)
