"""Pydantic models for Immich API responses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetType(StrEnum):
    """Asset type enumeration."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    OTHER = "OTHER"


class ExifInfo(BaseModel):
    """EXIF metadata for an asset."""

    make: str | None = None
    model: str | None = None
    exposure_time: str | None = Field(default=None, alias="exposureTime")
    f_number: float | None = Field(default=None, alias="fNumber")
    iso: int | None = None
    focal_length: float | None = Field(default=None, alias="focalLength")
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    date_time_original: datetime | None = Field(default=None, alias="dateTimeOriginal")
    lens_model: str | None = Field(default=None, alias="lensModel")
    file_size_in_byte: int | None = Field(default=None, alias="fileSizeInByte")

    model_config = ConfigDict(populate_by_name=True)


class VideoInfo(BaseModel):
    """Video-specific information."""

    duration_seconds: float | None = Field(default=None, alias="durationSeconds")
    bitrate: int | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    audio_codec: str | None = Field(default=None, alias="audioCodec")
    frame_rate: float | None = Field(default=None, alias="frameRate")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def resolution(self) -> tuple[int, int] | None:
        """Get resolution as (width, height) tuple."""
        if self.width and self.height:
            return (self.width, self.height)
        return None

    @property
    def megapixels(self) -> float | None:
        """Calculate megapixels."""
        if self.width and self.height:
            return (self.width * self.height) / 1_000_000
        return None


class PersonThumbnail(BaseModel):
    """Thumbnail info for a person."""

    asset_id: str = Field(alias="assetId")

    model_config = ConfigDict(populate_by_name=True)


class Person(BaseModel):
    """Person identified in Immich."""

    id: str
    name: str = ""
    birth_date: datetime | None = Field(default=None, alias="birthDate")
    thumbnail_path: str | None = Field(default=None, alias="thumbnailPath")
    is_hidden: bool = Field(default=False, alias="isHidden")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    # Nested face bboxes (returned by /api/assets/{id}, not search)
    faces: list[AssetFace] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    @property
    def display_name(self) -> str:
        """Get display name, falling back to ID if no name set."""
        return self.name or f"Person {self.id[:8]}"


class AssetFace(BaseModel):
    """Face detected in an asset."""

    id: str
    person: Person | None = None
    bounding_box_x1: int = Field(default=0, alias="boundingBoxX1")
    bounding_box_y1: int = Field(default=0, alias="boundingBoxY1")
    bounding_box_x2: int = Field(default=0, alias="boundingBoxX2")
    bounding_box_y2: int = Field(default=0, alias="boundingBoxY2")
    # Image dimensions the bbox was computed on (thumbnail, not original)
    image_width: int = Field(default=0, alias="imageWidth")
    image_height: int = Field(default=0, alias="imageHeight")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def bounding_box(self) -> tuple[int, int, int, int]:
        """Get bounding box as (x1, y1, x2, y2) tuple."""
        return (
            self.bounding_box_x1,
            self.bounding_box_y1,
            self.bounding_box_x2,
            self.bounding_box_y2,
        )

    @property
    def center(self) -> tuple[float, float]:
        """Get center point of the bounding box."""
        return (
            (self.bounding_box_x1 + self.bounding_box_x2) / 2,
            (self.bounding_box_y1 + self.bounding_box_y2) / 2,
        )

    @property
    def area(self) -> int:
        """Calculate area of the bounding box."""
        return abs(
            (self.bounding_box_x2 - self.bounding_box_x1)
            * (self.bounding_box_y2 - self.bounding_box_y1)
        )


class SmartInfo(BaseModel):
    """Immich object detection results."""

    objects: list[str] | None = None


