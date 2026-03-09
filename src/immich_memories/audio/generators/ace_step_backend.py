"""ACE-Step 1.5 music generation backend.

Supports two modes:
1. **Library mode** (preferred): Imports ace-step directly for local generation.
   Best for desktop/e2e usage. Requires `ace-step` package installed.
2. **API mode** (fallback): Connects to a remote ACE-Step Gradio server.
   Used when the package isn't installed locally but a server is available.

Hardware requirements (library mode):
- Apple Silicon: 16GB+ unified memory (M2/M3/M4), uses MLX backend
- NVIDIA GPU: 8GB+ VRAM (RTX 20-series+ recommended, Pascal with workarounds)
- CPU-only: Functional but very slow (8+ hours per song)

See: https://github.com/ace-step/ACE-Step
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from immich_memories.audio.generators.ace_step_captions import (
    ACE_CAPTION_TEMPLATES as ACE_CAPTION_TEMPLATES,  # noqa: PLC0414 — re-exported
)
from immich_memories.audio.generators.ace_step_captions import (
    build_ace_caption,
)
from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)

logger = logging.getLogger(__name__)


def _is_ace_step_importable() -> bool:
    """Check if ace-step package is importable."""
    try:
        import importlib.util

        return importlib.util.find_spec("acestep") is not None
    except (ImportError, ModuleNotFoundError):
        return False


@dataclass
class ACEStepConfig:
    """Configuration for ACE-Step backend.

    Attributes:
        mode: 'lib' for local library, 'api' for remote Gradio server.
        api_url: Gradio server URL (only used in API mode).
        model_variant: 'turbo' (8 steps, fast) or 'base' (50 steps, quality).
        lm_model_size: Language model size: '0.6B', '1.7B', or '4B'.
        use_lm: Whether to use the language model for "thinking mode".
        disable_offload: Disable CPU offloading (only if you have plenty of VRAM).
        num_versions: Number of versions to generate for selection.
        hemisphere: 'north' or 'south' for seasonal prompts.
        timeout_seconds: Max time per generation.
        bf16: Use bfloat16 (requires Ampere+ GPU). Set False for Pascal GPUs.
    """

    mode: str = "lib"  # "lib" or "api"
    api_url: str = "http://localhost:8000"
    model_variant: str = "turbo"
    lm_model_size: str = "1.7B"
    use_lm: bool = True
    disable_offload: bool = False
    num_versions: int = 3
    hemisphere: str = "north"
    timeout_seconds: int = 3600  # 1 hour (local gen can be slow)
    bf16: bool = True
    extra_args: dict[str, Any] = field(default_factory=dict)


def _mood_to_ace_prompt(mood: str, prompt: str = "") -> tuple[str, str]:
    """Convert a mood string to ACE-Step tags and lyrics format."""
    # Detect season from mood keywords
    season = None
    mood_lower = mood.lower()
    if "holiday" in mood_lower or "festive" in mood_lower:
        season = "holiday"
    elif "winter" in mood_lower:
        season = "winter"
    elif "summer" in mood_lower or "sunny" in mood_lower:
        season = "summer"
    elif "spring" in mood_lower or "fresh" in mood_lower:
        season = "spring"
    elif "autumn" in mood_lower or "fall" in mood_lower or "cozy" in mood_lower:
        season = "autumn"

    return build_ace_caption(mood, season=season)


class ACEStepBackend(MusicGenerator):
    """ACE-Step 1.5 music generation backend.

    Prefers library mode for desktop usage. Falls back to API mode
    if the ace-step package isn't installed but a server is configured.
    """

    def __init__(self, config: ACEStepConfig | None = None):
        self.config = config or ACEStepConfig()
        self._pipeline = None  # Lazy-loaded ACE-Step pipeline
        self._effective_mode: str | None = None

    @property
    def name(self) -> str:
        mode = self._effective_mode or self.config.mode
        return f"ACE-Step ({mode})"

    async def is_available(self) -> bool:
        """Check if ACE-Step is available in configured mode."""
        if self.config.mode == "lib":
            if _is_ace_step_importable():
                return True
            # Fallback: check API mode
            logger.info("ACE-Step lib not found, checking API fallback...")
            return await self._check_api()
        else:
            return await self._check_api()

    async def _check_api(self) -> bool:
        """Check if ACE-Step REST API server is reachable."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.config.api_url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", {}).get("status") == "ok"
                return False
        except Exception:
            return False

    def _get_effective_mode(self) -> str:
        """Determine which mode to actually use."""
        if self._effective_mode:
            return self._effective_mode

        if self.config.mode == "lib" and _is_ace_step_importable():
            self._effective_mode = "lib"
        else:
            self._effective_mode = "api"

        return self._effective_mode

    def _init_pipeline(self):
        """Initialize the ACE-Step pipeline for library mode.

        This lazy-loads the model to avoid memory usage until generation
        is actually requested.
        """
        if self._pipeline is not None:
            return

        try:
            from acestep.pipeline import ACEStepPipeline  # type: ignore[import-not-found]

            logger.info("Initializing ACE-Step pipeline...")

            # Set environment for Apple Silicon compatibility
            import platform

            if platform.system() == "Darwin":
                os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
                os.environ.setdefault("ACESTEP_LM_BACKEND", "mlx")

            # Configure bf16 for Pascal GPU compatibility
            if not self.config.bf16:
                os.environ["ACESTEP_BF16"] = "false"

            self._pipeline = ACEStepPipeline(
                model_variant=self.config.model_variant,
                lm_model_size=self.config.lm_model_size,
                use_lm=self.config.use_lm,
            )

            logger.info(
                f"ACE-Step pipeline initialized: variant={self.config.model_variant}, "
                f"lm={self.config.lm_model_size if self.config.use_lm else 'disabled'}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize ACE-Step pipeline: {e}")
            raise

    async def generate(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music using ACE-Step.

        In library mode, runs the pipeline directly.
        In API mode, calls the ACE-Step REST API.
        """
        mode = self._get_effective_mode()

        if mode == "lib":
            return await self._generate_lib(request, progress_callback)
        else:
            return await self._generate_api(request, progress_callback)

    async def _generate_lib(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music using local ACE-Step library."""
        import asyncio

        self._init_pipeline()

        request.output_dir.mkdir(parents=True, exist_ok=True)

        # Build prompt from scenes or use direct prompt
        if request.is_multi_scene:
            # Combine scene moods into a single prompt with the dominant mood
            combined_mood = ", ".join(s.get("mood", "upbeat") for s in request.scenes[:3])
            tags, lyrics = _mood_to_ace_prompt(combined_mood, request.prompt)
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            tags, lyrics = _mood_to_ace_prompt(request.prompt)
            duration = request.duration_seconds

        if progress_callback:
            progress_callback("generating", 0, {"tags": tags, "duration": duration})

        # Run pipeline in executor to avoid blocking the event loop
        def _run_pipeline():
            assert self._pipeline is not None
            return self._pipeline.generate(
                tags=tags,
                lyrics=lyrics,
                duration=min(duration, 300),  # Cap at 5 minutes
                batch_size=1,
                **self.config.extra_args,
            )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_pipeline)

        # Save output
        output_path = request.output_dir / f"ace_step_v{request.variation_index}.wav"

        # ACE-Step returns audio as numpy array or saves to file
        if isinstance(result, Path) or (isinstance(result, str) and Path(result).exists()):
            # Pipeline returned a file path
            import shutil

            shutil.copy2(str(result), str(output_path))
        elif hasattr(result, "audio"):
            # Pipeline returned an object with audio attribute
            self._save_audio(result.audio, result.sample_rate, output_path)
        else:
            # Assume numpy array with default sample rate
            self._save_audio(result, 48000, output_path)

        if progress_callback:
            progress_callback("completed", 100, {})

        logger.info(f"ACE-Step generated: {output_path} ({duration}s)")

        return GenerationResult(
            audio_path=output_path,
            duration_seconds=float(duration),
            prompt=f"[tags: {tags}] [lyrics: {lyrics}]",
            backend_name=self.name,
            metadata={"tags": tags, "lyrics": lyrics, "model_variant": self.config.model_variant},
        )

    async def _generate_api(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music via ACE-Step v1.5 REST API.

        Uses the /release_task + /query_result polling pattern from the
        ACE-Step 1.5 REST API (launched with `acestep-api`).
        """
        import asyncio
        import time

        import httpx

        request.output_dir.mkdir(parents=True, exist_ok=True)

        if request.is_multi_scene:
            combined_mood = ", ".join(s.get("mood", "upbeat") for s in request.scenes[:3])
            tags, lyrics = _mood_to_ace_prompt(combined_mood, request.prompt)
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            tags, lyrics = _mood_to_ace_prompt(request.prompt)
            duration = request.duration_seconds

        duration = min(duration, 300)  # Cap at 5 minutes

        if progress_callback:
            progress_callback("submitting", 0, {})

        headers: dict[str, str] = {}
        if self.config.extra_args.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config.extra_args['api_key']}"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=None),
            headers=headers,
        ) as client:
            # Submit task (see docs/API_USAGE.md for field reference)
            task_payload = {
                "caption": tags,
                "lyrics": lyrics,
                "duration": duration,
                "batch_size": 1,
            }

            resp = await client.post(f"{self.config.api_url}/release_task", json=task_payload)
            resp.raise_for_status()
            task_result = resp.json()
            task_id = task_result.get("data", {}).get("task_id") or task_result.get("task_id")

            if not task_id:
                raise RuntimeError(f"No task_id in ACE-Step response: {task_result}")

            logger.info(f"ACE-Step task submitted: {task_id}")

            if progress_callback:
                progress_callback("generating", 10, {"task_id": task_id})

            # Poll for completion
            # NOTE: param is task_id_list (not task_id/task_ids).
            # Using the wrong name silently returns empty data with 200.
            start_time = time.time()
            output_path = request.output_dir / f"ace_step_v{request.variation_index}.wav"

            while True:
                if time.time() - start_time > self.config.timeout_seconds:
                    raise TimeoutError(
                        f"ACE-Step task {task_id} timed out after {self.config.timeout_seconds}s"
                    )

                poll_resp = await client.post(
                    f"{self.config.api_url}/query_result",
                    json={"task_id_list": [task_id]},
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()

                results = poll_data.get("data", [])
                if results:
                    result_item = results[0]
                    status = result_item.get("status", 0)

                    if status == 2:
                        raise RuntimeError(f"ACE-Step task {task_id} failed: {result_item}")

                    if status == 1:
                        # Task complete — parse result JSON string and download audio
                        import json as _json

                        result_files = _json.loads(result_item.get("result", "[]"))
                        if not result_files:
                            raise RuntimeError(f"No files in completed task: {poll_data}")

                        # Download first file — URL is relative (e.g. /v1/audio?path=...)
                        file_url = result_files[0].get("file", "")
                        if not file_url:
                            raise RuntimeError(f"No file URL in result: {result_files[0]}")

                        audio_resp = await client.get(f"{self.config.api_url}{file_url}")
                        audio_resp.raise_for_status()
                        output_path.write_bytes(audio_resp.content)
                        break

                if progress_callback:
                    elapsed = time.time() - start_time
                    progress_callback("generating", min(90, int(10 + elapsed / 3)), {})

                await asyncio.sleep(3.0)

        if progress_callback:
            progress_callback("completed", 100, {})

        logger.info(f"ACE-Step API generated: {output_path} ({duration}s)")

        return GenerationResult(
            audio_path=output_path,
            duration_seconds=float(duration),
            prompt=f"[tags: {tags}] [lyrics: {lyrics}]",
            backend_name=self.name,
            metadata={"tags": tags, "lyrics": lyrics, "mode": "api", "task_id": task_id},
        )

    @staticmethod
    def _save_audio(audio_data, sample_rate: int, output_path: Path):
        """Save audio numpy array to WAV file."""
        import numpy as np

        try:
            import soundfile as sf  # type: ignore[import-not-found]

            sf.write(str(output_path), audio_data, sample_rate)
        except ImportError:
            # Fallback: use scipy if available
            from scipy.io import wavfile  # type: ignore[import-untyped]

            if isinstance(audio_data, np.ndarray) and audio_data.dtype in (np.float32, np.float64):
                audio_data = (audio_data * 32767).astype(np.int16)
            wavfile.write(str(output_path), sample_rate, audio_data)

    async def health_check(self) -> dict[str, Any]:
        """Check ACE-Step availability and configuration."""
        mode = self._get_effective_mode()
        info: dict[str, Any] = {
            "backend": self.name,
            "configured_mode": self.config.mode,
            "effective_mode": mode,
            "model_variant": self.config.model_variant,
            "lm_model_size": self.config.lm_model_size,
            "use_lm": self.config.use_lm,
        }

        if mode == "lib":
            info["available"] = _is_ace_step_importable()
            info["lib_installed"] = _is_ace_step_importable()
        else:
            info["available"] = await self._check_api()
            info["api_url"] = self.config.api_url

        return info

    async def __aexit__(self, *args):
        # Release pipeline memory if loaded
        self._pipeline = None
