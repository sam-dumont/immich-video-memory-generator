"""Candidate scope, exact reuse and missing-motion behavior of production motion measurement."""

from datetime import UTC, datetime

import pytest

from immich_memories.analysis.editorial_motion_facts import DemandedMotionResolver, measure_motion
from immich_memories.api.models import AssetType
from tests.conftest import make_asset


def _assets(count):
    return {
        f"still-{i}": make_asset(
            f"still-{i}", file_created_at=datetime(2025, 1, 1, tzinfo=UTC)
        ).model_copy(update={"type": AssetType.IMAGE, "live_photo_video_id": f"video-{i}"})
        for i in range(count)
    }


def _carrier(*members):
    return {
        "asset_id": members[0],
        "members": list(members),
        "kind": "live-motion",
        "seconds": 6.0,
        "raw_seconds": 8.0,
        "motion_candidate": True,
        "motion_assessed": False,
    }


def test_only_chosen_carrier_samples_download_and_exact_warm_uses_no_transport(tmp_path):
    assets = _assets(100)
    fetched = []

    def fetch(video):
        fetched.append(video)
        return video.encode()

    resolver = DemandedMotionResolver(
        assets=assets,
        cache_path=tmp_path / "motion.sqlite",
        fetch_video=fetch,
        measure=lambda _payload: {"residual": 2.0, "frames": 12},
    )
    carriers = [_carrier(*(f"still-{i}" for i in range(10)))]
    cold, metrics = resolver(carriers)
    assert fetched == ["video-0", "video-4", "video-9"]
    assert metrics["new_motion_downloads"] == metrics["sampled_sources"] == 3
    assert metrics["unsampled_stills"] == 7
    assert cold[0]["kind"] == "live-motion"

    def forbidden(_video):
        raise AssertionError("warm motion attempted a download")

    resolver.fetch_video = forbidden
    warm, metrics = resolver(carriers)
    assert warm == cold
    assert metrics["new_motion_downloads"] == metrics["fetch_attempts"] == 0
    assert metrics["cache_hits"] == 3


def test_source_metadata_change_invalidates_only_that_motion_fact(tmp_path):
    assets = _assets(2)
    fetched = []
    resolver = DemandedMotionResolver(
        assets=assets,
        cache_path=tmp_path / "motion.sqlite",
        fetch_video=lambda video: fetched.append(video) or b"preview",
        measure=lambda _payload: {"residual": 0.1},
    )
    carriers = [_carrier("still-0"), _carrier("still-1")]
    resolver(carriers)
    assets["still-1"] = assets["still-1"].model_copy(
        update={"live_photo_video_id": "replaced-video"}
    )
    result, metrics = resolver(carriers)
    assert fetched == ["video-0", "video-1", "replaced-video"]
    assert metrics["new_motion_downloads"] == 1 and metrics["cache_hits"] == 1
    assert all(row["kind"] == "live-still" and row["seconds"] == 4.0 for row in result)


def test_a_failed_preview_keeps_the_photograph_and_is_not_banked_as_a_fact(tmp_path):
    def failed(_video):
        raise TimeoutError("preview unavailable")

    resolver = DemandedMotionResolver(
        assets=_assets(1), cache_path=tmp_path / "motion.sqlite", fetch_video=failed
    )
    result, metrics = resolver([_carrier("still-0")])
    assert result[0]["kind"] == "live-still" and result[0]["asset_id"] == "still-0"
    assert not result[0]["motion_assessed"]
    assert metrics["unavailable_sources"] == 1 and metrics["cache_hits"] == 0
    resolver.fetch_video = lambda _video: b"readable"
    resolver.measure = lambda _payload: {"residual": 2.0}
    assert resolver([_carrier("still-0")])[0][0]["kind"] == "live-motion"


def test_programming_errors_and_a_blocked_network_are_not_silently_swallowed(tmp_path):
    def forbidden(_video):
        raise RuntimeError("HTTP blocked")

    resolver = DemandedMotionResolver(
        assets=_assets(1), cache_path=tmp_path / "motion.sqlite", fetch_video=forbidden
    )
    with pytest.raises(RuntimeError, match="HTTP blocked"):
        resolver([_carrier("still-0")])


def test_static_preview_has_no_measured_subject_motion(tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "static.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 12, (80, 60))
    assert writer.isOpened()
    try:
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        frame[20:40, 25:45] = 255
        for _ in range(16):
            writer.write(frame)
    finally:
        writer.release()
    result = measure_motion(path.read_bytes())
    assert result["frames"] == 12
    assert result["residual"] == 0.0


def test_wrapped_immich_transport_failure_keeps_still_and_does_not_bank_error(tmp_path):
    from immich_memories.api.immich import ImmichAPIError
    from tests.conftest import make_asset

    asset = make_asset("one").model_copy(update={"live_photo_video_id": "video-one"})

    def fail(_):
        raise ImmichAPIError("playback unavailable")

    resolver = DemandedMotionResolver(
        assets={"one": asset}, cache_path=tmp_path / "motion.sqlite", fetch_video=fail
    )
    carriers = [{"asset_id": "one", "members": ["one"], "raw_seconds": 3, "motion_candidate": True}]
    first, cost = resolver(carriers)
    second, retry = resolver(carriers)
    assert first == second and first[0]["kind"] == "live-still"
    assert cost["unavailable_sources"] == 1 and retry["fetch_attempts"] == 1
    assert retry["cache_hits"] == 0