class Asset(BaseModel):
    """An asset (photo or video) from Immich."""

    id: str
    device_asset_id: str = Field(default="", alias="deviceAssetId")
    owner_id: str = Field(default="", alias="ownerId")
    device_id: str = Field(default="", alias="deviceId")
    type: AssetType
    original_path: str = Field(default="", alias="originalPath")
    original_file_name: str = Field(default="", alias="originalFileName")
    original_mime_type: str | None = Field(default=None, alias="originalMimeType")
    thumbhash: str | None = None
    file_created_at: datetime = Field(alias="fileCreatedAt")
    file_modified_at: datetime = Field(alias="fileModifiedAt")
    local_date_time: datetime | None = Field(default=None, alias="localDateTime")
    updated_at: datetime = Field(alias="updatedAt")
    is_favorite: bool = Field(default=False, alias="isFavorite")
    is_archived: bool = Field(default=False, alias="isArchived")
    is_trashed: bool = Field(default=False, alias="isTrashed")
    duration: str | None = None
    # WHY: width/height from search API — needed for resolution filtering
    # BEFORE download. Without these, all non-favorites report 0×0 and get dropped.
    width: int = Field(default=0)
    height: int = Field(default=0)
    exif_info: ExifInfo | None = Field(default=None, alias="exifInfo")
    people: list[Person] = Field(default_factory=list)
    faces: list[AssetFace] = Field(default_factory=list)
    checksum: str | None = None
    live_photo_video_id: str | None = Field(default=None, alias="livePhotoVideoId")
    smart_info: SmartInfo | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def is_live_photo(self) -> bool:
        return self.live_photo_video_id is not None

    @field_validator("type", mode="before")
    @classmethod
    def parse_type(cls, v: Any) -> AssetType:
        """Parse asset type from string."""
        if isinstance(v, AssetType):
            return v
        if isinstance(v, str):
            try:
                return AssetType(v.upper())
            except ValueError:
                return AssetType.OTHER
        return AssetType.OTHER

    @property
    def is_video(self) -> bool:
        """Check if this asset is a video."""
        return self.type == AssetType.VIDEO

    @property
    def duration_seconds(self) -> float | None:
        """Parse duration string to seconds."""
        if not self.duration:
            return None
        try:
            # Format is typically "HH:MM:SS.mmm" or "MM:SS.mmm"
            parts = self.duration.split(":")
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
            elif len(parts) == 2:
                minutes, seconds = parts
                return float(minutes) * 60 + float(seconds)
            else:
                return float(self.duration)
        except (ValueError, TypeError):
            return None

    @property
    def year(self) -> int:
        """Get the year this asset was created."""
        return self.file_created_at.year

    @property
    def month(self) -> int:
        """Get the month this asset was created."""
        return self.file_created_at.month

    @property
    def file_size_mb(self) -> float | None:
        """Get file size in megabytes."""
        if self.exif_info and self.exif_info.file_size_in_byte:
            return self.exif_info.file_size_in_byte / (1024 * 1024)
        return None


class SearchResult(BaseModel):
    """Search result from Immich API."""

    assets: dict[str, list[Asset]] = Field(default_factory=dict)
    total: int = 0
    next_page: str | None = Field(default=None, alias="nextPage")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def all_assets(self) -> list[Asset]:
        """Get all assets from all buckets."""
        result = []
        for assets in self.assets.values():
            result.extend(assets)
        return result


class TimeBucket(BaseModel):
    """Time bucket for timeline queries."""

    count: int
    time_bucket: str = Field(alias="timeBucket")

    model_config = ConfigDict(populate_by_name=True)


class ServerInfo(BaseModel):
    """Immich server information."""

    version: str = ""
    version_url: str = Field(default="", alias="versionUrl")

    model_config = ConfigDict(populate_by_name=True)


class UserInfo(BaseModel):
    """Current user information."""

    id: str
    email: str
    name: str = ""
    is_admin: bool = Field(default=False, alias="isAdmin")
    avatar_color: str | None = Field(default=None, alias="avatarColor")
    profile_image_path: str = Field(default="", alias="profileImagePath")

    model_config = ConfigDict(populate_by_name=True)


class SmartSearchResult(BaseModel):
    """Result from smart/semantic search."""

    assets: dict[str, list[Asset]] = Field(default_factory=dict)
    next_page: str | None = Field(default=None, alias="nextPage")

    model_config = ConfigDict(populate_by_name=True)


class SearchAssetsResult(BaseModel):
    """Assets portion of search result."""

    total: int = 0
    count: int = 0
    items: list[Asset] = Field(default_factory=list)
    next_page: str | None = Field(default=None, alias="nextPage")

    model_config = ConfigDict(populate_by_name=True)


class MetadataSearchResult(BaseModel):
    """Result from metadata search."""

    assets: SearchAssetsResult = Field(default_factory=SearchAssetsResult)

    model_config = ConfigDict(populate_by_name=True)

    @property
    def all_assets(self) -> list[Asset]:
        """Get all assets from the search result."""
        return self.assets.items

    @property
    def next_page(self) -> str | None:
        """Get the next page token."""
        return self.assets.next_page

    @property
    def total(self) -> int:
        """Get total count of matching assets."""
        return self.assets.total


