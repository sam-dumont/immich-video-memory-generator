"""Music and render never hand a model a picture: the mood comes from banked text."""

import ast
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from immich_memories.config_loader import Config
from tests.no_pictures import refuse_pictures

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "immich_memories"
# Everything that runs after the cut is chosen: rendering, titles and music.
_FILM_TIME = ("audio", "processing", "titles", "generate_music.py", "cli/music_cmd.py")


def _calls_with_pictures(path: Path) -> list[str]:
    empty = (ast.Tuple, ast.List)
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            value = keyword.value
            if keyword.arg != "images":
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            if isinstance(value, empty) and not value.elts:
                continue
            found.append(f"{path.relative_to(_PACKAGE)}:{node.lineno}")
    return found


def test_no_render_or_music_module_passes_pictures_to_a_model_call():
    files = []
    for entry in _FILM_TIME:
        root = _PACKAGE / entry
        files.extend(sorted(root.rglob("*.py")) if root.is_dir() else [root])

    offenders = [hit for path in files for hit in _calls_with_pictures(path)]

    assert files
    assert offenders == []


def _video(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=32x32:r=2:d=1"]
        + ["-pix_fmt", "yuv420p", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def test_music_add_without_a_mood_sends_no_picture_even_with_a_reader(tmp_path, monkeypatch):
    from immich_memories.cli import main

    sent = refuse_pictures(monkeypatch)
    config = Config()
    config.llm.model = "reader"
    config.llm.base_url = "http://127.0.0.1:9/v1"
    config.audio.local_music_dir = str(tmp_path / "library")
    video = _video(tmp_path / "input.mp4")
    output = tmp_path / "output.mp4"

    # WHY: init_config_dir and get_config would touch the real $HOME and config file.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        result = CliRunner().invoke(main, ["music", "add", str(video), str(output)])

    assert result.exit_code == 0, result.output
    assert output.exists()
    assert sent == []


def test_the_frame_mood_options_are_gone():
    from immich_memories.cli import main

    # WHY: init_config_dir and get_config would touch the real $HOME and config file.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=Config()),
    ):
        analyze = CliRunner().invoke(main, ["music", "analyze", "--help"])
        add = CliRunner().invoke(main, ["music", "add", "--help"])

    assert analyze.exit_code != 0
    assert "--analyze-frames" not in add.output


@pytest.mark.asyncio
async def test_the_cut_mood_reaches_the_reader_as_text_only(tmp_path, monkeypatch):
    from immich_memories.audio.text_mood import mood_for_cut

    sent = refuse_pictures(monkeypatch)
    config = Config(
        tier="full",
        llm={"base_url": "http://localhost:11434", "model": "reader", "provider": "ollama"},
    )
    config.cache.directory = str(tmp_path / "cache")
    (tmp_path / "plan.private.json").write_text(json.dumps({"story": {"thesis": "A fair"}}))
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={
            "response": '{"primary_mood":"playful","energy_level":"high",'
            '"tempo_suggestion":"fast","genre_suggestions":["pop"],"specific_style":null}',
            "done": True,
        },
    )

    # WHY: the reader's HTTP endpoint; the guard above it sees the real request.
    with patch("httpx.AsyncClient.post", return_value=response):
        choice = await mood_for_cut(config, tmp_path, ("kept",))

    assert choice.source == "cut_text"
    assert sent == [0]
