"""Tests for API models."""

from __future__ import annotations

from datetime import datetime

import pytest

from immich_memories.api.models import (
    Asset,
    AssetFace,
    AssetType,
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
        assert asset.is_video is True

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
        assert clip.is_portrait is True
        assert clip.is_landscape is False

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
        assert clip.is_hdr is True
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
        assert clip.is_hdr is True
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
        assert clip.is_hdr is False
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
        assert clip.is_hdr is False
        assert clip.hdr_format == "SDR"
