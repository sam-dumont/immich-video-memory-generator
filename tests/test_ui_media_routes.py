"""Media routes must not outlive the element that needed them.

`app.add_media_file()` registers a FastAPI route and never removes it. NiceGUI
does not dedupe, so the same file registered twice yields two routes. Step 2
registered one per clip preview on every render, which meant the route table
grew for the lifetime of the process -- and Starlette matches routes linearly on
every request, so every page in the app pays for it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from nicegui import core, ui


@pytest.fixture(scope="module")
def media_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=5:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


def test_a_video_element_releases_its_route_when_deleted(media_file: Path):
    """Handing ui.video a Path ties the route's lifetime to the element."""
    before = len(core.app.routes)

    element = ui.video(media_file)
    assert len(core.app.routes) == before + 1, "no media route was registered"

    element.delete()

    assert len(core.app.routes) == before, "the media route outlived its element"


def test_registering_the_file_directly_leaks_the_route(media_file: Path):
    """The behaviour the pages used to rely on, kept here as the contrast.

    Nothing removes this route, which is why no page may call add_media_file.
    """
    before = len(core.app.routes)

    core.app.add_media_file(local_file=media_file)

    assert len(core.app.routes) == before + 1
    core.app.remove_route(core.app.routes[-1].path)


def test_no_ui_page_registers_media_files_by_hand():
    """A regression guard: the leak returns the moment a page calls this again."""
    pages = Path(__file__).resolve().parent.parent / "src" / "immich_memories" / "ui"
    offenders = [
        f"{path.relative_to(pages.parent.parent)}:{i + 1}"
        for path in pages.rglob("*.py")
        for i, line in enumerate(path.read_text().splitlines())
        if "add_media_file" in line
    ]

    assert offenders == [], f"pass the Path to ui.video instead: {offenders}"


class TestMusicPreviewCleanup:
    """Each "Generate music" click wrote a full mix plus four stems into a fresh
    temp dir -- 50-300 MB -- and nothing ever removed them, so they accumulated
    in the container's /tmp for as long as the process lived.
    """

    @staticmethod
    def _preview_dir(tmp_path: Path, name: str) -> Path:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "mix.wav").write_bytes(b"x" * 1024)
        return directory

    def test_discarding_removes_the_previous_preview(self, tmp_path: Path):
        from immich_memories.ui.state import AppState

        state = AppState()
        directory = self._preview_dir(tmp_path, "preview-1")
        state.music_preview_dir = directory
        state.music_preview_result = object()

        state.discard_music_preview()

        assert not directory.exists()
        assert state.music_preview_dir is None
        assert state.music_preview_result is None

    def test_discarding_twice_is_harmless(self, tmp_path: Path):
        """Called on reset as well as regenerate, so it must tolerate repeats."""
        from immich_memories.ui.state import AppState

        state = AppState()
        state.music_preview_dir = self._preview_dir(tmp_path, "preview-2")

        state.discard_music_preview()
        state.discard_music_preview()

    def test_a_dir_removed_underneath_us_does_not_raise(self, tmp_path: Path):
        from immich_memories.ui.state import AppState

        state = AppState()
        state.music_preview_dir = tmp_path / "never-created"

        state.discard_music_preview()

    def test_resetting_clips_discards_the_preview(self, tmp_path: Path):
        """Changing the selection invalidates music generated for the old one."""
        from immich_memories.ui.state import AppState

        state = AppState()
        directory = self._preview_dir(tmp_path, "preview-3")
        state.music_preview_dir = directory

        state.reset_clips()

        assert not directory.exists()
