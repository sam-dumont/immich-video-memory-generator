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

    def _scan_directory(self) -> list[MusicTrack]:
        """Scan directory for music files."""
        tracks: list[MusicTrack] = []

        if not self.music_dir.exists():
            logger.warning(f"Music directory does not exist: {self.music_dir}")
            return tracks

        for path in self.music_dir.rglob("*"):
            if path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                # Try to extract metadata
                try:
                    from mutagen import File as MutagenFile

                    audio = MutagenFile(path)
                    duration = audio.info.length if audio and audio.info else 0

                    # Try to get tags
                    title = path.stem
                    artist = "Unknown"

                    if audio and hasattr(audio, "tags") and audio.tags:
                        title = str(audio.tags.get("title", [path.stem])[0])
                        artist = str(audio.tags.get("artist", ["Unknown"])[0])

                except ImportError:
                    # Mutagen not installed, use filename
                    duration = 0
                    title = path.stem
                    artist = "Unknown"
                except Exception:
                    duration = 0
                    title = path.stem
                    artist = "Unknown"

                # Parse mood/genre from directory structure or filename
                parent_name = path.parent.name.lower()
                tags = []

                for mood in ["happy", "sad", "calm", "energetic", "romantic"]:
                    if mood in parent_name or mood in title.lower():
                        tags.append(mood)

                track = MusicTrack(
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
                tracks.append(track)

        return tracks

    @property
    def tracks(self) -> list[MusicTrack]:
        """Get all tracks (cached)."""
        if self._tracks is None:
            self._tracks = self._scan_directory()
        return self._tracks

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
            # Filter by duration
            if track.duration_seconds > 0:
                if track.duration_seconds < min_duration:
                    continue
                if track.duration_seconds > max_duration:
                    continue

            # Filter by mood/genre in tags or title
            if mood:
                mood_lower = mood.lower()
                if not any(
                    mood_lower in t.lower() for t in track.tags + [track.title, track.artist]
                ):
                    continue

            if genre:
                genre_lower = genre.lower()
                if not any(
                    genre_lower in t.lower() for t in track.tags + [track.title, track.artist]
                ):
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
