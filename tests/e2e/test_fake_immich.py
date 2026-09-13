"""Contract tests for the hermetic Immich v3 service."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import combinations
from pathlib import Path

import httpx
import pytest

from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash, hamming_distance
from immich_memories.api.compatibility import ResolvedApiVersion
from immich_memories.api.immich import ImmichAuthError, SyncImmichClient
from immich_memories.api.models import AssetType
from immich_memories.generate_clips import MIN_CLIP_DURATION
from immich_memories.timeperiod import calendar_year
from immich_memories.ui.pages.step2_loading import _build_clips
from tests.e2e.fake_immich import FakeImmichServer
from tests.e2e.fake_library import CAST, LIBRARY

pytestmark = pytest.mark.e2e

_VIDEO_IDS = [p.asset_id for p in LIBRARY if p.is_video]
_PHOTO_IDS = [p.asset_id for p in LIBRARY if not p.is_video]
_VIDEO_SECONDS = [p.seconds for p in LIBRARY if p.is_video]
_JUNE_START = date(2024, 6, 1)
_JUNE_END = date(2024, 6, 30)


def _probe_video(path: Path) -> dict:
    result = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,width,height,pix_fmt,color_transfer",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _probe_image(path: Path) -> dict:
    result = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _upload_to_fake(server: FakeImmichServer, index: int) -> str:
    with server.source_video.open("rb") as source:
        response = httpx.post(
            f"{server.base_url}/api/assets",
            headers={"x-api-key": server.api_key},
            data={
                "filename": f"memory-{index}.mp4",
                "fileCreatedAt": "2024-06-01T12:00:00.000Z",
                "fileModifiedAt": "2024-06-01T12:00:00.000Z",
            },
            files={"assetData": (f"memory-{index}.mp4", source, "video/mp4")},
        )
    response.raise_for_status()
    return response.json()["id"]


def test_auto_client_detects_immich_v3_1(fake_immich_server) -> None:
    """The real client resolves the fake's advertised v3 server version."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="auto",
    ) as client:
        assert client.get_server_info().version_string == "3.1.0"
        assert client.get_api_version() is ResolvedApiVersion.V3


def test_current_user_uses_api_key_authentication(fake_immich_server) -> None:
    """The real client authenticates and receives the deterministic test user."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="auto",
    ) as client:
        user = client.get_current_user()

    assert user.id == "fake-user"
    assert user.email == "fake@example.test"
    assert user.name == "Fake Immich User"


def test_step1_connection_chain_returns_user_people_and_available_years(
    fake_immich_server,
) -> None:
    """The real Step 1 connection call chain completes against the fake."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="auto",
    ) as client:
        user = client.get_current_user()
        people = client.get_all_people()
        years = client.get_available_years()

    assert user.name == "Fake Immich User"
    assert [(person.id, person.name, person.is_hidden) for person in people] == [
        (f"person-{name.lower()}", name, False) for name in CAST
    ]
    assert years == [2024]


def test_wrong_api_key_is_rejected(fake_immich_server) -> None:
    """The fake catches browser tests that forgot or corrupted the API key."""
    # WHY: nothing is replaced here — the real client speaks to the in-process fake Immich.
    with (
        SyncImmichClient(fake_immich_server.base_url, "wrong-key", api_version="v3") as client,
        pytest.raises(ImmichAuthError, match="Invalid API key"),
    ):
        client.get_current_user()


def test_monthly_timeline_contains_every_synthetic_moment(fake_immich_server) -> None:
    """Timeline discovery exposes the fake's complete deterministic inventory."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        buckets = client.get_time_buckets(size="MONTH")
        assets = client.get_bucket_assets("2024-06-01T00:00:00.000Z", size="MONTH")

    # One month holds the whole library.
    assert [(bucket.time_bucket, bucket.count) for bucket in buckets] == [
        ("2024-06-01T00:00:00.000Z", len(LIBRARY)),
    ]
    assert [asset.id for asset in assets if asset.type is AssetType.VIDEO] == _VIDEO_IDS
    assert [asset.id for asset in assets if asset.type is AssetType.IMAGE] == _PHOTO_IDS


def test_monthly_timeline_honors_requested_asset_type_and_count(fake_immich_server) -> None:
    """Typed timeline requests expose matching counts and matching assets only."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        video_buckets = client.get_time_buckets(size="MONTH", asset_type=AssetType.VIDEO)
        photo_buckets = client.get_time_buckets(size="MONTH", asset_type=AssetType.IMAGE)
        videos = client.get_bucket_assets(
            "2024-06-01T00:00:00.000Z",
            size="MONTH",
            asset_type=AssetType.VIDEO,
        )
        photos = client.get_bucket_assets(
            "2024-06-01T00:00:00.000Z",
            size="MONTH",
            asset_type=AssetType.IMAGE,
        )

    assert [bucket.count for bucket in video_buckets] == [len(_VIDEO_IDS)]
    assert [bucket.count for bucket in photo_buckets] == [len(_PHOTO_IDS)]
    assert [asset.id for asset in videos] == _VIDEO_IDS
    assert [asset.id for asset in photos] == _PHOTO_IDS


