"""Messaging re-encodes should not reach a 4K memory."""

from __future__ import annotations

from immich_memories.analysis.source_quality import is_usable_source


def test_a_whatsapp_reencode_is_rejected() -> None:
    """Measured on a real June pool: all 17 sub-1080p candidates had no camera
    EXIF at all, and every candidate with camera EXIF was 1080p or better."""
    assert not is_usable_source(width=356, height=634, has_camera_exif=False, min_short_side=1080)


def test_a_legitimately_small_original_is_kept() -> None:
    """An old camera's 480p footage carries real EXIF and is a real memory."""
    assert is_usable_source(width=640, height=480, has_camera_exif=True, min_short_side=1080)


def test_a_full_resolution_clip_without_exif_is_kept() -> None:
    """Resolution alone is enough; missing EXIF only condemns a small clip."""
    assert is_usable_source(width=3840, height=2160, has_camera_exif=False, min_short_side=1080)


def test_unknown_dimensions_are_kept() -> None:
    """Live-photo rows often have no cached dimensions. Absence of evidence
    must not delete the clip."""
    assert is_usable_source(width=0, height=0, has_camera_exif=False, min_short_side=1080)
