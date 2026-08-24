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

Loading and driving the local handlers lives in ace_step_runtime.py; this module
turns a GenerationRequest into a caption and a GenerationResult, and speaks
whichever of the two protocols the effective mode selects.

See: https://github.com/ace-step/ACE-Step-1.5
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from immich_memories.audio.generators.ace_step_captions import (
    build_ace_caption,
    build_ace_caption_structured,
)
from immich_memories.audio.generators.ace_step_runtime import (
    ACEStepV15Runtime,
    build_v15_runtime,
    is_ace_step_importable,
    release_runtime_memory,
    run_v15_generation,
)
from immich_memories.audio.generators.base import (
    GenerationRequest,
    GenerationResult,
    MusicGenerator,
)

logger = logging.getLogger(__name__)


@dataclass
class ACEStepConfig:
    """Configuration for ACE-Step backend."""

    mode: str = "api"  # "api" (default, no Python version constraints) or "lib"
    api_url: str = "http://localhost:8000"
    model_variant: str = "turbo"
    lm_model_size: str = "1.7B"
    use_lm: bool = True
    disable_offload: bool = False
    num_versions: int = 3
    hemisphere: str = "north"
    timeout_seconds: int = 3600  # 1 hour (local gen can be slow)
    extra_args: dict[str, Any] = field(default_factory=dict)


def _detect_season(mood: str) -> str | None:
    """Detect season from mood keywords."""
    mood_lower = mood.lower()
    if "holiday" in mood_lower or "festive" in mood_lower:
        return "holiday"
    if "winter" in mood_lower:
        return "winter"
    if "summer" in mood_lower or "sunny" in mood_lower:
        return "summer"
    if "spring" in mood_lower or "fresh" in mood_lower:
        return "spring"
    if "autumn" in mood_lower or "fall" in mood_lower or "cozy" in mood_lower:
        return "autumn"
    return None


def _mood_to_ace_prompt(mood: str, prompt: str = "") -> tuple[str, str]:
    """Convert a mood string to ACE-Step tags and lyrics format."""
    return build_ace_caption(mood, season=_detect_season(mood))


def _mood_to_structured_prompt(
    mood: str,
    scene_moods: list[str] | None = None,
    memory_type: str | None = None,
    cadence_seconds: float | None = None,
):
    """Convert mood to structured ACE-Step caption with explicit musical params.

    Returns ACECaptionResult with caption, lyrics, bpm, key_scale, time_signature.
    """
    return build_ace_caption_structured(
        mood,
        season=_detect_season(mood),
        cadence_seconds=cadence_seconds,
        scene_moods=scene_moods,
        memory_type=memory_type,
    )