def test_metadata_search_filters_the_video_and_photo_inventories(fake_immich_server) -> None:
    """The real search client receives exactly the requested media type."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        videos = client.search_metadata(
            asset_type=AssetType.VIDEO, taken_after=_JUNE_START, taken_before=_JUNE_END
        ).all_assets
        photos = client.search_metadata(
            asset_type=AssetType.IMAGE, taken_after=_JUNE_START, taken_before=_JUNE_END
        ).all_assets
        everything = client.search_metadata().all_assets

    assert len(everything) == len(LIBRARY)
    assert [asset.id for asset in videos] == _VIDEO_IDS
    assert [asset.duration_seconds for asset in videos] == _VIDEO_SECONDS
    assert [asset.id for asset in photos] == _PHOTO_IDS
    assert [asset.duration_seconds for asset in photos] == [None] * len(_PHOTO_IDS)


def test_metadata_search_narrows_to_the_named_faces(fake_immich_server) -> None:
    """A person filter is a server-side narrowing, the way Immich answers it."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        kit = client.search_metadata(person_ids=["person-kit"]).all_assets
        both = client.search_metadata(person_ids=["person-kit", "person-robin"]).all_assets

    assert {asset.id for asset in kit} == {p.asset_id for p in LIBRARY if "Kit" in p.people}
    assert {asset.id for asset in both} == {
        p.asset_id for p in LIBRARY if {"Kit", "Robin"} <= set(p.people)
    }
    assert 0 < len(both) < len(kit) < len(LIBRARY)


def test_search_uses_v3_millisecond_duration_on_the_wire(fake_immich_server) -> None:
    """The fake rejects accidental regression to Immich v2 duration strings."""
    response = httpx.post(
        f"{fake_immich_server.base_url}/api/search/metadata",
        headers={"x-api-key": fake_immich_server.api_key},
        json={
            "type": "VIDEO",
            "takenAfter": "2024-06-01T00:00:00.000Z",
            "takenBefore": "2024-06-30T23:59:59.999Z",
        },
    )

    response.raise_for_status()
    durations = [asset["duration"] for asset in response.json()["assets"]["items"]]
    assert durations == [int(seconds * 1000) for seconds in _VIDEO_SECONDS]
    assert all(type(duration) is int for duration in durations)


def test_real_step2_duration_filter_keeps_selectable_fake_clips(fake_immich_server) -> None:
    """The fake videos survive the same minimum-duration filter used by Step 2."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        assets = client.get_videos_for_date_range(calendar_year(2024))

    clips, skipped = _build_clips(assets)

    assert skipped == 0
    assert [clip.asset.id for clip in clips] == _VIDEO_IDS
    assert all(clip.duration_seconds >= MIN_CLIP_DURATION for clip in clips)


def test_original_and_playback_downloads_are_valid_h264_sdr_media(
    fake_immich_server,
    tmp_path: Path,
) -> None:
    """Both real download paths yield the generated, probeable source video."""
    assert fake_immich_server.source_video.is_relative_to(fake_immich_server.root)

    original_path = tmp_path / "original.mp4"
    playback_path = tmp_path / "playback.mp4"
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        client.download_asset(_VIDEO_IDS[0], original_path)
        playback_path.write_bytes(client.get_video_playback(_VIDEO_IDS[0]))

    for path in (original_path, playback_path):
        probe = _probe_video(path)
        streams = {stream["codec_type"]: stream for stream in probe["streams"]}
        assert streams["video"] == {
            "codec_name": "h264",
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "color_transfer": "bt709",
        }
        assert streams["audio"]["codec_name"] == "aac"
        assert float(probe["format"]["duration"]) == pytest.approx(_VIDEO_SECONDS[0], abs=0.1)


def test_photo_originals_are_generated_jpegs(fake_immich_server, tmp_path: Path) -> None:
    """Every fake photo downloads as a real JPEG above the source-quality floor."""
    assert set(fake_immich_server.photo_paths) == set(_PHOTO_IDS)
    assert all(
        path.is_relative_to(fake_immich_server.root)
        for path in fake_immich_server.photo_paths.values()
    )

    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        downloaded = [
            client.download_asset(asset_id, tmp_path / f"{asset_id}.jpg") for asset_id in _PHOTO_IDS
        ]

    for path in downloaded:
        assert _probe_image(path)["streams"] == [
            {"codec_name": "mjpeg", "codec_type": "video", "width": 1920, "height": 1280}
        ]


def test_every_asset_thumbnail_shows_its_own_scene(fake_immich_server) -> None:
    """One preview served for every asset made Phase 1 collapse the pool (#525)."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="v3",
    ) as client:
        hashes = {
            asset_id: compute_thumbnail_hash(client.get_asset_thumbnail(asset_id, size="preview"))
            for asset_id in _VIDEO_IDS + _PHOTO_IDS
        }

    # The editor's duplicate gate: a thumbnail pair within 8 hash bits reads as one scene.
    # The library carries near-duplicate frames on purpose (the phone's second shot of
    # the same moment), so a few close pairs are expected; one preview shared by all
    # assets, the #525 regression, would collapse every pair to zero.
    threshold = 8
    distances = {
        (left, right): hamming_distance(hashes[left], hashes[right])
        for left, right in combinations(sorted(hashes), 2)
    }
    assert all(hashes.values())
    assert len(set(hashes.values())) == len(hashes), "two assets share one preview"
    close = {pair: d for pair, d in distances.items() if d <= threshold}
    assert len(close) < 0.02 * len(distances), f"near-duplicate previews: {close}"


