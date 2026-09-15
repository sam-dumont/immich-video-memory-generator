"""The installed local audio environment takes precedence over API fallback."""

import io
import json
import subprocess
import sys
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from immich_memories.audio.generators.ace_step_backend import ACEStepBackend, ACEStepConfig
from immich_memories.audio.generators.base import GenerationRequest, GenerationResult


async def test_lib_generation_uses_the_installed_audio_environment(tmp_path, monkeypatch):
    from immich_memories.audio.generators import ace_step_isolated

    # WHY: replace the installed interpreter and expensive GPU generation boundary.
    monkeypatch.setattr(ace_step_isolated, "isolated_python", lambda: tmp_path / "python")
    generated = GenerationResult(tmp_path / "track.wav", metadata={"mode": "lib"})
    monkeypatch.setattr(ace_step_isolated, "generate_isolated", AsyncMock(return_value=generated))
    backend = ACEStepBackend(ACEStepConfig(mode="lib"))
    assert await backend.is_available()
    result = await backend.generate(GenerationRequest(output_dir=tmp_path))
    assert result == generated


def test_discovery_avoids_recursing_into_the_same_environment(tmp_path, monkeypatch):
    from immich_memories.audio.generators.ace_step_isolated import isolated_python

    python = tmp_path / ".venv-acestep" / "bin" / "python"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    assert isolated_python() is None
    python.parent.mkdir(parents=True)
    python.touch()
    assert isolated_python() == python
    monkeypatch.setattr(sys, "prefix", str(python.parent.parent))
    assert isolated_python() is None


@pytest.mark.parametrize("mode,exists", [("lib", True), ("api", True), ("lib", False)])
async def test_isolated_job_requires_local_audio_and_omits_api_credentials(
    tmp_path, monkeypatch, mode, exists
):
    from immich_memories.audio.generators.ace_step_isolated import generate_isolated

    track = tmp_path / "track.wav"
    if exists:
        track.write_bytes(b"audio")

    def run(command, *, input, **kwargs):
        payload = json.loads(input)
        assert payload["request"]["duration_seconds"] == 31
        assert "api_key" not in payload["config"]["extra_args"]
        from pathlib import Path

        Path(command[-1]).write_text(
            json.dumps({"audio_path": str(track), "metadata": {"mode": mode}})
        )

    # WHY: replace the child process, which normally loads large GPU models and writes its result.
    monkeypatch.setattr(subprocess, "run", run)
    config = ACEStepConfig(mode="lib", extra_args={"api_key": "test-not-a-real-key"})
    request = GenerationRequest(duration_seconds=31, output_dir=tmp_path)
    if mode == "lib" and exists:
        result = await generate_isolated(tmp_path / "python", config, request)
        assert result.audio_path == track
    else:
        with pytest.raises(RuntimeError, match="library-generated"):
            await generate_isolated(tmp_path / "python", config, request)


async def test_worker_runs_the_library_without_api_fallback(tmp_path, monkeypatch):
    from immich_memories.audio.generators.ace_step_isolated import _worker

    track = tmp_path / "track.wav"
    payload = {
        "config": asdict(ACEStepConfig(mode="lib")),
        "request": asdict(GenerationRequest(output_dir=tmp_path)),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, default=str)))
    # WHY: substitute only GPU inference; request decoding and result publication remain real.
    monkeypatch.setattr(
        ACEStepBackend,
        "_generate_lib",
        AsyncMock(return_value=GenerationResult(track, metadata={"mode": "lib"})),
    )
    destination = tmp_path / "result.json"
    await _worker(destination)
    assert json.loads(destination.read_text())["audio_path"] == str(track)