class VideoClipInfo(BaseModel):
    """Information about a video clip for processing."""

    asset: Asset
    local_path: str | None = None
    duration_seconds: float = 0
    width: int = 0
    height: int = 0
    bitrate: int = 0
    fps: float = 0
    codec: str = ""
    rotation: int = 0  # Rotation metadata (0, 90, 180, 270) - affects displayed orientation
    # HDR metadata
    color_space: str | None = None  # e.g., "bt2020nc"
    color_transfer: str | None = None  # e.g., "smpte2084" (HDR10), "arib-std-b67" (HLG)
    color_primaries: str | None = None  # e.g., "bt2020"
    bit_depth: int | None = None  # 8, 10, 12

    # Live Photo burst: video IDs + trim points + shutter timestamps for merging
    live_burst_video_ids: list[str] | None = None
    live_burst_trim_points: list[tuple[float, float]] | None = None
    live_burst_shutter_timestamps: list[float] | None = None  # epoch seconds per clip

    # Audio categories detected (populated during pipeline analysis)
    audio_categories: list[str] | None = None  # e.g. ["laughter", "speech", "engine"]

    # LLM Content Analysis results (populated during pipeline analysis)
    llm_description: str | None = None  # Brief description of what's happening
    llm_emotion: str | None = None  # Detected emotional tone (happy, calm, excited, etc.)
    llm_setting: str | None = None  # Where it takes place (indoor, outdoor, beach, etc.)
    llm_activities: list[str] | None = None  # Activities detected
    llm_subjects: list[str] | None = None  # Who/what is in the video
    llm_interestingness: float | None = None  # Score 0-1 for how interesting
    llm_quality: float | None = None  # Score 0-1 for visual quality

    @property
    def video_asset_id(self) -> str:
        """Get the asset ID for the actual video content.

        For regular videos, this is the asset ID. For Live Photos, the video
        component lives at a different ID (live_photo_video_id).
        """
        return self.asset.live_photo_video_id or self.asset.id

    @property
    def has_llm_analysis(self) -> bool:
        """Check if LLM analysis results are available."""
        return self.llm_description is not None or self.llm_emotion is not None

    @property
    def resolution(self) -> tuple[int, int]:
        """Get resolution as (width, height) tuple."""
        return (self.width, self.height)

    @property
    def aspect_ratio(self) -> float:
        """Calculate aspect ratio."""
        if self.height == 0:
            return 0
        return self.width / self.height

    @property
    def displayed_width(self) -> int:
        """Get displayed width (accounting for rotation)."""
        if self.rotation in (90, 270):
            return self.height
        return self.width

    @property
    def displayed_height(self) -> int:
        """Get displayed height (accounting for rotation)."""
        if self.rotation in (90, 270):
            return self.width
        return self.height

    @property
    def is_portrait(self) -> bool:
        """Check if video is portrait orientation (accounting for rotation)."""
        return self.displayed_height > self.displayed_width

    @property
    def is_landscape(self) -> bool:
        """Check if video is landscape orientation (accounting for rotation)."""
        return self.displayed_width > self.displayed_height

    @property
    def quality_score(self) -> float:
        """Calculate a quality score based on resolution, bitrate, etc."""
        # Weight factors
        resolution_weight = 0.4
        bitrate_weight = 0.4
        duration_weight = 0.2

        # Normalize values (assuming max 4K, 50Mbps, 60s)
        resolution_score = min((self.width * self.height) / (3840 * 2160), 1.0)
        bitrate_score = min(self.bitrate / 50_000_000, 1.0) if self.bitrate else 0.5
        duration_score = min(self.duration_seconds / 60, 1.0)

        return (
            resolution_score * resolution_weight
            + bitrate_score * bitrate_weight
            + duration_score * duration_weight
        )

    @property
    def is_hdr(self) -> bool:
        """Check if video is HDR based on color transfer function."""
        hdr_transfers = {"smpte2084", "arib-std-b67", "smpte428"}  # HDR10, HLG, DCI-P3
        return self.color_transfer in hdr_transfers if self.color_transfer else False

    @property
    def hdr_format(self) -> str:
        """Get the HDR format name if HDR, otherwise SDR."""
        if not self.color_transfer:
            return "SDR"
        return {
            "smpte2084": "HDR10",
            "arib-std-b67": "HLG",
            "smpte428": "DCI-P3",
        }.get(self.color_transfer, "SDR")

    @property
    def is_camera_original(self) -> bool:
        """Check if video is original camera footage (not a compilation/processed video).

        Videos from phones/cameras have EXIF make/model metadata.
        Compilations (FamilyAlbum, etc.) typically lack this metadata.
        """
        if not self.asset.exif_info:
            return False
        # Must have either make or model to be considered original camera footage
        return bool(self.asset.exif_info.make or self.asset.exif_info.model)
