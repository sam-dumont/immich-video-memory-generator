"""Adapter boundary between the worker lifecycle and the application's renderer."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from immich_memories.processing.encoding_plan import EncodingPlan
from immich_memories.processing.output_contract import OutputProbe
from immich_memories_render_worker.models import RenderRequest


@dataclass(frozen=True)
class RenderArtifact:
    path: Path
    encoding_plan: EncodingPlan
    probe: OutputProbe | None = None
    # The assembler is the only place the final sequence and its transitions
    # coexist, so a caller mixing music later cannot work these out itself.
    music_mute_windows: list[tuple[float, float]] | None = None
    clips: tuple[dict, ...] = ()
    degradations: tuple[str, ...] = field(default=())


class Renderer(Protocol):
    def health(self) -> dict: ...
    def render(
        self, request: RenderRequest, directory: Path, progress: Callable[[str, float, str], None]
    ) -> RenderArtifact: ...
