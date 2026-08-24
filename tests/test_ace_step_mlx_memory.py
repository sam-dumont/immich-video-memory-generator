"""MLX VAE decode sizing on Apple Silicon.

Clamping the decode chunk to the 16 GB value on a large-memory Mac shrinks the
temporal context each decode window sees and multiplies the number of blended
window boundaries, which audibly smears the result. The cache limit alone is
what prevents the runaway allocation; a 300 s render peaks at ~38 GiB with the
upstream chunk size.
"""

from __future__ import annotations

import pytest

from immich_memories.audio.generators.ace_step_runtime import _bound_mlx_memory

ENV_VAR = "ACESTEP_MLX_VAE_CHUNK"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_the_decode_chunk_is_left_to_ace_step_by_default():
    """ACE-Step sizes the chunk from available unified memory; do not override it."""
    assert _bound_mlx_memory() is None
    import os

    assert ENV_VAR not in os.environ


def test_an_explicit_override_is_still_honoured(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "512")

    assert _bound_mlx_memory() == 512


def test_a_nonsense_override_falls_back_to_ace_steps_own_sizing(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "not-a-number")

    assert _bound_mlx_memory() is None
