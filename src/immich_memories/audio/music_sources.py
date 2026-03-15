"""Music source providers for automatic background music."""

from __future__ import annotations

import hashlib
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MusicTrack:
    """Represents a music track."""

    id: str
    title: str
    artist: str
    duration_seconds: float
    url: str
    preview_url: str | None = None
    tags: list[str] = field(default_factory=list)
    mood: str | None = None
    genre: str | None = None
    tempo: str | None = None  # "slow", "medium", "fast"
    license: str = "royalty-free"
    source: str = "unknown"
    local_path: Path | None = None

    @property
    def cache_filename(self) -> str:
        """Generate a cache filename based on track ID."""
        hash_id = hashlib.md5(  # noqa: S324
            f"{self.source}:{self.id}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        safe_title = "".join(c if c.isalnum() else "_" for c in self.title)[:30]
        return f"{safe_title}_{hash_id}.mp3"


class MusicSource(ABC):
    """Abstract base class for music sources."""

    @abstractmethod
    async def search(
        self,
        mood: str | None = None,
        genre: str | None = None,
        tempo: str | None = None,
        min_duration: float = 60,
        max_duration: float = 600,
        limit: int = 10,
    ) -> list[MusicTrack]:
        """Search for music tracks.

        Args:
            mood: Mood/feeling (e.g., "happy", "calm", "energetic")
            genre: Music genre (e.g., "acoustic", "electronic", "cinematic")
            tempo: Tempo preference ("slow", "medium", "fast")
            min_duration: Minimum duration in seconds
            max_duration: Maximum duration in seconds
            limit: Maximum number of results

        Returns:
            List of matching MusicTrack objects
        """
        pass

    @abstractmethod
    async def download(
        self,
        track: MusicTrack,
        output_dir: Path,
    ) -> Path:
        """Download a track to local storage.

        Args:
            track: The track to download
            output_dir: Directory to save the file

        Returns:
            Path to the downloaded file
        """
        pass

    async def get_random_track(
        self,
        mood: str | None = None,
        genre: str | None = None,
        tempo: str | None = None,
        min_duration: float = 60,
    ) -> MusicTrack | None:
        """Get a random track matching criteria.

        Args:
            mood: Mood/feeling preference
            genre: Genre preference
            tempo: Tempo preference
            min_duration: Minimum duration

        Returns:
            A random matching track, or None if no matches
        """
        tracks = await self.search(
            mood=mood,
            genre=genre,
            tempo=tempo,
            min_duration=min_duration,
            limit=20,
        )
        return random.choice(tracks) if tracks else None


class LocalMusicSource(MusicSource):
    """Local music library source.

    Scans a directory for music files and provides search functionality.
    """

    SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac"}

    def __init__(self, music_dir: Path):
        """Initialize with a local music directory.

        Args:
            music_dir: Directory containing music files
        """
        self.music_dir = Path(music_dir)
        self._tracks: list[MusicTrack] | None = None

    @staticmethod
    def _read_mutagen_metadata(path: Path) -> tuple[float, str, str]:
        """Read duration, title, artist from audio file via mutagen."""
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
        duration = audio.info.length if audio and audio.info else 0.0
        title = path.stem
        artist = "Unknown"
        if audio and hasattr(audio, "tags") and audio.tags:
            title = str(audio.tags.get("title", [path.stem])[0])
            artist = str(audio.tags.get("artist", ["Unknown"])[0])
        return duration, title, artist

    @staticmethod
    def _extract_tags_from_path(path: Path, title: str) -> list[str]:
        """Infer mood tags from parent directory name and title."""
        parent_name = path.parent.name.lower()
        return [
            mood
            for mood in ("happy", "sad", "calm", "energetic", "romantic")
            if mood in parent_name or mood in title.lower()
        ]

    def _load_track_metadata(self, path: Path) -> tuple[float, str, str]:
        """Load duration, title, artist — falls back to filename on error."""
        try:
            return self._read_mutagen_metadata(path)
        except (ImportError, Exception):
            return 0.0, path.stem, "Unknown"

    def _scan_directory(self) -> list[MusicTrack]:
        """Scan directory for music files."""
        if not self.music_dir.exists():
            logger.warning(f"Music directory does not exist: {self.music_dir}")
            return []

        tracks: list[MusicTrack] = []
        for path in self.music_dir.rglob("*"):
            if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            duration, title, artist = self._load_track_metadata(path)
            tags = self._extract_tags_from_path(path, title)
            tracks.append(
                MusicTrack(
                    id=str(path),
                    title=title,
                    artist=artist,
                    duration_seconds=duration,
                    url=f"file://{path}",
                    tags=tags,
                    license="local",
                    source="local",
                    local_path=path,
                )
            )
        return tracks

    @property
    def tracks(self) -> list[MusicTrack]:
        """Get all tracks (cached)."""
        if self._tracks is None:
            self._tracks = self._scan_directory()
        return self._tracks

    @staticmethod
    def _track_matches_duration(
        track: MusicTrack, min_duration: float, max_duration: float
    ) -> bool:
        """Return False if track duration is known and outside bounds."""
        if track.duration_seconds <= 0:
            return True
        return min_duration <= track.duration_seconds <= max_duration

    @staticmethod
    def _track_matches_term(track: MusicTrack, term: str) -> bool:
        """Return True if term appears in any of the track's tags, title, or artist."""
        term_lower = term.lower()
        return any(term_lower in t.lower() for t in track.tags + [track.title, track.artist])

    async def search(
        self,
        mood: str | None = None,
        genre: str | None = None,
        tempo: str | None = None,
        min_duration: float = 60,
        max_duration: float = 600,
        limit: int = 10,
    ) -> list[MusicTrack]:
        """Search local music library."""
        results = []
        for track in self.tracks:
            if not self._track_matches_duration(track, min_duration, max_duration):
                continue
            if mood and not self._track_matches_term(track, mood):
                continue
            if genre and not self._track_matches_term(track, genre):
                continue
            results.append(track)
            if len(results) >= limit:
                break
        return results

    async def download(
        self,
        track: MusicTrack,
        output_dir: Path,
    ) -> Path:
        """Return local path (no download needed)."""
        if track.local_path and track.local_path.exists():
            return track.local_path
        raise ValueError(f"Local track not found: {track.id}")


def get_music_source(
    source_type: str = "local",
    music_dir: Path | None = None,
) -> MusicSource:
    """Get a music source by type.

    Args:
        source_type: "local"
        music_dir: Directory for local music

    Returns:
        MusicSource instance
    """
    if source_type == "local":
        if not music_dir:
            raise ValueError("music_dir required for local source")
        return LocalMusicSource(music_dir=music_dir)
    else:
        raise ValueError(f"Unknown music source type: {source_type}")
