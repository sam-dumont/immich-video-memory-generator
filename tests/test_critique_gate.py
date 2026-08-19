"""The test-quality gate should see the patches people actually write.

`make critique` only inspected `@patch` decorators. The suite uses `with patch(`
354 times — the majority — and every one was invisible to the gate, so the rule
"every mock must say why" was enforced on a minority of mocks.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "critique_tests.py"


def _load():
    spec = importlib.util.spec_from_file_location("critique_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_sample.py"
    path.write_text(body)
    return path


def test_a_context_manager_patch_without_why_is_flagged(tmp_path) -> None:
    critique = _load()
    sample = _write(
        tmp_path,
        "def test_thing():\n    with patch('a.b.c'):\n        assert True\n",
    )

    assert critique.check_patch_without_why([sample])


def test_a_context_manager_patch_with_why_is_accepted(tmp_path) -> None:
    critique = _load()
    sample = _write(
        tmp_path,
        "def test_thing():\n"
        "    # WHY: replaces the Immich HTTP boundary\n"
        "    with patch('a.b.c'):\n"
        "        assert True\n",
    )

    assert not critique.check_patch_without_why([sample])


def test_a_decorator_patch_still_needs_why(tmp_path) -> None:
    critique = _load()
    sample = _write(tmp_path, "@patch('a.b.c')\ndef test_thing(m):\n    assert True\n")

    assert critique.check_patch_without_why([sample])


def test_stacked_patches_only_need_one_why(tmp_path) -> None:
    """A WHY above the stack explains the group; requiring one per line would
    just teach people to paste the same comment."""
    critique = _load()
    sample = _write(
        tmp_path,
        "# WHY: replaces both network boundaries\n"
        "@patch('a.b.c')\n"
        "@patch('a.b.d')\n"
        "def test_thing(m, n):\n"
        "    assert True\n",
    )

    assert not critique.check_patch_without_why([sample])


def test_the_backlog_ceilings_are_not_above_what_exists(tmp_path) -> None:
    """The ceilings are a ratchet: they freeze today's backlog so it can only
    shrink. If either drifts above the real count the gate has stopped biting,
    which is how it got to 926 unexplained patches in the first place."""
    from pathlib import Path as _Path

    critique = _load()
    tests_dir = _Path(__file__).resolve().parent
    files = sorted(f for f in tests_dir.rglob("test_*.py") if "__pycache__" not in str(f))

    assert len(critique.check_mock_only_assertions(files)) <= critique.MAX_MOCK_ONLY_TESTS
    assert len(critique.check_patch_without_why(files)) <= critique.MAX_PATCHES_WITHOUT_WHY
