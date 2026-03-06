"""MusicGen API client for AI-generated background music.

This module integrates with the MusicGen API server to:
1. Generate multiple music versions for user selection
2. Separate stems (vocals/accompaniment) for intelligent ducking
3. Handle video timeline-aware duration calculations
"""

from __future__ import annotations

import asyncio
import logging
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Seasonal Prompts
# =============================================================================

SEASONAL_MOODS = {
    1: "winter bright energetic",
    2: "late winter hopeful driving",
    3: "spring fresh bouncy uplifting",
    4: "spring bright cheerful playful",
    5: "late spring vibrant energetic fun",
    6: "early summer carefree sunny groovy",
    7: "midsummer bright joyful energetic",
    8: "summer sunny upbeat fun",
    9: "early autumn warm upbeat groovy",
    10: "autumn cozy upbeat rhythmic",
    11: "late autumn upbeat warm",
    12: "winter holiday festive bouncy fun",
}


def get_seasonal_prompt(month: int, hemisphere: str = "north") -> str:
    """Generate seasonal mood keywords for a month.

    Args:
        month: Month number (1-12)
        hemisphere: "north" or "south" (inverts seasons for southern hemisphere)

    Returns:
        Seasonal mood string for music generation prompt
    """
    if not 1 <= month <= 12:
        return ""

    # Invert seasons for southern hemisphere
    if hemisphere.lower() == "south":
        month = (month + 6 - 1) % 12 + 1

    return SEASONAL_MOODS.get(month, "")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class ClipMood:
    """Mood information for a single video clip."""

    duration: float  # Clip duration in seconds
    mood: str  # Primary mood (e.g., "happy", "nostalgic")
    has_transition_after: bool = False  # Month divider after this clip
    transition_duration: float = 2.0  # Duration of transition (if any)
    month: int | None = None  # Month number (1-12) for seasonal prompts


@dataclass
class VideoTimeline:
    """Timeline info for music duration calculation."""

    title_duration: float = 3.5       # Opening title screen
    ending_duration: float = 7.0      # Ending screen (fade to white)
    fade_buffer: float = 5.0          # Extra for smooth fade out (5s as requested)

    # Per-clip mood information
    clips: list[ClipMood] = field(default_factory=list)

    @property
    def content_duration(self) -> float:
        """Total content duration including transitions."""
        total = 0.0
        for clip in self.clips:
            total += clip.duration
            if clip.has_transition_after:
                total += clip.transition_duration
        return total

    @property
    def total_duration(self) -> float:
        """Total music duration needed."""
        return (
            self.title_duration
            + self.content_duration
            + self.ending_duration
            + self.fade_buffer
        )

    @property
    def content_start(self) -> float:
        """When main content starts (after title)."""
        return self.title_duration

    def build_scenes(self, hemisphere: str = "north") -> list[dict]:
        """Build scene list for MusicGen soundtrack API.

        Rules:
        - Title duration is added to FIRST clip
        - Transition durations are added to the clip BEFORE the transition
        - Ending duration + fade buffer are added to LAST clip
        - Seasonal keywords are combined with video mood for richer prompts

        Args:
            hemisphere: "north" or "south" for seasonal prompt generation

        Returns:
            List of {"mood": str, "duration": int} for soundtrack API
        """
        if not self.clips:
            # No clips, just return a single upbeat scene
            return [{"mood": "upbeat", "duration": int(self.total_duration)}]

        scenes = []

        for i, clip in enumerate(self.clips):
            scene_duration = clip.duration

            # First clip: add title duration
            if i == 0:
                scene_duration += self.title_duration

            # Add transition duration if there's one after this clip
            if clip.has_transition_after:
                scene_duration += clip.transition_duration

            # Last clip: add ending duration + fade buffer
            if i == len(self.clips) - 1:
                scene_duration += self.ending_duration + self.fade_buffer

            # API requires integer seconds, minimum 5s
            scene_duration = max(5, int(scene_duration))

            # Combine video mood with seasonal keywords
            mood = clip.mood

            # Transform mellow moods to be more energetic
            # Memory videos should feel upbeat and fun, not slow and sad
            mood_lower = mood.lower()
            mellow_words = ["calm", "peaceful", "serene", "gentle", "soft", "quiet", "slow", "relaxed", "mellow", "tender"]
            sad_words = ["sad", "melancholy", "somber", "nostalgic", "reflective", "wistful", "bittersweet"]

            if any(word in mood_lower for word in mellow_words):
                # Replace mellow with energetic but warm
                mood = f"upbeat warm groovy {mood}"
            elif any(word in mood_lower for word in sad_words):
                # Replace sad with warm but still positive
                mood = f"warm uplifting hopeful"
            else:
                # For all other moods, ensure they're upbeat
                if "upbeat" not in mood_lower and "energetic" not in mood_lower:
                    mood = f"upbeat {mood}"

            if clip.month:
                seasonal = get_seasonal_prompt(clip.month, hemisphere)
                if seasonal:
                    mood = f"{mood}, {seasonal}"

            scenes.append({
                "mood": mood,
                "duration": scene_duration,
            })

        return scenes

    @classmethod
    def from_clips(
        cls,
        clips: list[tuple[float, str, int | None]],  # List of (duration, mood, month)
        transitions_after: list[int] | None = None,  # Indices with transitions after
        title_duration: float = 3.5,
        ending_duration: float = 4.0,
        transition_duration: float = 2.0,
        fade_buffer: float = 5.0,
    ) -> VideoTimeline:
        """Create timeline from clip list.

        Args:
            clips: List of (duration, mood, month) tuples. Month can be None.
            transitions_after: Indices of clips that have transitions after them
            title_duration: Title screen duration
            ending_duration: Ending screen duration
            transition_duration: Duration of each transition
            fade_buffer: Extra buffer for fade out

        Returns:
            VideoTimeline instance
        """
        transitions_after = transitions_after or []

        clip_moods = []
        for i, clip_data in enumerate(clips):
            # Support both (duration, mood) and (duration, mood, month) tuples
            if len(clip_data) == 2:
                duration, mood = clip_data
                month = None
            else:
                duration, mood, month = clip_data

            clip_moods.append(ClipMood(
                duration=duration,
                mood=mood,
                has_transition_after=(i in transitions_after),
                transition_duration=transition_duration,
                month=month,
            ))

        return cls(
            title_duration=title_duration,
            ending_duration=ending_duration,
            fade_buffer=fade_buffer,
            clips=clip_moods,
        )


