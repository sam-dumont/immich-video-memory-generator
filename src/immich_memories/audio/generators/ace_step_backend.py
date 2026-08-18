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

See: https://github.com/ace-step/ACE-Step-1.5
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import suppress
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

# WHY: ACE-Step's MLX VAE decode grows the MLX buffer cache by ~0.8 GiB per
# second of audio inside one decode chunk, and on >64 GB Macs upstream picks an
# 82 s chunk (2048 latent frames). MLX only trims that cache near its default
# limit (~recommendedMaxWorkingSetSize, ~120 GiB on a 128 GB machine), so a
# 216 s track pushed the host process past 100 GiB and macOS killed it. A small
# chunk plus a hard cache limit keeps the same request around 50 GiB (measured:
# 108 GB -> 53 GB peak footprint). Peak *active* memory stays a few GiB either
# way, so the limit costs ~20% on VAE decode and nothing elsewhere.
_MLX_VAE_CHUNK_FRAMES = 256  # 25 latent frames/s -> ~10 s of audio per decode
_MLX_CACHE_LIMIT_BYTES = 4 * 1024**3


def _bound_mlx_memory() -> int:
    """Cap MLX allocator growth before ACE-Step constructs its handlers.

    Must run before ``AceStepHandler()``: ACE-Step reads the chunk override
    into a process-wide cached GPU config on first handler construction.
    Returns the VAE chunk size in latent frames that ACE-Step should use.
    """
    os.environ.setdefault("ACESTEP_MLX_VAE_CHUNK", str(_MLX_VAE_CHUNK_FRAMES))
    with suppress(ImportError):
        import mlx.core as mx  # type: ignore[import-not-found]

        mx.set_cache_limit(_MLX_CACHE_LIMIT_BYTES)
    try:
        return max(192, int(os.environ["ACESTEP_MLX_VAE_CHUNK"]))
    except ValueError:
        return _MLX_VAE_CHUNK_FRAMES


def _cast_mlx_decoder_to_bf16(handler: Any) -> None:
    """Run the MLX DiT at ACE-Step's reference GPU precision.

    ACE-Step keeps the torch model in fp32 on MPS (a torch-MPS workaround) and
    converts the MLX decoder from that copy, so the 4B XL decoder costs 15.5 GB
    instead of the 7.8 GB the CUDA path uses in bf16. Set
    ``IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32=1`` to keep fp32.
    """
    decoder = getattr(handler, "mlx_decoder", None)
    if decoder is None or os.environ.get("IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32") == "1":
        return
    with suppress(ImportError):
        import mlx.core as mx  # type: ignore[import-not-found]
        from mlx.utils import tree_map  # type: ignore[import-not-found]

        def _to_bf16(value: Any) -> Any:
            if isinstance(value, mx.array) and mx.issubdtype(value.dtype, mx.floating):
                return value.astype(mx.bfloat16)
            return value

        decoder.update(tree_map(_to_bf16, decoder.parameters()))
        mx.eval(decoder.parameters())
        mx.clear_cache()


def _release_mlx_cache() -> None:
    """Hand cached Metal buffers back to macOS once a generation finishes."""
    with suppress(ImportError):
        import mlx.core as mx  # type: ignore[import-not-found]

        mx.clear_cache()
        mx.synchronize()


def _release_runtime_memory() -> None:
    """Return the dropped runtime's GPU memory to the OS.

    WHY: dropping the handlers frees the tensors, but torch's MPS caching
    allocator keeps the freed heaps (~26 GB for XL/4B) until empty_cache(),
    so a UI process would sit at ~27 GB between generations instead of ~1 GB.
    """
    import gc

    gc.collect()
    with suppress(ImportError):
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    _release_mlx_cache()


