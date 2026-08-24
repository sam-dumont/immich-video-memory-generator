"""The run's arguments, normalised once instead of at each point of use.

`run_pipeline_and_generate` takes 37 keyword arguments and normalised eight of
them inline, where each was needed. That is not a complexity problem wearing a
gate's costume -- it is a correctness problem the gate happened to notice:
"the photos, if photos are enabled" was written twice, once for duration
resolution and once for canvas sizing, so tightening the rule in one place and
not the other leaves a video sized for photos it never budgeted time for.

Resolving once removes that possibility rather than documenting it. Frozen, so
nothing re-normalises halfway through a generation.

Shaped after `PipelineConfig.from_app_config`, which exists for the same
reason: a constructor whose whole job is that one rule lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

__all__ = ["ResolvedRunInputs"]

# The value that asks the pipeline to choose a track rather than naming one.
_CHOOSE_MUSIC = "auto"


@dataclass(frozen=True)
class ResolvedRunInputs:
    """What the run's raw arguments mean, decided once."""

    photo_assets: list[Any] | None
    has_photos: bool
    attempt_id: str | None
    should_upload: bool
    person_name: str | None
    music_path: Path | None
    preset_params: dict[str, Any]

    @classmethod
    def from_arguments(
        cls,
        *,
        include_photos: bool,
        photo_assets: list[Any] | None,
        dry_run: bool,
        automation_attempt_id: str | None,
        upload_to_immich: bool,
        config: Config,
        person_names: list[str] | None,
        music: str | None,
        memory_preset_params: dict[str, Any] | None,
    ) -> ResolvedRunInputs:
        """Apply every rule the run body used to apply inline."""
        photos = photo_assets if include_photos else None
        return cls(
            photo_assets=photos,
            has_photos=bool(photos),
            # A rehearsal must not mark an automation attempt as spent.
            attempt_id=None if dry_run else automation_attempt_id,
            should_upload=upload_to_immich or config.upload.enabled,
            person_name=person_names[0] if person_names else None,
            # "auto" is a request to choose, not a file to load.
            music_path=(Path(music) if music and music != _CHOOSE_MUSIC else None),
            preset_params=memory_preset_params or {},
        )
