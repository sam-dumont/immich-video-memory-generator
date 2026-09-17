"""The instant a finished memory is filed under, and how it is written down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.delivery_timestamp import capture_metadata_args, film_capture_instant

PARIS_SUMMER = timezone(timedelta(hours=2))
PARIS_WINTER = timezone(timedelta(hours=1))


def _picture(local: datetime, *, original: datetime | None = None) -> Asset:
    """An asset shot at the given local wall clock, the way Immich reports one.

    Immich sends ``localDateTime`` as the local wall clock with a Z suffix, and
    ``fileCreatedAt`` as the same instant in real UTC.
    """
    instant = local.astimezone(UTC)
    wall_clock = local.replace(tzinfo=UTC)
    return Asset(
        id=f"picture-{local.isoformat()}",
        type=AssetType.IMAGE,
        fileCreatedAt=instant,
        fileModifiedAt=instant,
        updatedAt=instant,
        localDateTime=wall_clock,
        exifInfo=ExifInfo(dateTimeOriginal=original) if original else None,
    )


class TestFilmCaptureInstant:
    def test_last_picture_in_the_zone_most_pictures_share(self):
        """Three pictures at +02:00 and one at +01:00: the last, in +02:00."""
        pictures = [
            _picture(datetime(2024, 6, 19, 9, 0, tzinfo=PARIS_SUMMER)),
            _picture(datetime(2024, 6, 19, 12, 30, tzinfo=PARIS_SUMMER)),
            _picture(datetime(2024, 6, 20, 18, 45, tzinfo=PARIS_SUMMER)),
            _picture(datetime(2024, 6, 18, 8, 0, tzinfo=PARIS_WINTER)),
        ]

        assert film_capture_instant(pictures) == datetime(2024, 6, 20, 18, 45, tzinfo=PARIS_SUMMER)

    def test_last_picture_from_another_zone_is_told_in_the_shared_one(self):
        """The last picture is at +01:00, most are at +02:00: same instant, +02:00 clock."""
        pictures = [
            _picture(datetime(2024, 6, 19, 9, 0, tzinfo=PARIS_SUMMER)),
            _picture(datetime(2024, 6, 19, 12, 30, tzinfo=PARIS_SUMMER)),
            _picture(datetime(2024, 6, 20, 17, 45, tzinfo=PARIS_WINTER)),
        ]

        result = film_capture_instant(pictures)

        assert result == datetime(2024, 6, 20, 17, 45, tzinfo=PARIS_WINTER)
        assert result.utcoffset() == timedelta(hours=2)
        assert result.isoformat() == "2024-06-20T18:45:00+02:00"

    def test_exif_offset_is_read_when_the_local_clock_is_missing(self):
        """A picture without localDateTime still has a zone if EXIF carries one."""
        instant = datetime(2024, 6, 20, 16, 45, tzinfo=UTC)
        picture = Asset(
            id="picture-exif",
            type=AssetType.IMAGE,
            fileCreatedAt=instant,
            fileModifiedAt=instant,
            updatedAt=instant,
            exifInfo=ExifInfo(dateTimeOriginal=instant.astimezone(PARIS_SUMMER)),
        )

        assert film_capture_instant([picture]).utcoffset() == timedelta(hours=2)

    def test_no_usable_time_files_nothing(self):
        instant = datetime(2024, 6, 20, 16, 45, tzinfo=UTC)
        picture = Asset(
            id="picture-bare",
            type=AssetType.IMAGE,
            fileCreatedAt=instant,
            fileModifiedAt=instant,
            updatedAt=instant,
        )

        assert film_capture_instant([picture]) is None
        assert film_capture_instant([]) is None


class TestCaptureMetadataArgs:
    def test_both_container_tags_carry_the_instant(self):
        args = capture_metadata_args(datetime(2024, 6, 20, 18, 45, tzinfo=PARIS_SUMMER))

        assert args == [
            "-metadata",
            "creation_time=2024-06-20T16:45:00Z",
            "-metadata",
            "com.apple.quicktime.creationdate=2024-06-20T18:45:00+02:00",
        ]

    def test_no_instant_adds_no_arguments(self):
        assert capture_metadata_args(None) == []


class TestDeliveryFilesTheMemoryOnItsOwnDay:
    def test_upload_carries_the_last_picture_s_instant(self, tmp_path):
        from unittest.mock import MagicMock

        from immich_memories.api.models import VideoClipInfo
        from immich_memories.generate import GenerationParams
        from immich_memories.generate_delivery import deliver_completed_artifact

        film = tmp_path / "memory.mp4"
        film.write_bytes(b"film")
        clips = [
            VideoClipInfo(asset=_picture(datetime(2024, 6, 19, 9, 0, tzinfo=PARIS_SUMMER))),
            VideoClipInfo(asset=_picture(datetime(2024, 6, 20, 18, 45, tzinfo=PARIS_SUMMER))),
        ]
        # WHY: uploading is a write against a live Immich server.
        client = MagicMock()
        client.upload_memory.return_value = {"asset_id": "asset-1"}
        params = GenerationParams(
            clips=clips,
            output_path=film,
            config=MagicMock(),
            client=client,
            upload_enabled=True,
        )

        deliver_completed_artifact(params, film, MagicMock())

        assert client.upload_memory.call_args.kwargs["captured_at"] == datetime(
            2024, 6, 20, 18, 45, tzinfo=PARIS_SUMMER
        )
