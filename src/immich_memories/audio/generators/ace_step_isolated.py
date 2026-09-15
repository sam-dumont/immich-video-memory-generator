"""Run ACE-Step's older Transformers stack without changing the editor's dependencies."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.audio.generators.base import GenerationRequest, GenerationResult

if TYPE_CHECKING:
    from immich_memories.audio.generators.ace_step_backend import ACEStepConfig


def isolated_python() -> Path | None:
    """Find the sibling environment installed by make install-acestep."""
    candidate = Path(sys.prefix).parent / ".venv-acestep" / "bin" / "python"
    # Checking the prefix avoids recursion; both environments can symlink one Python binary.
    if candidate.is_file() and candidate.parent.parent != Path(sys.prefix):
        return candidate
    return None


async def generate_isolated(
    python: Path, config: ACEStepConfig, request: GenerationRequest
) -> GenerationResult:
    """Run the existing library backend in the audio environment, with no HTTP fallback."""
    payload = {"config": asdict(config), "request": asdict(request)}
    # API credentials have no role in a local library job.
    payload["config"]["extra_args"] = {
        "cpu_offload": bool(config.extra_args.get("cpu_offload", False))
    }
    with tempfile.TemporaryDirectory(prefix="acestep-result-") as directory:
        result_path = Path(directory) / "result.json"
        await asyncio.to_thread(
            subprocess.run,
            [str(python), "-m", __name__, str(result_path)],
            input=json.dumps(payload, default=str),
            text=True,
            check=True,
            timeout=config.timeout_seconds,
        )
        data = json.loads(result_path.read_text())
    data["audio_path"] = Path(data["audio_path"])
    result = GenerationResult(**data)
    if result.metadata.get("mode") != "lib" or not result.audio_path.is_file():
        raise RuntimeError("Local ACE-Step did not produce a library-generated track")
    return result


async def _worker(result_path: Path) -> None:
    from immich_memories.audio.generators.ace_step_backend import ACEStepBackend, ACEStepConfig

    payload = json.load(sys.stdin)
    config = ACEStepConfig(**payload["config"])
    request = GenerationRequest(**payload["request"])
    request.output_dir = Path(request.output_dir)
    async with ACEStepBackend(config) as backend:
        # Direct call deliberately forbids falling through to a configured API server.
        result = await backend._generate_lib(request)
    result_path.write_text(json.dumps(asdict(result), default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_worker(Path(sys.argv[1])))
