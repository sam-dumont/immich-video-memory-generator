"""Pre-assembly clip validation — filter out bad clips before FFmpeg."""

from __future__ import annotations

import logging
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


def validate_clips(
    clips: list[AssemblyClip],
) -> tuple[list[AssemblyClip], list[AssemblyClip]]:
    """Validate clips exist and are non-empty before assembly.

    Returns (valid_clips, skipped_clips).
    """
    valid: list[AssemblyClip] = []
    skipped: list[AssemblyClip] = []

    for clip in clips:
        if not _clip_file_ok(clip.path):
            logger.warning(f"Skipping bad clip {clip.asset_id}: file missing or empty")
            skipped.append(clip)
            continue
        valid.append(clip)

    if skipped:
        logger.info(f"Clip validation: {len(valid)} valid, {len(skipped)} skipped")

    return valid, skipped


def _clip_file_ok(path: Path) -> bool:
    """Check that a clip file exists and is non-empty."""
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False
