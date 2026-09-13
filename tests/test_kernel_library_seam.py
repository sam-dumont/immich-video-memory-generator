"""One module imports the GPU kernel library, and an absent one is not a crash.

Both halves matter. The package used to import its kernel library from five
places, which is what made replacing it a five-file change instead of a one-line
one (#558); and the library has no wheel for macOS x86_64 or Python 3.14, where
the right answer is PIL title screens rather than a traceback.
"""

from __future__ import annotations

import builtins
import importlib
from pathlib import Path

import pytest

from immich_memories.titles import gpu_kernel_backend

_TITLES = Path(gpu_kernel_backend.__file__).parent
_SEAM = Path(gpu_kernel_backend.__file__).name


def test_only_the_seam_names_the_kernel_library() -> None:
    """Every other module takes `ti` from the seam, so the next swap is one line."""
    importers = sorted(
        path.name
        for path in _TITLES.glob("*.py")
        if f"import {gpu_kernel_backend.KERNEL_LIBRARY}" in path.read_text()
    )

    assert importers == [_SEAM]


def test_the_banner_variables_are_set_before_the_runtime_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The C++ banner lands on stdout and corrupts the Rich Live display."""
    for name in gpu_kernel_backend._SILENT_BANNERS:
        monkeypatch.delenv(name, raising=False)

    gpu_kernel_backend._silence_kernel_banners()

    assert all(
        os_value == value
        for name, value in gpu_kernel_backend._SILENT_BANNERS.items()
        if (os_value := __import__("os").environ.get(name)) is not None
    )


@pytest.fixture
def reloaded_seam(monkeypatch: pytest.MonkeyPatch):
    """Re-execute the seam, then put the real one back whatever happens.

    The undo is explicit: fixtures tear down in reverse setup order, so the
    restoring reload would otherwise run while the import is still broken and
    leave every later test in this session without a kernel library.
    """
    yield lambda: importlib.reload(gpu_kernel_backend)
    monkeypatch.undo()
    importlib.reload(gpu_kernel_backend)


def _import_failure(monkeypatch: pytest.MonkeyPatch, error: ModuleNotFoundError) -> None:
    # WHY: whether a wheel exists for this platform is the boundary the seam reads.
    real = builtins.__import__

    def _import(module, *args, **kwargs):
        if module == gpu_kernel_backend.KERNEL_LIBRARY:
            raise error
        return real(module, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)


def test_a_platform_with_no_wheel_gets_pil_titles_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, reloaded_seam, caplog: pytest.LogCaptureFixture
) -> None:
    _import_failure(
        monkeypatch,
        ModuleNotFoundError("No module named 'quadrants'", name="quadrants"),
    )

    with caplog.at_level("INFO"):
        seam = reloaded_seam()

    assert (seam.ti, seam.KERNELS_AVAILABLE) == (None, False)
    assert "PIL renderer" in caplog.text


def test_a_broken_install_still_raises(monkeypatch: pytest.MonkeyPatch, reloaded_seam) -> None:
    """A library that cannot load its own runtime is a machine to fix, not a fallback."""
    _import_failure(
        monkeypatch,
        ModuleNotFoundError("No module named 'quadrants_python'", name="quadrants_python"),
    )

    with pytest.raises(ModuleNotFoundError, match="quadrants_python"):
        reloaded_seam()
