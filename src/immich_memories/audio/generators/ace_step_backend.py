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
    build_ace_caption_structured,
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

        return importlib.util.find_spec("acestep.pipeline_ace_step") is not None
    except (ImportError, ModuleNotFoundError):
        return False


def _validate_torchcodec() -> None:
    """Verify torchcodec is installed and version-compatible with torch.

    torchaudio 2.9+ delegates all I/O to torchcodec. The torchcodec minor
    version must match the torch minor version (e.g. torch 2.10 → torchcodec 0.10).
    """
    try:
        import torchcodec  # type: ignore[import-untyped,import-not-found]
    except ImportError:
        raise RuntimeError(
            "torchcodec is required for ACE-Step lib mode (torchaudio 2.9+ "
            "delegates all audio I/O to torchcodec). Install the version that "
            "matches your torch: pip install 'torchcodec==0.<torch_minor>.*' "
            "(e.g. torchcodec==0.10.* for torch 2.10)"
        ) from None

    import torch

    torch_minor = torch.__version__.split(".")[1]
    tc_minor = torchcodec.__version__.split(".")[1]
    if torch_minor != tc_minor:
        raise RuntimeError(
            f"torchcodec {torchcodec.__version__} is incompatible with "
            f"torch {torch.__version__} (minor versions must match). "
            f"Fix: pip install 'torchcodec==0.{torch_minor}.*'"
        )


def _run_with_suppressed_output(pipeline_fn, **kwargs):
    """Run ACE-Step pipeline with loguru, tqdm, and FutureWarnings suppressed.

    ACE-Step uses loguru (not stdlib logging) and tqdm progress bars that
    bypass our logging config. Suppress them at the FD level during generation.
    """
    import warnings

    # WHY: torch.nn.utils.weight_norm emits a FutureWarning on every model load
    warnings.filterwarnings("ignore", category=FutureWarning, module="torch")

    # WHY: ACE-Step uses loguru, which ignores stdlib logging config
    try:
        from loguru import logger as loguru_logger  # type: ignore[import-not-found]

        loguru_logger.disable("acestep")
    except ImportError:
        loguru_logger = None  # type: ignore[assignment]

    # WHY: tqdm writes progress bars to stderr, bypassing Python logging
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_err = os.dup(2)
    try:
        os.dup2(devnull_fd, 2)
        return pipeline_fn(**kwargs)
    finally:
        os.dup2(saved_err, 2)
        os.close(saved_err)
        os.close(devnull_fd)
        if loguru_logger is not None:
            loguru_logger.enable("acestep")


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
    bf16: bool = True
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
):
    """Convert mood to structured ACE-Step caption with explicit musical params.

    Returns ACECaptionResult with caption, lyrics, bpm, key_scale, time_signature.
    """
    return build_ace_caption_structured(
        mood,
        season=_detect_season(mood),
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
            logger.warning(
                "ACE-Step library not installed (pip install 'ace-step @ "
                "git+https://github.com/ace-step/ACE-Step.git'). "
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

        if self.config.mode == "lib" and _is_ace_step_importable():
            self._effective_mode = "lib"
        else:
            self._effective_mode = "api"

        return self._effective_mode

    def _init_pipeline(self):
        """Initialize the ACE-Step pipeline for library mode.

        This lazy-loads the model to avoid memory usage until generation
        is actually requested. The model (~3.5GB) is downloaded from
        HuggingFace on first use and cached at ~/.cache/ace-step/checkpoints.
        """
        if self._pipeline is not None:
            return

        try:
            _validate_torchcodec()

            # Official ACE-Step package uses pipeline_ace_step module
            from acestep.pipeline_ace_step import ACEStepPipeline  # type: ignore[import-not-found]

            logger.info("Initializing ACE-Step pipeline...")

            import platform

            if platform.system() == "Darwin":
                os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

            dtype = "bfloat16" if self.config.bf16 else "float32"

            self._pipeline = ACEStepPipeline(
                dtype=dtype,
                cpu_offload=not self.config.disable_offload
                and self.config.extra_args.get("cpu_offload", False),
            )

            logger.info(f"ACE-Step pipeline initialized: dtype={dtype}")
        except Exception as e:  # WHY: plugin boundary — ACE-Step init can fail in many ways
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
        return await self._generate_api(request, progress_callback)

    async def _generate_lib(
        self,
        request: GenerationRequest,
        progress_callback: Any | None = None,
    ) -> GenerationResult:
        """Generate music using local ACE-Step library.

        Uses the official ACE-Step __call__ API which handles model loading,
        diffusion, decoding, and saving in one call. Uses structured captions
        with dense instrument descriptions for best quality.
        """
        import asyncio

        self._init_pipeline()

        request.output_dir.mkdir(parents=True, exist_ok=True)

        # Build structured prompt (same rich captions as API mode)
        if request.is_multi_scene:
            scene_moods = [s.get("mood", "upbeat") for s in request.scenes]
            primary_mood = scene_moods[0] if scene_moods else "upbeat"
            caption_result = _mood_to_structured_prompt(
                primary_mood, scene_moods=scene_moods, memory_type=request.memory_type
            )
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            caption_result = _mood_to_structured_prompt(
                request.prompt, memory_type=request.memory_type
            )
            duration = request.duration_seconds

        duration = min(duration, 300)  # Cap at 5 minutes
        output_path = request.output_dir / f"ace_step_v{request.variation_index}.wav"

        if progress_callback:
            progress_callback(
                "generating", 0, {"caption": caption_result.caption, "duration": duration}
            )

        # base = 60 steps (high quality), turbo = 8 steps (fast preview)
        infer_step = 60 if self.config.model_variant == "base" else 8

        def _run_pipeline():
            assert self._pipeline is not None
            return _run_with_suppressed_output(
                self._pipeline,
                audio_duration=float(duration),
                prompt=caption_result.caption,
                lyrics=caption_result.lyrics,
                infer_step=infer_step,
                guidance_scale=15.0,
                scheduler_type="euler",
                cfg_type="apg",
                omega_scale=10.0,
                use_erg_tag=True,
                use_erg_lyric=True,
                use_erg_diffusion=True,
                batch_size=1,
                save_path=str(output_path),
                format="wav",
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_pipeline)

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
                "model_variant": self.config.model_variant,
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
                primary_mood, scene_moods=scene_moods, memory_type=request.memory_type
            )
            duration = sum(s.get("duration", 30) for s in request.scenes)
        else:
            caption_result = _mood_to_structured_prompt(
                request.prompt, memory_type=request.memory_type
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
            info["available"] = _is_ace_step_importable()
            info["lib_installed"] = _is_ace_step_importable()
        else:
            info["available"] = await self._check_api()
            info["api_url"] = self.config.api_url

        return info

    async def __aexit__(self, *args):
        # Release pipeline memory if loaded
        self._pipeline = None
