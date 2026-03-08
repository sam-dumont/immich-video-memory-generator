"""MusicGen API backend.

Wraps the existing MusicGenClient to conform to the MusicGenerator interface.
This backend communicates with an external MusicGen API server.
"""

from __future__ import annotations

import logging
from typing import Any

from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)

logger = logging.getLogger(__name__)


class MusicGenBackend(MusicGenerator):
    """Music generation via external MusicGen API server.

    This wraps the existing MusicGenClient (from music_generator.py)
    behind the MusicGenerator interface.

    Args:
        config: MusicGenClientConfig or compatible config object with
                base_url, api_key, timeout_seconds, etc.
    """

    def __init__(self, config=None):
        # Import here to avoid circular deps - this module is the bridge
        from immich_memories.audio.music_generator import (
            MusicGenClient,
            MusicGenClientConfig,
        )

        self._config = config or MusicGenClientConfig()
        self._client: MusicGenClient | None = None

    @property
    def name(self) -> str:
        return "MusicGen"

    async def __aenter__(self):
        from immich_memories.audio.music_generator import MusicGenClient

        self._client = MusicGenClient(self._config)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.__aexit__(*args)
            self._client = None

    async def is_available(self) -> bool:
        """Check if the MusicGen API server is reachable."""
        if self._client is None:
            from immich_memories.audio.music_generator import MusicGenClient

            async with MusicGenClient(self._config) as client:
                try:
                    await client.health_check()
                    return True
                except Exception:
                    return False
        try:
            await self._client.health_check()
            return True
        except Exception:
            return False

    async def generate(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music via MusicGen API."""
        if self._client is None:
            raise RuntimeError("Backend not initialized. Use 'async with' context.")

        request.output_dir.mkdir(parents=True, exist_ok=True)

        if request.is_multi_scene:
            audio_path = await self._client.generate_soundtrack(
                base_prompt=request.prompt,
                scenes=request.scenes,
                output_dir=request.output_dir,
                progress_callback=progress_callback,
                crossfade_duration=request.crossfade_duration,
            )
        else:
            audio_path = await self._client.generate_music(
                prompt=request.prompt,
                duration=request.duration_seconds,
                output_dir=request.output_dir,
                progress_callback=progress_callback,
            )

        return GenerationResult(
            audio_path=audio_path,
            duration_seconds=request.duration_seconds,
            prompt=request.prompt,
            backend_name=self.name,
        )

    async def generate_with_stems(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> tuple[GenerationResult, Any]:
        """Generate music and separate stems via MusicGen API."""
        result = await self.generate(request, progress_callback)

        if self._client is None:
            return result, None

        stems = await self._client.separate_stems(
            result.audio_path,
            output_dir=request.output_dir,
            progress_callback=progress_callback,
        )

        return result, stems

    async def health_check(self) -> dict[str, Any]:
        """Get detailed MusicGen API health info."""
        if self._client is None:
            return {"backend": self.name, "available": False, "error": "not initialized"}
        try:
            health = await self._client.health_check()
            return {
                "backend": self.name,
                "available": True,
                "device": health.get("device", "unknown"),
                "status": health.get("status", "unknown"),
            }
        except Exception as e:
            return {"backend": self.name, "available": False, "error": str(e)}
