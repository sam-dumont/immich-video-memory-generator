"""Loading and driving ACE-Step 1.5 in this process ("lib" mode).

Everything here is about the host: which device and precision the handlers get,
how much GPU memory MLX and torch are allowed to keep, and how one generation is
run and cleaned up after. The constants and the comments explaining them are
postmortems -- an unbounded MLX cache is what got the process SIGKILLed, and a
clamped decode chunk is what audibly smeared the output. Do not tune them from
first principles.

`ACEStepBackend` imports these names into `ace_step_backend` and calls them
there, so tests that patch `ace_step_backend.is_ace_step_importable` keep
working. Patching it here would not affect the backend -- `from x import y`
binds y in the importing module at import time.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.audio.generators.memory_budget import memory_shortfall

logger = logging.getLogger(__name__)

__all__ = [
    "ACEStepV15Runtime",
    "build_v15_runtime",
    "is_ace_step_importable",
    "release_runtime_memory",
    "run_v15_generation",
]

# WHY: ACE-Step's MLX VAE decode grows the MLX buffer cache by ~0.8 GiB per
# second of audio inside one decode chunk. MLX only trims that cache near its
# default limit (~120 GiB on a 128 GB machine), so a long track pushed the host
# process past 100 GiB and macOS killed it. A hard cache limit is what fixes
# that: measured with it in place, a 300 s render peaks at 38 GiB of MLX memory
# and 13 GiB RSS at ACE-Step's own chunk size.
#
# Do NOT also clamp the decode chunk. ACE-Step sizes it from unified memory
# (256 at <=16 GB, 512 at <=36 GB, 1024 at <=64 GB, else 2048) and the chunk sets
# how much temporal context each decode window sees — stride is chunk - 2*overlap.
# Forcing the 16 GB value on a large Mac cut the window from ~82 s to ~10 s and
# multiplied the blended boundaries, which audibly muddied the output.
_MLX_CACHE_LIMIT_BYTES = 4 * 1024**3


def _bound_mlx_memory() -> int | None:
    """Cap MLX allocator growth before ACE-Step constructs its handlers.

    Must run before ``AceStepHandler()``: ACE-Step reads the chunk override into a
    process-wide cached GPU config on first handler construction.

    Returns an explicit ``ACESTEP_MLX_VAE_CHUNK`` override when one is set, or
    None to leave the decode chunk to ACE-Step's own memory-based sizing.
    """
    with suppress(ImportError):
        import mlx.core as mx  # type: ignore[import-not-found]

        mx.set_cache_limit(_MLX_CACHE_LIMIT_BYTES)

    override = os.environ.get("ACESTEP_MLX_VAE_CHUNK")
    if override is None:
        return None
    try:
        return max(192, int(override))
    except ValueError:
        return None


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


def release_runtime_memory() -> None:
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


def is_ace_step_importable() -> bool:
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
    bars are disabled via ``ACESTEP_DISABLE_TQDM`` (set in ``build_v15_runtime``).
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
class ACEStepV15Runtime:
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


def build_v15_runtime(
    *,
    model_variant: str,
    lm_model_size: str,
    use_lm: bool,
    disable_offload: bool,
    cpu_offload: bool,
) -> ACEStepV15Runtime:
    """Initialize the pinned ACE-Step 1.5 direct-library runtime."""
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
        dit_model = _dit_model_name(model_variant)
        lm_model = _lm_model_name(lm_model_size) if use_lm else None
        offload = cpu_offload and not (device == "mps" or disable_offload)

        logger.info(
            "Initializing ACE-Step 1.5 locally: model=%s device=%s lm_backend=%s",
            dit_model,
            device,
            lm_backend if lm_model else "disabled",
        )

        shortfall = memory_shortfall(dit_model, lm_model)
        if shortfall is not None:
            raise RuntimeError(
                f"{shortfall}. Loading it anyway is what gets the process SIGKILLed by "
                "the OS mid-render. Free memory, use a smaller model_variant, or set "
                "use_lm: false."
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

        runtime = ACEStepV15Runtime(
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
        return runtime
    except Exception as e:  # WHY: plugin boundary — ACE-Step init can fail in many ways
        logger.error(f"Failed to initialize ACE-Step pipeline: {e}")
        raise


def run_v15_generation(
    runtime: ACEStepV15Runtime,
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