class ACEStepBackend(MusicGenerator):
    """ACE-Step 1.5 music generation backend.

    Prefers library mode for desktop usage. Falls back to API mode
    if the ace-step package isn't installed but a server is configured.
    """

    def __init__(self, config: ACEStepConfig | None = None):
        self.config = config or ACEStepConfig()
        self._pipeline: ACEStepV15Runtime | None = None
        self._effective_mode: str | None = None

    @property
    def name(self) -> str:
        mode = self._effective_mode or self.config.mode
        return f"ACE-Step ({mode})"

    async def is_available(self) -> bool:
        """Check if ACE-Step is available in configured mode."""
        if self.config.mode == "lib":
            if is_ace_step_importable():
                return True
            logger.warning(
                "ACE-Step library not installed (pip install 'ace-step @ "
                "git+https://github.com/ace-step/ACE-Step-1.5.git@v0.1.8'). "
                "Falling back to API at %s",
                self.config.api_url,
            )
            api_ok = await self._check_api()
            if not api_ok:
                logger.warning(
                    "ACE-Step unavailable: library not installed AND API at %s "
                    "unreachable. Configure a reachable URL in advanced.ace_step.api_url "
                    "or install the library.",
                    self.config.api_url,
                )
            return api_ok
        return await self._check_api()

    async def _check_api(self) -> bool:
        """Check if ACE-Step REST API server is reachable."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.config.api_url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("data", {}).get("status") == "ok":
                        return True
                    logger.warning(
                        "ACE-Step API at %s returned unhealthy: %s",
                        self.config.api_url,
                        data,
                    )
                    return False
                logger.warning(
                    "ACE-Step API at %s returned HTTP %d",
                    self.config.api_url,
                    resp.status_code,
                )
                return False
        except (OSError, RuntimeError, ValueError, ExceptionGroup) as exc:
            # WHY: anyio wraps connection failures in ExceptionGroup on Linux
            logger.warning("ACE-Step API at %s unreachable: %s", self.config.api_url, exc)
            return False

    def _get_effective_mode(self) -> str:
        """Determine which mode to actually use."""
        if self._effective_mode:
            return self._effective_mode

        if self.config.mode == "lib" and is_ace_step_importable():
            self._effective_mode = "lib"
        else:
            self._effective_mode = "api"

        return self._effective_mode

    def _init_pipeline(self):
        """Load the local ACE-Step 1.5 handlers once per backend instance."""
        if self._pipeline is not None:
            return

        self._pipeline = build_v15_runtime(
            model_variant=self.config.model_variant,
            lm_model_size=self.config.lm_model_size,
            use_lm=self.config.use_lm,
            disable_offload=self.config.disable_offload,
            cpu_offload=bool(self.config.extra_args.get("cpu_offload", False)),
        )

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
        return await self._generate_api(request, progress_callback)

    async def _generate_lib(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music using local ACE-Step library.

        Uses the official ACE-Step 1.5 handler API with structured captions.
        """
        import asyncio

        self._init_pipeline()

        request.output_dir.mkdir(parents=True, exist_ok=True)

        # Build structured prompt (same rich captions as API mode)
        if request.is_multi_scene:
            scene_moods = [s.get("mood", "upbeat") for s in request.scenes]
            primary_mood = scene_moods[0] if scene_moods else "upbeat"
            caption_result = _mood_to_structured_prompt(
                primary_mood,
                scene_moods=scene_moods,
                memory_type=request.memory_type,
                cadence_seconds=request.photo_cadence_seconds,
            )
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            caption_result = _mood_to_structured_prompt(
                request.prompt,
                memory_type=request.memory_type,
                cadence_seconds=request.photo_cadence_seconds,
            )
            duration = request.duration_seconds

        duration = min(duration, 300)  # Cap at 5 minutes
        output_path = request.output_dir / f"ace_step_v{request.variation_index}.wav"

        if progress_callback:
            progress_callback(
                "generating", 0, {"caption": caption_result.caption, "duration": duration}
            )

        # ACE-Step 1.5 base = 50 steps; turbo = 8 distilled steps.
        is_turbo = self.config.model_variant.endswith("turbo")
        infer_step = 8 if is_turbo else 50
        timestep_shift = 3.0 if is_turbo else 1.0

        runtime = self._pipeline
        assert isinstance(runtime, ACEStepV15Runtime)
        params = runtime.generation_params_type(
            caption=caption_result.caption,
            lyrics="[Instrumental]",
            instrumental=True,
            bpm=caption_result.bpm,
            # WHY: ACE-Step's guides recommend leaving key and meter for the model to
            # infer, and only pinning BPM (which the caption also states in tags).
            keyscale="",
            timesignature="",
            duration=float(duration),
            inference_steps=infer_step,
            guidance_scale=1.0 if is_turbo else 7.0,
            shift=timestep_shift,
            thinking=self.config.use_lm,
            use_cot_metas=self.config.use_lm,
            use_cot_caption=self.config.use_lm,
            use_cot_language=False,
        )
        generation_config = runtime.generation_config_type(
            batch_size=1,
            use_random_seed=True,
            audio_format="wav",
        )

        loop = asyncio.get_running_loop()
        upstream_result = await loop.run_in_executor(
            None,
            run_v15_generation,
            runtime,
            params,
            generation_config,
            request.output_dir,
        )

        if not getattr(upstream_result, "success", False):
            error = getattr(upstream_result, "error", "unknown ACE-Step error")
            raise RuntimeError(f"ACE-Step generation failed: {error}")
        audios = getattr(upstream_result, "audios", None) or []
        if not audios or not audios[0].get("path"):
            raise RuntimeError("ACE-Step generation returned no audio file")

        upstream_path = Path(audios[0]["path"])
        if not upstream_path.exists():
            raise RuntimeError(f"ACE-Step output does not exist: {upstream_path}")
        if upstream_path.resolve() != output_path.resolve():
            shutil.copy2(upstream_path, output_path)

        if not output_path.exists():
            raise RuntimeError(f"ACE-Step did not produce output at {output_path}")

        if progress_callback:
            progress_callback("completed", 100, {})

        logger.info(f"ACE-Step generated: {output_path} ({duration}s, {infer_step} steps)")

        return GenerationResult(
            audio_path=output_path,
            duration_seconds=float(duration),
            prompt=caption_result.caption,
            backend_name=self.name,
            metadata={
                "caption": caption_result.caption,
                "lyrics": caption_result.lyrics,
                "bpm": caption_result.bpm,
                "key_scale": caption_result.key_scale,
                "infer_step": infer_step,
                "timestep_shift": timestep_shift,
                "model_variant": self.config.model_variant,
                "mode": "lib",
            },
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
        import httpx

        request.output_dir.mkdir(parents=True, exist_ok=True)

        if request.is_multi_scene:
            scene_moods = [s.get("mood", "upbeat") for s in request.scenes]
            primary_mood = scene_moods[0] if scene_moods else "upbeat"
            caption_result = _mood_to_structured_prompt(
                primary_mood,
                scene_moods=scene_moods,
                memory_type=request.memory_type,
                cadence_seconds=request.photo_cadence_seconds,
            )
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            caption_result = _mood_to_structured_prompt(
                request.prompt,
                memory_type=request.memory_type,
                cadence_seconds=request.photo_cadence_seconds,
            )
            duration = request.duration_seconds

        duration = min(duration, 300)  # Cap at 5 minutes

        if progress_callback:
            progress_callback("Submitting task...", 0, {})

        headers: dict[str, str] = {}
        if self.config.extra_args.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config.extra_args['api_key']}"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=None),
            headers=headers,
        ) as client:
            task_payload = {
                "caption": caption_result.caption,
                "lyrics": caption_result.lyrics,
                "duration": duration,
                "batch_size": 1,
                "audio_format": "wav",
                "instrumental": True,
                "bpm": caption_result.bpm,
                "keyscale": caption_result.key_scale,
                "timesignature": caption_result.time_signature,
            }

            resp = await client.post(f"{self.config.api_url}/release_task", json=task_payload)
            resp.raise_for_status()
            task_result = resp.json()
            task_id = task_result.get("data", {}).get("task_id") or task_result.get("task_id")

            if not task_id:
                raise RuntimeError(f"No task_id in ACE-Step response: {task_result}")

            logger.info(f"ACE-Step task submitted: {task_id}")

            output_path = request.output_dir / f"ace_step_v{request.variation_index}.wav"
            await self._poll_and_download(client, task_id, output_path, progress_callback)

        if progress_callback:
            progress_callback("Complete!", 100, {})

        logger.info(f"ACE-Step API generated: {output_path} ({duration}s)")

        return GenerationResult(
            audio_path=output_path,
            duration_seconds=float(duration),
            prompt=caption_result.caption,
            backend_name=self.name,
            metadata={
                "caption": caption_result.caption,
                "lyrics": caption_result.lyrics,
                "bpm": caption_result.bpm,
                "key_scale": caption_result.key_scale,
                "time_signature": caption_result.time_signature,
                "mode": "api",
                "task_id": task_id,
            },
        )

    async def _poll_and_download(
        self,
        client: Any,
        task_id: str,
        output_path: Path,
        progress_callback: Any | None,
    ) -> None:
        """Poll ACE-Step API for task completion and download the result."""
        import asyncio
        import time

        if progress_callback:
            progress_callback("LLM reasoning...", 5, {"task_id": task_id})

        start_time = time.time()

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
                    await self._download_result(
                        client, result_item, output_path, poll_data, progress_callback
                    )
                    return

            self._report_estimated_progress(time.time() - start_time, progress_callback)
            await asyncio.sleep(3.0)

    async def _download_result(
        self,
        client: Any,
        result_item: dict,
        output_path: Path,
        poll_data: dict,
        progress_callback: Any | None,
    ) -> None:
        """Download audio from a completed ACE-Step task."""
        import json as _json

        if progress_callback:
            progress_callback("Downloading audio...", 90, {})

        result_files = _json.loads(result_item.get("result", "[]"))
        if not result_files:
            raise RuntimeError(f"No files in completed task: {poll_data}")

        file_url = result_files[0].get("file", "")
        if not file_url:
            raise RuntimeError(f"No file URL in result: {result_files[0]}")

        audio_resp = await client.get(f"{self.config.api_url}{file_url}")
        audio_resp.raise_for_status()
        output_path.write_bytes(audio_resp.content)

    @staticmethod
    def _report_estimated_progress(elapsed: float, progress_callback: Any | None) -> None:
        """Report estimated progress based on elapsed time."""
        if not progress_callback:
            return

        if elapsed < 8:
            phase = "LLM reasoning..."
            pct = min(15, int(5 + elapsed))
        elif elapsed < 30:
            phase = "Generating audio (diffusion)..."
            pct = min(80, int(15 + (elapsed - 8) * 2.95))
        else:
            phase = "Decoding audio..."
            pct = min(89, int(80 + (elapsed - 30) * 0.3))

        progress_callback(phase, pct, {})

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
            info["available"] = is_ace_step_importable()
            info["lib_installed"] = is_ace_step_importable()
        else:
            info["available"] = await self._check_api()
            info["api_url"] = self.config.api_url

        return info

    async def __aexit__(self, *args):
        had_runtime = self._pipeline is not None
        self._pipeline = None
        if had_runtime:
            release_runtime_memory()
