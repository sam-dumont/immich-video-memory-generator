"""MusicGen API client for communicating with the MusicGen server.

Handles job submission, polling, file downloads, music generation,
stem separation, and soundtrack generation.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from immich_memories.audio.music_generator_models import MusicStems

logger = logging.getLogger(__name__)


# =============================================================================
# API Client Configuration
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


# =============================================================================
# API Client
# =============================================================================


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
        job_id = await self._submit_job(
            "/generate",
            {
                "prompt": prompt,
                "duration": min(120, max(10, duration)),  # Clamp to API limits
                "mood": mood,
            },
        )

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
        with audio_path.open("rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            resp = await self.client.post("/separate", files=files)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

        logger.info(f"Stem separation job submitted: {job_id}")

        # Wait for completion
        result_urls = (await self._wait_for_job(job_id, progress_callback)).get("result_urls", [])
        if not result_urls:
            raise RuntimeError("No stem files in result")

        return await self._download_stems(result_urls, job_id, output_dir)

    async def _download_stems(
        self, result_urls: list[str], job_id: str, output_dir: Path
    ) -> MusicStems:
        """Download and organize stem files from separation results.

        Args:
            result_urls: URLs to stem files from the API
            job_id: Job ID for naming output files
            output_dir: Directory for output files

        Returns:
            MusicStems with paths to downloaded files
        """
        # Detect mode based on returned stems
        has_drums = any("_drums" in url for url in result_urls)

        if has_drums and len(result_urls) >= 4:
            return await self._download_four_stems(result_urls, job_id, output_dir)
        return await self._download_two_stems(result_urls, job_id, output_dir)

    async def _download_four_stems(
        self, result_urls: list[str], job_id: str, output_dir: Path
    ) -> MusicStems:
        """Download 4-stem separation results (drums, bass, vocals, other)."""
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

        logger.info(
            f"4-stem separation complete: drums={drums_path}, bass={bass_path}, "
            f"vocals={vocals_path}, other={other_path}"
        )

        return MusicStems(
            vocals=vocals_path,
            drums=drums_path,
            bass=bass_path,
            other=other_path,
        )

    async def _download_two_stems(
        self, result_urls: list[str], job_id: str, output_dir: Path
    ) -> MusicStems:
        """Download 2-stem separation results (vocals, accompaniment)."""
        if len(result_urls) < 2:
            raise RuntimeError("Expected at least 2 stem files in result")

        vocals_path = output_dir / f"vocals_{job_id[:8]}.wav"
        accompaniment_path = output_dir / f"accompaniment_{job_id[:8]}.wav"

        for url in result_urls:
            if "_vocals" in url:
                await self._download_file(url, vocals_path)
            elif "_accompaniment" in url:
                await self._download_file(url, accompaniment_path)

        logger.info(
            f"2-stem separation complete: vocals={vocals_path}, accompaniment={accompaniment_path}"
        )

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
        job_id = await self._submit_job(
            "/generate/soundtrack",
            {
                "base_prompt": base_prompt,
                "scenes": scenes,
                "use_beat_aligned_crossfade": True,
                "crossfade_duration": crossfade_duration,
            },
        )

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
