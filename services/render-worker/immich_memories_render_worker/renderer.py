"""Adapter boundary between the worker lifecycle and the application's renderer."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from immich_memories.processing.encoding_plan import EncodingPlan
from immich_memories_render_worker.models import RenderRequest


@dataclass(frozen=True)
class RenderArtifact:
    path: Path
    encoding_plan: EncodingPlan


class Renderer(Protocol):
    def health(self) -> dict: ...
    def render(
        self, request: RenderRequest, directory: Path, progress: Callable[[str, float, str], None]
    ) -> RenderArtifact: ...