def _is_ace_step_importable() -> bool:
    """Check if the ACE-Step 1.5 library API is importable."""
    try:
        import importlib.util

        return importlib.util.find_spec("acestep.handler") is not None
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
    """Run the ACE-Step pipeline with loguru and FutureWarnings suppressed.

    ACE-Step logs through loguru, which ignores stdlib logging config. Its tqdm
    bars are disabled via ``ACESTEP_DISABLE_TQDM`` (set in ``_init_pipeline``).
    The process's stderr is deliberately left alone: a native MLX/Metal failure
    prints its only diagnostic there.
    """
    import warnings

    # WHY: torch.nn.utils.weight_norm emits a FutureWarning on every model load
    warnings.filterwarnings("ignore", category=FutureWarning, module="torch")

    try:
        from loguru import logger as loguru_logger  # type: ignore[import-not-found]

        loguru_logger.disable("acestep")
    except ImportError:
        loguru_logger = None  # type: ignore[assignment]

    try:
        return pipeline_fn(**kwargs)
    finally:
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
    extra_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ACEStepV15Runtime:
    """Loaded ACE-Step 1.5 handlers and generation API."""

    dit_handler: Any
    llm_handler: Any | None
    generation_params_type: Any
    generation_config_type: Any
    generate_music: Any
    device: str
    lm_backend: str
    dit_model: str
    lm_model: str | None


def _initialize_dit_handler(
    handler_type: Any,
    *,
    project_root: Path,
    dit_model: str,
    device: str,
    offload: bool,
    use_mlx_dit: bool,
    mlx_vae_chunk: int | None = None,
) -> Any:
    """Initialize and validate the ACE-Step 1.5 DiT handler."""
    handler = handler_type()
    if mlx_vae_chunk is not None and hasattr(handler, "mlx_vae_chunk_size"):
        # WHY: belt and braces — the env override is ignored if ACE-Step's
        # global GPU config was already cached earlier in this process.
        handler.mlx_vae_chunk_size = mlx_vae_chunk
    status, initialized = handler.initialize_service(
        project_root=str(project_root),
        config_path=dit_model,
        device=device,
        use_flash_attention=False,
        compile_model=False,
        offload_to_cpu=offload,
        offload_dit_to_cpu=offload,
        quantization=None,
        use_mlx_dit=use_mlx_dit,
    )
    if not initialized:
        raise RuntimeError(f"ACE-Step DiT initialization failed: {status}")
    if use_mlx_dit:
        _cast_mlx_decoder_to_bf16(handler)
    return handler


def _initialize_lm_handler(
    handler_type: Any,
    ensure_model: Any,
    *,
    checkpoint_dir: Path,
    lm_model: str | None,
    lm_backend: str,
    device: str,
    offload: bool,
) -> Any | None:
    """Download, initialize, and validate the optional ACE-Step planner."""
    if not lm_model:
        return None

    downloaded, download_status = ensure_model(
        model_name=lm_model,
        checkpoints_dir=checkpoint_dir,
    )
    if not downloaded:
        raise RuntimeError(f"ACE-Step LM download failed: {download_status}")

    handler = handler_type()
    status, initialized = handler.initialize(
        checkpoint_dir=str(checkpoint_dir),
        lm_model_path=lm_model,
        backend=lm_backend,
        device=device,
        offload_to_cpu=offload,
        dtype=None,
    )
    if not initialized:
        raise RuntimeError(f"ACE-Step LM initialization failed: {status}")
    return handler


def _dit_model_name(variant: str) -> str:
    """Translate stable app variant names to ACE-Step 1.5 checkpoints."""
    if variant.startswith("acestep-v15-"):
        return variant
    return f"acestep-v15-{variant.replace('_', '-')}"


def _lm_model_name(size: str) -> str:
    """Translate stable app LM sizes to ACE-Step 1.5 checkpoints."""
    if size.startswith("acestep-5Hz-lm-"):
        return size
    return f"acestep-5Hz-lm-{size}"


def _local_runtime_target() -> tuple[str, str, bool]:
    """Return DiT device, LM backend, and MLX-DiT flag for this host."""
    import platform

    if platform.system() == "Darwin":
        return "mps", "mlx", True

    with suppress(ImportError, RuntimeError):
        import torch

        if torch.cuda.is_available():
            return "cuda", "vllm", False
    return "cpu", "pt", False


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


def _run_v15_generation(
    runtime: _ACEStepV15Runtime,
    params: Any,
    generation_config: Any,
    output_dir: Path,
) -> Any:
    """Run one ACE-Step 1.5 generation on the worker thread and release MLX buffers after."""
    try:
        return _run_with_suppressed_output(
            runtime.generate_music,
            dit_handler=runtime.dit_handler,
            llm_handler=runtime.llm_handler,
            params=params,
            config=generation_config,
            save_dir=str(output_dir),
        )
    finally:
        if runtime.device == "mps":
            _release_mlx_cache()