@dataclass
class MusicStems:
    """Separated audio stems from Demucs.

    Supports both 2-stem (vocals/accompaniment) and 4-stem (drums/bass/vocals/other)
    separation modes. Use `has_full_stems` to check which mode was used.
    """

    vocals: Path  # Melody/vocal stem (duck most aggressively)
    accompaniment: Path | None = None  # Combined drums+bass+other (2-stem mode)

    # 4-stem mode (htdemucs) - more granular control
    drums: Path | None = None  # Drum stem (duck least)
    bass: Path | None = None  # Bass stem (duck moderately)
    other: Path | None = None  # Other instruments (duck moderately)

    @property
    def has_full_stems(self) -> bool:
        """Check if 4-stem separation was used."""
        return self.drums is not None and self.bass is not None

    def cleanup(self):
        """Remove stem files."""
        for path in [self.vocals, self.accompaniment, self.drums, self.bass, self.other]:
            if path and path.exists():
                path.unlink()


@dataclass
class GeneratedMusic:
    """A single generated music version."""

    version_id: int
    full_mix: Path
    stems: MusicStems | None = None
    duration: float = 0.0
    prompt: str = ""
    mood: str = ""

    def cleanup(self):
        """Remove all associated files."""
        if self.full_mix.exists():
            self.full_mix.unlink()
        if self.stems:
            self.stems.cleanup()


@dataclass
class MusicGenerationResult:
    """Result containing multiple versions for user selection."""

    versions: list[GeneratedMusic]
    timeline: VideoTimeline
    mood: str
    selected_version: int | None = None  # User's choice (0-indexed)

    @property
    def selected(self) -> GeneratedMusic | None:
        """Get the selected version."""
        if self.selected_version is not None and 0 <= self.selected_version < len(self.versions):
            return self.versions[self.selected_version]
        return None

    def cleanup_unselected(self):
        """Remove unselected versions to save space."""
        for i, version in enumerate(self.versions):
            if i != self.selected_version:
                version.cleanup()


@dataclass
class StemDuckingConfig:
    """Configuration for stem-aware audio ducking."""

    # During speech: keep accompaniment (drums+bass), lower vocals/melody
    duck_vocals: bool = True
    duck_amount_db: float = -12.0

    # Crossfade duration for ducking transitions
    crossfade_ms: float = 100.0

    # Fade settings
    fade_in_seconds: float = 2.0
    fade_out_seconds: float = 3.0


# =============================================================================
# API Client
# =============================================================================

