"""The capability matrix must pin settings where the config already spells them.

A tier-2 section may sit at top level (legacy) or under `advanced:`, and the
loader resolves the clash with `if key not in data` — top level wins. Writing
the modern spelling into a config using the old one leaves the edit inert, so
a pin has to land wherever the section already lives.

This is not hypothetical: the first version of the sweep wrote
`advanced.ace_step.enabled: false` into a config carrying a top-level
`ace_step:`, and the row that was supposed to exercise the bundled-music
fallback would have quietly generated music instead — passing, and testing the
wrong thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from capability_matrix import _pinned_config  # noqa: E402

from immich_memories.config_loader import _load_yaml_data  # noqa: E402


def _write(tmp_path: Path, data: dict) -> Path:
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(data))
    return source


def test_a_pin_reaches_a_section_spelled_at_top_level(tmp_path: Path) -> None:
    source = _write(tmp_path, {"immich": {"url": "http://x"}, "ace_step": {"enabled": True}})

    dest = _pinned_config(source, tmp_path / "out.yaml", {"ace_step.enabled": False})

    assert _load_yaml_data(dest)["ace_step"]["enabled"] is False


def test_a_pin_reaches_a_section_spelled_under_advanced(tmp_path: Path) -> None:
    source = _write(
        tmp_path, {"immich": {"url": "http://x"}, "advanced": {"ace_step": {"enabled": True}}}
    )

    dest = _pinned_config(source, tmp_path / "out.yaml", {"ace_step.enabled": False})

    assert _load_yaml_data(dest)["ace_step"]["enabled"] is False


def test_pinning_keeps_the_credentials_the_run_needs(tmp_path: Path) -> None:
    """Every row generates against the real library; a pin must not drop the key."""
    source = _write(tmp_path, {"immich": {"url": "http://x", "api_key": "k"}})

    dest = _pinned_config(source, tmp_path / "out.yaml", {"output.resolution": "1080p"})

    loaded = _load_yaml_data(dest)
    assert loaded["immich"] == {"url": "http://x", "api_key": "k"}
    assert loaded["output"]["resolution"] == "1080p"


def test_a_missing_config_is_named_rather_than_silently_empty(tmp_path: Path) -> None:
    """Starting from `{}` produced a config with no URL and a 0s 'not configured' run."""
    with pytest.raises(SystemExit, match="does not exist"):
        _pinned_config(tmp_path / "absent.yaml", tmp_path / "out.yaml", {})


def test_rows_that_render_the_same_video_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rows produced pixel-identical files and both reported "ok" for 218s.

    A duplicate is a row asserting something the config already sets, so it is a
    silent hole in the sweep rather than a failure anyone would notice.
    """
    import capability_matrix as cm

    same, other = Path("a.mp4"), Path("b.mp4")
    monkeypatch.setattr(cm, "_frame_signature", lambda p: "X" if p != other else "Y")

    assert cm._duplicate_groups([same, Path("c.mp4"), other]) == [["a.mp4", "c.mp4"]]


def test_distinct_renders_are_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import capability_matrix as cm

    monkeypatch.setattr(cm, "_frame_signature", lambda p: p.name)

    assert cm._duplicate_groups([Path("a.mp4"), Path("b.mp4")]) == []
