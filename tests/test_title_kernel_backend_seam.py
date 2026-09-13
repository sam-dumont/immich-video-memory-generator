"""Which kernel library the title renderer compiles against, and why.

Taichi 1.7.4 is terminal upstream (#558). Quadrants is a live fork that exposes
the same `ti.*` surface, so the whole choice is one import — these tests pin
where that decision is read from and what happens when it cannot be honoured.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from immich_memories.titles import gpu_kernel_backend, kernel_backend_choice


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the developer's shell nor their config decides these tests."""
    monkeypatch.delenv(kernel_backend_choice.KERNEL_BACKEND_ENV_VAR, raising=False)
    _use_config(monkeypatch, "taichi")


def _use_config(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    # WHY: the on-disk config file is the boundary. Everything else here is
    # this module's own logic.
    import immich_memories.config as config_module

    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: SimpleNamespace(hardware=SimpleNamespace(title_kernel_backend=backend)),
    )


def test_taichi_is_what_you_get_when_nobody_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    assert kernel_backend_choice.requested_kernel_backend() == "taichi"


def test_the_config_key_picks_quadrants(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(monkeypatch, "quadrants")

    assert kernel_backend_choice.requested_kernel_backend() == "quadrants"


def test_the_env_var_picks_quadrants_for_one_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(kernel_backend_choice.KERNEL_BACKEND_ENV_VAR, "quadrants")

    assert kernel_backend_choice.requested_kernel_backend() == "quadrants"


def test_the_env_var_beats_the_config_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(monkeypatch, "quadrants")
    monkeypatch.setenv(kernel_backend_choice.KERNEL_BACKEND_ENV_VAR, "taichi")

    assert kernel_backend_choice.requested_kernel_backend() == "taichi"


def test_a_backend_nobody_ships_is_not_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(kernel_backend_choice.KERNEL_BACKEND_ENV_VAR, "warp")

    assert kernel_backend_choice.requested_kernel_backend() == "taichi"


def test_an_unreadable_config_still_renders_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken config file must not take the title renderer down with it."""
    import immich_memories.config as config_module

    def _explode() -> None:
        raise OSError("config.yaml is a directory")

    monkeypatch.setattr(config_module, "get_config", _explode)

    assert kernel_backend_choice.requested_kernel_backend() == "taichi"


def _fake_import(monkeypatch: pytest.MonkeyPatch, missing: set[str]) -> None:
    """Make `missing` unimportable and hand back a stand-in for the rest.

    Nothing here imports either library for real, and it must stay that way:
    Taichi and Quadrants both register the same options with their own copy of
    LLVM, so a process that imports both dies on the second one.
    """

    # WHY: which distributions are installed is the boundary this seam reads.
    def _import(name: str) -> SimpleNamespace:
        if name in missing:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return SimpleNamespace(__name__=name)

    monkeypatch.setattr(gpu_kernel_backend.importlib, "import_module", _import)


def test_a_missing_quadrants_falls_back_to_taichi(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _fake_import(monkeypatch, {"quadrants"})

    with caplog.at_level("WARNING"):
        module, name = gpu_kernel_backend.load_kernel_library("quadrants")

    assert (module.__name__, name) == ("taichi", "taichi")
    assert "quadrants" in caplog.text.lower()


def test_the_requested_library_is_the_one_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_import(monkeypatch, set())

    module, name = gpu_kernel_backend.load_kernel_library("quadrants")

    assert (module.__name__, name) == ("quadrants", "quadrants")


def test_no_kernel_library_at_all_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Titles fall back to the PIL renderer; the seam must say so, not raise."""
    _fake_import(monkeypatch, {"quadrants", "taichi"})

    module, name = gpu_kernel_backend.load_kernel_library("quadrants")

    assert (module, name) == (None, None)


def test_a_broken_library_is_not_quietly_swapped_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed library that cannot load its runtime is a machine to fix."""

    def _import(name: str):
        raise ModuleNotFoundError("No module named 'quadrants_python'", name="quadrants_python")

    monkeypatch.setattr(gpu_kernel_backend.importlib, "import_module", _import)

    with pytest.raises(ModuleNotFoundError, match="quadrants_python"):
        gpu_kernel_backend.load_kernel_library("quadrants")