@dataclass
class MusicGenClientConfig:
    """Configuration for MusicGen API connection."""

    base_url: str = "http://localhost:8000"
    api_key: str | None = None
    timeout_seconds: int = 10800  # 3 hours per job (generation can be slow for long videos)
    poll_interval_seconds: float = 2.0
    num_versions: int = 3  # Generate 3 versions for selection

    @classmethod
    def from_app_config(cls, app_config) -> MusicGenClientConfig:
        """Create client config from app's MusicGenConfig (pydantic model).

        Args:
            app_config: The MusicGenConfig from immich_memories.config

        Returns:
            MusicGenClientConfig instance for the API client
        """
        return cls(
            base_url=app_config.base_url,
            api_key=app_config.api_key or None,
            timeout_seconds=app_config.timeout_seconds,
            num_versions=app_config.num_versions,
        )


# Alias for backwards compatibility
MusicGenConfig = MusicGenClientConfig


class MusicGenClient:
    """Client for the MusicGen API server."""

    def __init__(self, config: MusicGenConfig | None = None):
        self.config = config or MusicGenConfig()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        headers = {}
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key
        # Use explicit timeout values:
        # - connect: 30s to establish connection
        # - read: 120s for individual read operations (file downloads can be slow)
        # - write: 30s for sending requests
        # - pool: None (no pool timeout)
        # The overall job polling timeout is handled separately in _wait_for_job
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=None),
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context.")
        return self._client

    async def health_check(self) -> dict:
        """Check API health and GPU availability."""
        resp = await self.client.get("/health")
        resp.raise_for_status()
        return resp.json()

    async def _submit_job(self, endpoint: str, payload: dict) -> str:
        """Submit a job and return job_id."""
        resp = await self.client.post(endpoint, json=payload)
        resp.raise_for_status()
        return resp.json()["job_id"]

    async def _wait_for_job(
        self,
        job_id: str,
        progress_callback: callable | None = None,
    ) -> dict:
        """Poll job status until completion with retry for transient errors."""
        start_time = time.time()
        consecutive_errors = 0
        max_consecutive_errors = 5

        while True:
            if time.time() - start_time > self.config.timeout_seconds:
                raise TimeoutError(f"Job {job_id} timed out after {self.config.timeout_seconds}s")

            try:
                resp = await self.client.get(f"/jobs/{job_id}")
                resp.raise_for_status()
                job = resp.json()
                consecutive_errors = 0  # Reset on success
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                consecutive_errors += 1
                logger.warning(
                    f"Job {job_id} poll error ({consecutive_errors}/{max_consecutive_errors}): "
                    f"{type(e).__name__}: {e}"
                )
                if consecutive_errors >= max_consecutive_errors:
                    raise RuntimeError(
                        f"Job {job_id} polling failed after {max_consecutive_errors} consecutive errors"
                    ) from e
                await asyncio.sleep(self.config.poll_interval_seconds * 2)  # Back off
                continue

            status = job["status"]
            progress = job.get("progress", 0)

            if progress_callback:
                detail = job.get("progress_detail", {})
                progress_callback(status, progress, detail)

            if status == "completed":
                return job
            elif status == "failed":
                raise RuntimeError(f"Job failed: {job.get('error', 'Unknown error')}")

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _download_file(self, file_url: str, output_path: Path) -> Path:
        """Download a result file."""
        resp = await self.client.get(file_url)
        resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path

    async def generate_music(
        self,
        prompt: str,
        duration: int,
        mood: str | None = None,
        output_dir: Path | None = None,
        progress_callback: callable | None = None,
    ) -> Path:
        """Generate a single music track.

        Args:
            prompt: Text description of desired music
            duration: Duration in seconds (10-120)
            mood: Optional mood modifier
            output_dir: Directory for output file
            progress_callback: Optional callback(status, progress, detail)

        Returns:
            Path to generated WAV file
        """
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="musicgen_"))

        # Submit generation job
        job_id = await self._submit_job("/generate", {
            "prompt": prompt,
            "duration": min(120, max(10, duration)),  # Clamp to API limits
            "mood": mood,
        })

        logger.info(f"Music generation job submitted: {job_id}")

        # Wait for completion
        job = await self._wait_for_job(job_id, progress_callback)

        # Download result
        if not job.get("result_urls"):
            raise RuntimeError("No result files in completed job")

        result_url = job["result_urls"][0]
        output_path = output_dir / f"generated_{job_id[:8]}.wav"
        await self._download_file(result_url, output_path)

        logger.info(f"Music generated: {output_path}")
        return output_path

    async def separate_stems(
        self,
        audio_path: Path,
        output_dir: Path | None = None,
        progress_callback: callable | None = None,
    ) -> MusicStems:
        """Separate audio into stems using Demucs (htdemucs model).

        The API returns 4 stems: drums, bass, vocals, other.
        Server-side config determines stem mode (TWO_STEMS flag).

        Args:
            audio_path: Path to audio file
            output_dir: Directory for output files
            progress_callback: Optional callback(status, progress, detail)

        Returns:
            MusicStems with paths to separated files
        """
        if output_dir is None:
            output_dir = audio_path.parent

        # Upload file and submit separation job
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            resp = await self.client.post("/separate", files=files)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

        logger.info(f"Stem separation job submitted: {job_id}")

        # Wait for completion
        job = await self._wait_for_job(job_id, progress_callback)

        result_urls = job.get("result_urls", [])
        if not result_urls:
            raise RuntimeError("No stem files in result")

        # Detect mode based on returned stems
        # 4-stem mode returns: {job_id}_drums.wav, {job_id}_bass.wav, {job_id}_vocals.wav, {job_id}_other.wav
        # 2-stem mode returns: {job_id}_vocals.wav, {job_id}_accompaniment.wav
        has_drums = any("_drums" in url for url in result_urls)

        if has_drums and len(result_urls) >= 4:
            # 4-stem mode: drums, bass, vocals, other
            drums_path = output_dir / f"drums_{job_id[:8]}.wav"
            bass_path = output_dir / f"bass_{job_id[:8]}.wav"
            vocals_path = output_dir / f"vocals_{job_id[:8]}.wav"
            other_path = output_dir / f"other_{job_id[:8]}.wav"

            for url in result_urls:
                if "_drums" in url:
                    await self._download_file(url, drums_path)
                elif "_bass" in url:
                    await self._download_file(url, bass_path)
                elif "_vocals" in url:
                    await self._download_file(url, vocals_path)
                elif "_other" in url:
                    await self._download_file(url, other_path)

            logger.info(f"4-stem separation complete: drums={drums_path}, bass={bass_path}, "
                       f"vocals={vocals_path}, other={other_path}")

            return MusicStems(
                vocals=vocals_path,
                drums=drums_path,
                bass=bass_path,
                other=other_path,
            )
        else:
            # 2-stem mode: vocals + accompaniment
            if len(result_urls) < 2:
                raise RuntimeError("Expected at least 2 stem files in result")

            vocals_path = output_dir / f"vocals_{job_id[:8]}.wav"
            accompaniment_path = output_dir / f"accompaniment_{job_id[:8]}.wav"

            for url in result_urls:
                if "_vocals" in url:
                    await self._download_file(url, vocals_path)
                elif "_accompaniment" in url:
                    await self._download_file(url, accompaniment_path)

            logger.info(f"2-stem separation complete: vocals={vocals_path}, accompaniment={accompaniment_path}")

            return MusicStems(vocals=vocals_path, accompaniment=accompaniment_path)

    async def generate_soundtrack(
        self,
        base_prompt: str,
        scenes: list[dict],
        output_dir: Path | None = None,
        progress_callback: callable | None = None,
        crossfade_duration: float = 2.0,
    ) -> Path:
        """Generate a multi-scene soundtrack.

        Args:
            base_prompt: Base musical description
            scenes: List of {"mood": str, "duration": int, "prompt": str | None}
            output_dir: Directory for output file
            progress_callback: Optional callback(status, progress, detail)
            crossfade_duration: Duration of crossfade between scenes (0.5-5.0)

        Returns:
            Path to generated soundtrack WAV file
        """
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="musicgen_"))

        # Submit soundtrack job
        job_id = await self._submit_job("/generate/soundtrack", {
            "base_prompt": base_prompt,
            "scenes": scenes,
            "use_beat_aligned_crossfade": True,
            "crossfade_duration": crossfade_duration,
        })

        logger.info(f"Soundtrack generation job submitted: {job_id}")

        # Wait for completion
        job = await self._wait_for_job(job_id, progress_callback)

        # Download result
        if not job.get("result_urls"):
            raise RuntimeError("No result files in completed job")

        result_url = job["result_urls"][0]
        output_path = output_dir / f"soundtrack_{job_id[:8]}.wav"
        await self._download_file(result_url, output_path)

        logger.info(f"Soundtrack generated: {output_path}")
        return output_path


