"""Messaging re-encodes should not reach a 4K memory."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.source_quality import is_usable_source


def test_a_whatsapp_reencode_is_rejected() -> None:
    """Measured on a real June pool: all 17 sub-1080p candidates had no camera
    EXIF at all, and every candidate with camera EXIF was 1080p or better."""
    assert not is_usable_source(width=356, height=634, has_camera_exif=False, min_short_side=1080)


def test_a_whatsapp_hd_reencode_is_rejected_by_its_combined_provenance() -> None:
    """Real M093 frame 23: its 1153px short side alone clears the old floor."""
    assert not is_usable_source(
        width=2048,
        height=1153,
        has_camera_exif=False,
        min_short_side=1080,
        original_file_name="00000000-0000-4000-8000-000000000000.jpg",
        captured_at=datetime(2023, 6, 18, tzinfo=UTC),
    )


def test_a_named_no_exif_image_at_the_same_size_is_not_called_forwarded() -> None:
    """A capped export is ambiguous until its renamed messaging fingerprint joins it."""
    assert is_usable_source(
        width=2048,
        height=1153,
        has_camera_exif=False,
        min_short_side=1080,
        original_file_name="portrait_bxl_tour.jpg",
        captured_at=datetime(2023, 6, 18, tzinfo=UTC),
    )


def test_a_legitimately_small_original_is_kept() -> None:
    """An old camera's 480p footage carries real EXIF and is a real memory."""
    assert is_usable_source(width=640, height=480, has_camera_exif=True, min_short_side=1080)


def test_a_pre_smartphone_asset_without_camera_exif_is_kept() -> None:
    """Small/no-EXIF describes the 2003 archive, not a messaging re-encode."""
    assert is_usable_source(
        width=600,
        height=450,
        has_camera_exif=False,
        min_short_side=1080,
        captured_at=datetime(2003, 8, 12, tzinfo=UTC),
    )


def test_the_modern_reencode_rule_starts_after_the_2007_archive() -> None:
    assert not is_usable_source(
        width=600,
        height=450,
        has_camera_exif=False,
        min_short_side=1080,
        captured_at=datetime(2008, 1, 1, tzinfo=UTC),
    )


def test_a_full_resolution_clip_without_exif_is_kept() -> None:
    """Resolution alone is enough; missing EXIF only condemns a small clip."""
    assert is_usable_source(width=3840, height=2160, has_camera_exif=False, min_short_side=1080)


def test_unknown_dimensions_are_kept() -> None:
    """Live-photo rows often have no cached dimensions. Absence of evidence
    must not delete the clip."""
    assert is_usable_source(width=0, height=0, has_camera_exif=False, min_short_side=1080)