def test_real_auto_client_uploads_and_fake_records_v3_multipart(fake_immich_server) -> None:
    """A real v3 upload is accepted and retained for launch-smoke assertions."""
    with SyncImmichClient(
        fake_immich_server.base_url,
        fake_immich_server.api_key,
        api_version="auto",
    ) as client:
        asset_id = client.upload_asset(fake_immich_server.source_video)

    assert asset_id == "uploaded-1"
    assert len(fake_immich_server.uploads) == 1
    upload = fake_immich_server.uploads[0]
    assert set(upload.fields) == {"filename", "fileCreatedAt", "fileModifiedAt"}
    source_name = fake_immich_server.source_video.name
    assert upload.fields["filename"] == source_name
    assert upload.filename == source_name
    assert upload.content_type == "video/mp4"
    assert upload.data == fake_immich_server.source_video.read_bytes()


def test_concurrent_uploads_receive_unique_ids_and_are_all_recorded(tmp_path: Path) -> None:
    """Concurrent handler threads allocate one unique ID per recorded upload."""
    server = FakeImmichServer.start(tmp_path / "concurrent", upload_commit_delay=0.02)
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            asset_ids = list(pool.map(lambda index: _upload_to_fake(server, index), range(6)))
    finally:
        server.close()

    assert set(asset_ids) == {f"uploaded-{index}" for index in range(1, 7)}
    assert len(server.uploads) == 6
    assert len({upload.filename for upload in server.uploads}) == 6


@pytest.mark.parametrize("v2_field", ["deviceAssetId", "deviceId"])
def test_v3_upload_rejects_each_v2_device_field(fake_immich_server, v2_field: str) -> None:
    """Each removed v2 identity field makes the v3 multipart request invalid."""
    upload_count = len(fake_immich_server.uploads)
    with fake_immich_server.source_video.open("rb") as source:
        response = httpx.post(
            f"{fake_immich_server.base_url}/api/assets",
            headers={"x-api-key": fake_immich_server.api_key},
            data={
                "filename": "memory.mp4",
                "fileCreatedAt": "2024-06-01T12:00:00.000Z",
                "fileModifiedAt": "2024-06-01T12:00:00.000Z",
                v2_field: "removed-in-v3",
            },
            files={"assetData": ("memory.mp4", source, "video/mp4")},
        )

    assert response.status_code == 400
    assert response.json() == {"message": f"v3 upload rejects {v2_field}"}
    assert len(fake_immich_server.uploads) == upload_count


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_unexpected_method_endpoint_pairs_return_json_diagnostics(
    fake_immich_server,
    method: str,
) -> None:
    """Unimplemented smoke traffic fails loudly with its exact request pair."""
    response = httpx.request(
        method,
        f"{fake_immich_server.base_url}/api/not-implemented",
        headers={"x-api-key": fake_immich_server.api_key},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": "unexpected fake Immich request",
        "method": method,
        "path": "/api/not-implemented",
    }