# =============================================================================
# High-Level Music Generation
# =============================================================================

# Music generation parameters for variety
# Keep prompts SIMPLE but SPECIFIC - emphasize modern/upbeat sound
# Explicitly avoid classical/orchestral to prevent mellow output
MUSIC_PROMPTS = [
    "upbeat lo-fi hip hop beat, bouncy drums, warm synths, feel-good groove, no vocals",
    "modern pop electronic, punchy drums, synth bass, bright and fun, instrumental",
    "happy indie electronic, driving beat, synth melody, uplifting energy, no singing",
    "feel-good future bass, energetic drops, warm pads, joyful and bouncy, instrumental",
    "upbeat chillwave pop, groovy bassline, sparkly synths, positive vibes, no vocals",
    "modern tropical house, steel drums, bouncy beat, sunny and fun, instrumental only",
]


def _get_base_prompt(variation: int = 0, seed: int | None = None) -> str:
    """Generate a simple prompt for music generation.

    Uses simple, clear prompts to avoid artifacts from overly complex descriptions.

    Args:
        variation: Variation index for deterministic variety
        seed: Optional random seed for reproducibility

    Returns:
        Simple prompt string for MusicGen
    """
    # Use seed for reproducibility if provided
    rng = random.Random(seed + variation) if seed is not None else random.Random(variation * 42)
    return rng.choice(MUSIC_PROMPTS)


