"""The canonical Live manifest survives storage and refuses a disagreeing alias."""

from __future__ import annotations

import pytest

from immich_memories.processing.live_material import (
    VERSION,
    LiveRenderMaterial,
    LiveSourceEntry,
)


def material() -> LiveRenderMaterial:
    """One burst whose third still contributed no displayed footage."""
    return LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 1.0, 0.0, 2.0),
            LiveSourceEntry("still-b", "video-b", 2.0, 1.5, 3.0),
            LiveSourceEntry("still-c", "video-c", 3.0, 1.0, 1.0),
        )
    )


def test_an_empty_slice_survives_storage_with_its_lineage_intact() -> None:
    """Dropping the silent still would lose the picture the burst was taken for."""
    restored = LiveRenderMaterial.from_dict(material().as_dict())

    assert restored == material()
    assert restored.still_ids == ("still-a", "still-b", "still-c")
    assert restored.video_ids == ("video-a", "video-b")
    assert restored.duration_seconds == pytest.approx(3.5)


def test_the_identity_changes_when_a_silent_still_changes() -> None:
    """Two bursts that play identically are still two different sets of pictures."""
    other = LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 1.0, 0.0, 2.0),
            LiveSourceEntry("still-b", "video-b", 2.0, 1.5, 3.0),
            LiveSourceEntry("still-d", "video-d", 3.0, 1.0, 1.0),
        )
    )

    assert other.trim_points == material().trim_points
    assert other.identity() != material().identity()


@pytest.mark.parametrize(
    "manifest",
    [
        "not a manifest",
        {"version": VERSION},
        {"version": "live-render-material-v0", "source_entries": []},
        {"version": VERSION, "source_entries": {}},
        {"version": VERSION, "source_entries": [], "extra": 1},
        {"version": VERSION, "source_entries": ["not an entry"]},
        {"version": VERSION, "source_entries": [{"still_id": "a", "video_id": "v"}]},
    ],
)
def test_a_manifest_that_is_not_this_contract_is_refused(manifest) -> None:
    with pytest.raises(ValueError):
        LiveRenderMaterial.from_dict(manifest)


def test_aligned_arrays_must_agree_with_the_manifest_they_claim_to_describe() -> None:
    """The arrays are a projection; a silent disagreement renders the wrong footage."""
    canonical = material()
    canonical.assert_arrays(
        still_ids=canonical.still_ids,
        video_ids=canonical.video_ids,
        trim_points=[list(pair) for pair in canonical.trim_points],
        shutter_timestamps=canonical.shutter_timestamps,
    )

    with pytest.raises(ValueError, match="arrays disagree"):
        canonical.assert_arrays(
            still_ids=canonical.still_ids,
            video_ids=("unrelated", *canonical.video_ids[1:]),
            trim_points=canonical.trim_points,
            shutter_timestamps=canonical.shutter_timestamps,
        )
