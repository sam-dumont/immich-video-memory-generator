"""Tests for API models."""

from __future__ import annotations

from datetime import datetime

import pytest

from immich_memories.api.models import (
    Asset,
    AssetFace,
    AssetType,
    ExifInfo,
    Person,
    VideoClipInfo,
)


class TestAssetType:
    """Tests for AssetType enum."""

    def test_values(self):
        """Test enum values."""
        assert AssetType.VIDEO.value == "VIDEO"
        assert AssetType.IMAGE.value == "IMAGE"
        assert AssetType.AUDIO.value == "AUDIO"


class TestPerson:
    """Tests for Person model."""

    def test_basic_creation(self):
        """Test basic person creation."""
        person = Person(id="123", name="John Doe")
        assert person.id == "123"
        assert person.name == "John Doe"

    def test_display_name_with_name(self):
        """Test display name when name is set."""
        person = Person(id="123", name="John Doe")
        assert person.display_name == "John Doe"

    def test_display_name_without_name(self):
        """Test display name when name is not set."""
        person = Person(id="12345678-abcd-1234-abcd-123456789012")
        assert person.display_name == "Person 12345678"


class TestAsset:
    """Tests for Asset model."""

    def test_basic_creation(self):
        """Test basic asset creation."""
        asset = Asset(
            id="asset-123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime(2024, 1, 15, 10, 30, 0),
            fileModifiedAt=datetime(2024, 1, 15, 10, 30, 0),
            updatedAt=datetime(2024, 1, 15, 10, 30, 0),
        )
        assert asset.id == "asset-123"
        assert asset.type == AssetType.VIDEO
        assert asset.is_video

    def test_type_parsing_from_string(self):
        """Test type parsing from string value."""
        asset = Asset(
            id="123",
            type="VIDEO",
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.type == AssetType.VIDEO

    def test_duration_parsing(self):
        """Test duration string parsing."""
        # HH:MM:SS format
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            duration="00:01:30.500",
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.duration_seconds == 90.5

        # MM:SS format
        asset2 = Asset(
            id="456",
            type=AssetType.VIDEO,
            duration="02:30.000",
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset2.duration_seconds == 150.0

    def test_year_and_month(self):
        """Test year and month extraction."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime(2024, 6, 15),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.year == 2024
        assert asset.month == 6


class TestAssetFace:
    """Tests for AssetFace model."""

    def test_bounding_box(self):
        """Test bounding box property."""
        face = AssetFace(
            id="face-123",
            boundingBoxX1=100,
            boundingBoxY1=200,
            boundingBoxX2=300,
            boundingBoxY2=400,
        )
        assert face.bounding_box == (100, 200, 300, 400)

    def test_center(self):
        """Test center calculation."""
        face = AssetFace(
            id="face-123",
            boundingBoxX1=100,
            boundingBoxY1=200,
            boundingBoxX2=300,
            boundingBoxY2=400,
        )
        assert face.center == (200.0, 300.0)

    def test_area(self):
        """Test area calculation."""
        face = AssetFace(
            id="face-123",
            boundingBoxX1=0,
            boundingBoxY1=0,
            boundingBoxX2=100,
            boundingBoxY2=200,
        )
        assert face.area == 20000


class TestVideoClipInfo:
    """Tests for VideoClipInfo model."""

    def test_aspect_ratio(self):
        """Test aspect ratio calculation."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=1920,
            height=1080,
        )
        assert clip.aspect_ratio == pytest.approx(16 / 9, rel=0.01)

    def test_is_portrait(self):
        """Test portrait detection."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=1080,
            height=1920,
        )
        assert clip.is_portrait
        assert not clip.is_landscape

    def test_quality_score(self):
        """Test quality score calculation."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=1920,
            height=1080,
            bitrate=10_000_000,
            duration_seconds=30,
        )
        score = clip.quality_score
        assert 0 <= score <= 1

    def test_is_hdr_with_hdr10(self):
        """Test HDR10 detection."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=3840,
            height=2160,
            color_transfer="smpte2084",  # HDR10 transfer function
            color_primaries="bt2020",
        )
        assert clip.is_hdr
        assert clip.hdr_format == "HDR10"

    def test_is_hdr_with_hlg(self):
        """Test HLG detection."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=3840,
            height=2160,
            color_transfer="arib-std-b67",  # HLG transfer function
        )
        assert clip.is_hdr
        assert clip.hdr_format == "HLG"

    def test_is_hdr_sdr(self):
        """Test SDR detection."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=1920,
            height=1080,
            color_transfer="bt709",  # SDR transfer function
        )
        assert not clip.is_hdr
        assert clip.hdr_format == "SDR"

    def test_is_hdr_none(self):
        """Test HDR detection with no color_transfer."""
        asset = Asset(
            id="123",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(
            asset=asset,
            width=1920,
            height=1080,
        )
        assert not clip.is_hdr
        assert clip.hdr_format == "SDR"


class TestVideoClipInfoEdgeCases:
    """Edge cases for VideoClipInfo."""

    def _make_asset(self):
        return Asset(
            id="edge",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )

    def test_square_video_not_portrait_or_landscape(self):
        """Square video (1:1) is neither portrait nor landscape."""
        clip = VideoClipInfo(asset=self._make_asset(), width=1080, height=1080)
        assert not clip.is_portrait
        assert not clip.is_landscape

    def test_aspect_ratio_portrait(self):
        """Portrait video has aspect ratio < 1."""
        clip = VideoClipInfo(asset=self._make_asset(), width=1080, height=1920)
        assert clip.aspect_ratio < 1.0

    def test_zero_bitrate_quality_score(self):
        """Zero bitrate produces a valid quality score (no division error)."""
        clip = VideoClipInfo(
            asset=self._make_asset(),
            width=1920,
            height=1080,
            bitrate=0,
            duration_seconds=30,
        )
        score = clip.quality_score
        assert 0 <= score <= 1

    def test_duration_zero_seconds(self):
        """Asset with zero duration parses correctly."""
        asset = Asset(
            id="z",
            type=AssetType.VIDEO,
            duration="0:00:00.000",
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.duration_seconds == 0.0

    def test_duration_none(self):
        """Asset with no duration returns None."""
        asset = Asset(
            id="n",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.duration_seconds is None


class TestAssetFaceEdgeCases:
    """Edge cases for face geometry calculations."""

    def test_zero_area_face(self):
        """Degenerate face (zero area) calculates correctly."""
        face = AssetFace(
            id="f",
            boundingBoxX1=100,
            boundingBoxY1=200,
            boundingBoxX2=100,
            boundingBoxY2=200,
        )
        assert face.area == 0
        assert face.center == (100.0, 200.0)


class TestHDRDetectionParametrized:
    """Parametrized HDR format detection."""

    def _make_clip(self, color_transfer=None, color_primaries=None):
        asset = Asset(
            id="hdr",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        return VideoClipInfo(
            asset=asset,
            width=3840,
            height=2160,
            color_transfer=color_transfer,
            color_primaries=color_primaries,
        )

    @pytest.mark.parametrize(
        "transfer,primaries,expected_hdr,expected_format",
        [
            pytest.param("smpte2084", "bt2020", True, "HDR10", id="hdr10"),
            pytest.param("arib-std-b67", None, True, "HLG", id="hlg"),
            pytest.param("bt709", None, False, "SDR", id="bt709-sdr"),
            pytest.param(None, None, False, "SDR", id="none-sdr"),
            pytest.param("unknown_transfer", None, False, "SDR", id="unknown-sdr"),
        ],
    )
    def test_hdr_format_detection(self, transfer, primaries, expected_hdr, expected_format):
        """HDR detection maps transfer function to correct format."""
        clip = self._make_clip(color_transfer=transfer, color_primaries=primaries)
        assert clip.is_hdr is expected_hdr
        assert clip.hdr_format == expected_format


class TestDurationParsingParametrized:
    """Parametrized duration string parsing."""

    @pytest.mark.parametrize(
        "duration_str,expected",
        [
            pytest.param("0:00:10.000", 10.0, id="hhmmss-10s"),
            pytest.param("00:01:30.500", 90.5, id="hhmmss-90.5s"),
            pytest.param("02:30.000", 150.0, id="mmss-150s"),
            pytest.param("0:00:00.000", 0.0, id="zero"),
            pytest.param(None, None, id="none"),
        ],
    )
    def test_duration_parsing(self, duration_str, expected):
        """Duration strings parse to correct seconds."""
        asset = Asset(
            id="d",
            type=AssetType.VIDEO,
            duration=duration_str,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.duration_seconds == expected

    def test_invalid_duration_returns_none(self):
        """Unparseable duration returns None."""
        asset = Asset(
            id="d",
            type=AssetType.VIDEO,
            duration="not-a-duration",
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        assert asset.duration_seconds is None


class TestRotationParametrized:
    """Parametrized rotation dimension swapping."""

    def _make_clip(self, rotation=0):
        asset = Asset(
            id="r",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        return VideoClipInfo(asset=asset, width=1920, height=1080, rotation=rotation)

    @pytest.mark.parametrize(
        "rotation,exp_width,exp_height",
        [
            pytest.param(0, 1920, 1080, id="0-deg"),
            pytest.param(90, 1080, 1920, id="90-deg-swapped"),
            pytest.param(180, 1920, 1080, id="180-deg"),
            pytest.param(270, 1080, 1920, id="270-deg-swapped"),
            pytest.param(0, 1920, 1080, id="no-rotation"),
        ],
    )
    def test_displayed_dimensions(self, rotation, exp_width, exp_height):
        """Rotation swaps displayed dimensions for 90/270."""
        clip = self._make_clip(rotation=rotation)
        assert clip.displayed_width == exp_width
        assert clip.displayed_height == exp_height


class TestIsCameraOriginal:
    """Tests for is_camera_original property."""

    def test_with_exif_make_and_model(self):
        """Camera original when exif has make and model."""
        asset = Asset(
            id="c",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
            exifInfo=ExifInfo(make="Apple", model="iPhone 15 Pro"),
        )
        clip = VideoClipInfo(asset=asset, width=1920, height=1080)
        assert clip.is_camera_original

    def test_without_exif(self):
        """Not camera original without exif info."""
        asset = Asset(
            id="c",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        clip = VideoClipInfo(asset=asset, width=1920, height=1080)
        assert not clip.is_camera_original

    def test_with_exif_no_make_no_model(self):
        """Not camera original when exif has neither make nor model."""
        asset = Asset(
            id="c",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
            exifInfo=ExifInfo(),
        )
        clip = VideoClipInfo(asset=asset, width=1920, height=1080)
        assert not clip.is_camera_original


class TestPersonDisplayName:
    """Edge cases for Person.display_name."""

    def test_empty_name(self):
        """Empty name falls back to ID-based display name."""
        person = Person(id="abc12345-rest", name="")
        assert person.display_name == "Person abc12345"

    def test_whitespace_name(self):
        """Whitespace-only name is used as-is (not stripped)."""
        person = Person(id="abc12345", name="  ")
        # name is non-empty so it's used
        assert person.display_name == "  "

    def test_default_name_is_empty(self):
        """Default name is empty string."""
        person = Person(id="abc12345")
        assert person.name == ""
