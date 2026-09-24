"""`titles fonts --install` writes only files that hash to their pins."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from immich_memories.titles import script_fonts
from immich_memories.titles.script_fonts import (
    SCRIPT_FONTS,
    ScriptFont,
    install_script_fonts,
    installed_script_fonts,
)

_BODY = b"\x00\x01\x00\x00 a pinned face"
_PIN = ScriptFont("NotoSansHebrew-Bold.ttf", hashlib.sha256(_BODY).hexdigest(), len(_BODY))


@pytest.fixture
def one_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: the real table is 43 MB of downloads; one pin exercises the same loop.
    monkeypatch.setattr(script_fonts, "SCRIPT_FONTS", (_PIN,))


@pytest.mark.usefixtures("one_pin")
def test_a_file_that_matches_its_pin_is_written(tmp_path: Path) -> None:
    written = install_script_fonts(tmp_path, fetch=lambda _url: _BODY)

    assert written == ["NotoSansHebrew-Bold.ttf"]
    assert (tmp_path / "NotoSansHebrew-Bold.ttf").read_bytes() == _BODY


@pytest.mark.usefixtures("one_pin")
def test_a_file_that_does_not_match_is_refused_and_not_written(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pinned SHA-256"):
        install_script_fonts(tmp_path, fetch=lambda _url: b"<html>a proxy error page")

    assert not any(tmp_path.iterdir())


@pytest.mark.usefixtures("one_pin")
def test_a_second_install_fetches_nothing(tmp_path: Path) -> None:
    install_script_fonts(tmp_path, fetch=lambda _url: _BODY)

    def _refuse(url: str) -> bytes:
        raise AssertionError(f"fetched {url} again")

    assert install_script_fonts(tmp_path, fetch=_refuse) == []


@pytest.mark.usefixtures("one_pin")
def test_the_renderer_finds_what_the_install_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(tmp_path))
    install_script_fonts(fetch=lambda _url: _BODY)

    assert installed_script_fonts(bold=True) == (tmp_path / "NotoSansHebrew-Bold.ttf",)
    assert installed_script_fonts(bold=False) == ()


def test_every_pin_is_a_full_sha256_on_a_pinned_url() -> None:
    for font in SCRIPT_FONTS:
        assert len(font.sha256) == 64
        assert "/main/" not in font.url
        assert script_fonts.NOTO_COMMIT in font.url or script_fonts.NOTO_CJK_TAG in font.url


def test_both_weights_of_every_script_are_pinned() -> None:
    regular = {font.file.replace("-Regular.", ".") for font in SCRIPT_FONTS if not font.bold}
    bold = {font.file.replace("-Bold.", ".") for font in SCRIPT_FONTS if font.bold}

    assert regular == bold