class ACEStepBackend(MusicGenerator):
    """ACE-Step 1.5 music generation backend.

    Prefers library mode for desktop usage. Falls back to API mode
    if the ace-step package isn't installed but a server is configured.
    """

    def __init__(self, config: ACEStepConfig | None = None):
        self.config = config or ACEStepConfig()
        self._pipeline: _ACEStepV15Runtime | None = None
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

        if self.config.mode == "lib" and _is_ace_step_importable():
            self._effective_mode = "lib"
        else:
            self._effective_mode = "api"

        return self._effective_mode

    def _init_pipeline(self):
        """Initialize the pinned ACE-Step 1.5 direct-library runtime."""
        if self._pipeline is not None:
            return

        try:
            import acestep  # type: ignore[import-not-found]
            from acestep.handler import AceStepHandler  # type: ignore[import-not-found]
            from acestep.inference import (  # type: ignore[import-not-found]
                GenerationConfig as V15GenerationConfig,
            )
            from acestep.inference import (  # type: ignore[import-not-found]
                GenerationParams as V15GenerationParams,
            )
            from acestep.inference import generate_music as generate_v15_music
            from acestep.llm_inference import LLMHandler  # type: ignore[import-not-found]
            from acestep.model_downloader import ensure_lm_model  # type: ignore[import-not-found]

            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            # WHY: tqdm bars were the reason stderr used to be redirected to
            # /dev/null; upstream honours this switch, so stderr stays visible.
            os.environ.setdefault("ACESTEP_DISABLE_TQDM", "1")
            os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
            os.environ.setdefault(
                "ACESTEP_CHECKPOINTS_DIR",
                str(Path.home() / ".cache" / "ace-step" / "checkpoints"),
            )

            package_file = getattr(acestep, "__file__", None)
            if not package_file:
                raise RuntimeError("Cannot locate the installed ACE-Step 1.5 package")
            project_root = Path(package_file).resolve().parent.parent
            checkpoint_dir = Path(os.environ["ACESTEP_CHECKPOINTS_DIR"]).expanduser()
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

            device, lm_backend, use_mlx_dit = _local_runtime_target()
            mlx_vae_chunk = _bound_mlx_memory() if use_mlx_dit else None
            dit_model = _dit_model_name(self.config.model_variant)
            lm_model = _lm_model_name(self.config.lm_model_size) if self.config.use_lm else None
            offload = bool(self.config.extra_args.get("cpu_offload", False)) and not (
                device == "mps" or self.config.disable_offload
            )

            logger.info(
                "Initializing ACE-Step 1.5 locally: model=%s device=%s lm_backend=%s",
                dit_model,
                device,
                lm_backend if lm_model else "disabled",
            )

            dit_handler = _initialize_dit_handler(
                AceStepHandler,
                project_root=project_root,
                dit_model=dit_model,
                device=device,
                offload=offload,
                use_mlx_dit=use_mlx_dit,
                mlx_vae_chunk=mlx_vae_chunk,
            )
            llm_handler = _initialize_lm_handler(
                LLMHandler,
                ensure_lm_model,
                checkpoint_dir=checkpoint_dir,
                lm_model=lm_model,
                lm_backend=lm_backend,
                device=device,
                offload=offload,
            )

            self._pipeline = _ACEStepV15Runtime(
                dit_handler=dit_handler,
                llm_handler=llm_handler,
                generation_params_type=V15GenerationParams,
                generation_config_type=V15GenerationConfig,
                generate_music=generate_v15_music,
                device=device,
                lm_backend=lm_backend,
                dit_model=dit_model,
                lm_model=lm_model,
            )
            logger.info("ACE-Step 1.5 local runtime initialized")
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

        # ACE-Step 1.5 base = 50 steps; turbo = 8 distilled steps.
        is_turbo = self.config.model_variant.endswith("turbo")
        infer_step = 8 if is_turbo else 50
        timestep_shift = 3.0 if is_turbo else 1.0

        runtime = self._pipeline
        assert isinstance(runtime, _ACEStepV15Runtime)
        params = runtime.generation_params_type(
            caption=caption_result.caption,
            lyrics="[Instrumental]",
            instrumental=True,
            bpm=caption_result.bpm,
            keyscale=caption_result.key_scale,
            timesignature=caption_result.time_signature,
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
            _run_v15_generation,
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
        had_runtime = self._pipeline is not None
        self._pipeline = None
        if had_runtime:
            _release_runtime_memory()
