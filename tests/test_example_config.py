"""The shipped example config has to load, not just read well.

It was broken for a while by `output.resolution: auto`, which no schema has
ever accepted, and nothing noticed because nothing loaded the file.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.config import Config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "config.example.yaml"
GRADED_READER = "mlx-community/gemma-4-e4b-it-6bit"


def test_example_config_loads_and_names_the_graded_reader(monkeypatch, tmp_path: Path) -> None:
    # WHY: the loader resolves paths and defaults against the user's home, so a
    # real ~/.immich-memories would decide what this test sees.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    config = Config.from_yaml(EXAMPLE)

    assert config.llm.model == GRADED_READER
    # The example writes the editorial block under `advanced:`; it is flat at runtime.
    assert config.editorial.preparation.caption_base_url == "http://localhost:8092/v1"
