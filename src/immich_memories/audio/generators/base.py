"""Abstract base for music generation backends.

Defines the interface that all music generation backends must implement.
This allows swapping between MusicGen, ACE-Step, or future backends
without changing the rest of the application.
"""

from __future__ import annotations

import logging
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GenerationRequest:
    """Request to generate music.

    This is the backend-agnostic representation of what music to generate.
    Each backend translates this into its own API/library calls.
    """

    # What to generate
    prompt: str = ""
    scenes: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: int = 60

    # Generation options
    variation_index: int = 0
    crossfade_duration: float = 2.0

    # Output
    output_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "musicgen")

    @property
    def is_multi_scene(self) -> bool:
        """Whether this is a multi-scene soundtrack request."""
        return len(self.scenes) > 1


@dataclass
class GenerationResult:
    """Result from a music generation backend."""

    audio_path: Path
    duration_seconds: float = 0.0
    prompt: str = ""
    backend_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class MusicGenerator(ABC):
    """Abstract base class for music generation backends.

    All music generation backends (MusicGen API, ACE-Step lib/API, etc.)
    implement this interface. The application code only interacts with
    this interface, never with backend-specific details.

    Usage:
        async with generator:
            result = await generator.generate(request)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g., 'MusicGen', 'ACE-Step')."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this backend is usable (dependencies installed, API reachable, etc.)."""

    @abstractmethod
    async def generate(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music from a request.

        Args:
            request: What music to generate.
            progress_callback: Optional callback(status, progress, detail).

        Returns:
            GenerationResult with path to generated audio.
        """

    async def generate_with_stems(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> tuple[GenerationResult, Any]:
        """Generate music and separate stems.

        Default implementation generates music then returns no stems.
        Backends with stem separation support should override this.

        Returns:
            Tuple of (GenerationResult, MusicStems or None).
        """
        result = await self.generate(request, progress_callback)
        return result, None

    async def health_check(self) -> dict[str, Any]:
        """Detailed health/status info. Override for richer info."""
        available = await self.is_available()
        return {"backend": self.name, "available": available}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):  # noqa: B027
        pass