async def generate_music_for_video(
    timeline: VideoTimeline,
    output_dir: Path,
    config: MusicGenConfig | None = None,
    progress_callback: callable | None = None,
    crossfade_duration: float = 2.0,
    hemisphere: str = "north",
) -> MusicGenerationResult:
    """Generate multiple music versions for a video with per-clip moods.

    Uses the soundtrack endpoint to generate music that transitions
    between moods matching each clip in the video.

    Args:
        timeline: Video timeline with per-clip mood information
        output_dir: Directory for output files
        config: MusicGen API configuration
        progress_callback: Optional callback(version, status, progress, detail)
        crossfade_duration: Duration of crossfade between mood sections
        hemisphere: "north" or "south" for seasonal prompt generation

    Returns:
        MusicGenerationResult with multiple versions
    """
    config = config or MusicGenConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build scenes from timeline (handles title, transitions, ending, buffer, seasonal prompts)
    scenes = timeline.build_scenes(hemisphere=hemisphere)
    total_duration = sum(s["duration"] for s in scenes)

    logger.info(f"Building soundtrack with {len(scenes)} scenes, total {total_duration}s")
    for i, scene in enumerate(scenes):
        logger.info(f"  Scene {i + 1}: {scene['mood']} ({scene['duration']}s)")

    versions: list[GeneratedMusic] = []

    # Determine primary mood for result (most common or first)
    primary_mood = scenes[0]["mood"] if scenes else "calm"

    async with MusicGenClient(config) as client:
        # Check API health
        health = await client.health_check()
        logger.info(f"MusicGen API: {health['status']}, device: {health['device']}")

        for i in range(config.num_versions):
            logger.info(f"Generating version {i + 1}/{config.num_versions}")

            base_prompt = _get_base_prompt(variation=i)

            def version_progress(status, progress, detail, version_idx=i):
                if progress_callback:
                    progress_callback(version_idx, status, progress, detail)

            # Always use soundtrack endpoint for mood transitions
            music_path = await client.generate_soundtrack(
                base_prompt=base_prompt,
                scenes=scenes,
                output_dir=output_dir,
                progress_callback=version_progress,
                crossfade_duration=crossfade_duration,
            )

            # Separate stems for intelligent ducking
            logger.info(f"Separating stems for version {i + 1}")
            stems = await client.separate_stems(
                music_path,
                output_dir=output_dir,
                progress_callback=version_progress,
            )

            versions.append(GeneratedMusic(
                version_id=i,
                full_mix=music_path,
                stems=stems,
                duration=float(total_duration),
                prompt=base_prompt,
                mood=primary_mood,
            ))

    return MusicGenerationResult(
        versions=versions,
        timeline=timeline,
        mood=primary_mood,
    )


# =============================================================================
# Sync Wrapper
# =============================================================================

def generate_music_sync(
    timeline: VideoTimeline,
    mood: str,
    output_dir: Path,
    config: MusicGenConfig | None = None,
    progress_callback: callable | None = None,
) -> MusicGenerationResult:
    """Synchronous wrapper for generate_music_for_video."""
    return asyncio.run(generate_music_for_video(
        timeline=timeline,
        mood=mood,
        output_dir=output_dir,
        config=config,
        progress_callback=progress_callback,
    ))
