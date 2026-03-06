"""Music source providers for automatic background music."""

from __future__ import annotations

import hashlib
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

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


class PixabayMusicSource(MusicSource):
    """Pixabay Music API client.

    Pixabay offers royalty-free music that can be used without attribution.
    API documentation: https://pixabay.com/api/docs/#api_search_music
    """

    BASE_URL = "https://pixabay.com/api/music/"

    # Mood to Pixabay category/mood mapping
    MOOD_MAPPING = {
        "happy": ["happy", "upbeat", "cheerful"],
        "sad": ["sad", "melancholic", "emotional"],
        "calm": ["calm", "relaxing", "peaceful", "ambient"],
        "energetic": ["energetic", "upbeat", "driving"],
        "romantic": ["romantic", "love", "tender"],
        "dramatic": ["dramatic", "epic", "cinematic"],
        "mysterious": ["mysterious", "suspense", "dark"],
        "nostalgic": ["nostalgic", "emotional", "reflective"],
        "playful": ["playful", "fun", "quirky"],
        "inspiring": ["inspiring", "uplifting", "motivational"],
    }

    # Genre mapping
    GENRE_MAPPING = {
        "acoustic": "acoustic",
        "electronic": "electronic",
        "cinematic": "cinematic",
        "classical": "classical",
        "jazz": "jazz",
        "pop": "pop",
        "rock": "rock",
        "ambient": "ambient",
        "folk": "folk",
    }

    def __init__(self, api_key: str | None = None):
        """Initialize Pixabay client.

        Args:
            api_key: Pixabay API key. If not provided, uses limited access.
        """
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        mood: str | None = None,
        genre: str | None = None,
        tempo: str | None = None,
        min_duration: float = 60,
        max_duration: float = 600,
        limit: int = 10,
    ) -> list[MusicTrack]:
        """Search Pixabay for music tracks."""
        params: dict = {
            "per_page": min(limit * 2, 200),  # Get extra to filter
        }

        if self.api_key:
            params["key"] = self.api_key

        # Build search query
        search_terms = []

        if mood:
            mood_lower = mood.lower()
            if mood_lower in self.MOOD_MAPPING:
                search_terms.extend(self.MOOD_MAPPING[mood_lower])
            else:
                search_terms.append(mood_lower)

        if genre:
            genre_lower = genre.lower()
            if genre_lower in self.GENRE_MAPPING:
                params["genre"] = self.GENRE_MAPPING[genre_lower]
            search_terms.append(genre_lower)

        if search_terms:
            params["q"] = " ".join(search_terms[:3])  # Limit query terms

        try:
            response = await self.client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            logger.error(f"Pixabay API error: {e}")
            return []

        tracks = []
        for hit in data.get("hits", []):
            duration = hit.get("duration", 0)

            # Filter by duration
            if duration < min_duration or duration > max_duration:
                continue

            # Filter by tempo if specified
            if tempo:
                # Estimate tempo from tags
                tags = hit.get("tags", "").lower()
                if tempo == "slow" and any(t in tags for t in ["fast", "upbeat", "energetic"]):
                    continue
                if tempo == "fast" and any(t in tags for t in ["slow", "calm", "relaxing"]):
                    continue

            track = MusicTrack(
                id=str(hit.get("id")),
                title=hit.get("title", "Untitled"),
                artist=hit.get("user", "Unknown Artist"),
                duration_seconds=duration,
                url=hit.get("audio", ""),
                preview_url=hit.get("preview", ""),
                tags=hit.get("tags", "").split(", "),
                license="Pixabay License (royalty-free)",
                source="pixabay",
            )
            tracks.append(track)

            if len(tracks) >= limit:
                break

        return tracks

    async def download(
        self,
        track: MusicTrack,
        output_dir: Path,
    ) -> Path:
        """Download a track from Pixabay."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / track.cache_filename

        if output_path.exists():
            logger.info(f"Using cached track: {output_path}")
            return output_path

        if not track.url:
            raise ValueError(f"Track {track.id} has no download URL")

        logger.info(f"Downloading: {track.title}")

        try:
            response = await self.client.get(track.url, follow_redirects=True)
            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

            track.local_path = output_path
            return output_path

        except httpx.HTTPError as e:
            logger.error(f"Download failed: {e}")
            raise


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
    source_type: str = "pixabay",
    api_key: str | None = None,
    music_dir: Path | None = None,
) -> MusicSource:
    """Get a music source by type.

    Args:
        source_type: "pixabay" or "local"
        api_key: API key for Pixabay
        music_dir: Directory for local music

    Returns:
        MusicSource instance
    """
    if source_type == "pixabay":
        return PixabayMusicSource(api_key=api_key)
    elif source_type == "local":
        if not music_dir:
            raise ValueError("music_dir required for local source")
        return LocalMusicSource(music_dir=music_dir)
    else:
        raise ValueError(f"Unknown music source type: {source_type}")
