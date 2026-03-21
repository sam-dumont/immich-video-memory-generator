#!/usr/bin/env python3
"""Validate all-local music pipeline on Apple Silicon.

Runs ACE-Step (lib mode) + Demucs (local) end-to-end with no API servers.
Use this to prove the "fully local" claim actually works.

Usage:
    uv run python scripts/validate_local_audio.py
    uv run python scripts/validate_local_audio.py --quality high  # base + 4B

Requirements:
    pip install 'immich-memories[demucs]'
    pip install acestep  # or: pip install git+https://github.com/sam-dumont/ace-step-1.5-turbo

Model cache locations:
    ACE-Step: ~/.cache/huggingface/hub/  (~2-8GB depending on lm_model_size)
    Demucs:   ~/.cache/torch/hub/        (~80MB for htdemucs)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import resource
import sys
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("validate_local_audio")


def check_dependencies() -> dict[str, bool]:
    """Check which audio ML packages are installed."""
    import importlib.util

    deps = {}
    for pkg in ("acestep", "demucs", "torch", "torchaudio"):
        deps[pkg] = importlib.util.find_spec(pkg) is not None
    return deps


def get_peak_memory_mb() -> float:
    """Get peak RSS in MB (macOS/Linux)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports in bytes, Linux in KB
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024 * 1024)
    return usage.ru_maxrss / 1024


async def run_acestep(output_dir: Path, quality: str) -> Path | None:
    """Generate music with ACE-Step lib mode."""
    try:
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest
    except ImportError:
        logger.error("immich-memories not installed")
        return None

    if quality == "high":
        config = ACEStepConfig(
            mode="lib",
            model_variant="base",
            lm_model_size="4B",
            use_lm=True,
            bf16=True,
        )
    else:
        config = ACEStepConfig(
            mode="lib",
            model_variant="turbo",
            lm_model_size="0.6B",
            use_lm=True,
            bf16=True,
        )

    logger.info(f"ACE-Step config: variant={config.model_variant}, lm={config.lm_model_size}")

    backend = ACEStepBackend(config)
    if not await backend.is_available():
        logger.error("ACE-Step not available (acestep package missing)")
        return None

    request = GenerationRequest(
        prompt="upbeat happy nostalgic",
        duration_seconds=15,
        output_dir=output_dir,
    )

    start = time.monotonic()
    result = await backend.generate(request)
    elapsed = time.monotonic() - start

    logger.info(f"ACE-Step generated: {result.audio_path} ({elapsed:.1f}s)")
    logger.info(f"  Peak memory: {get_peak_memory_mb():.0f} MB")

    # Release model memory
    await backend.__aexit__()
    return result.audio_path


async def run_demucs(audio_path: Path, output_dir: Path) -> bool:
    """Separate stems with local Demucs."""
    try:
        from immich_memories.audio.generators.demucs_local import DemucsLocalBackend
    except ImportError:
        logger.error("immich-memories not installed")
        return False

    backend = DemucsLocalBackend(model_name="htdemucs")
    if not await backend.is_available():
        logger.error("Demucs not available (demucs package missing)")
        return False

    start = time.monotonic()
    stems = await backend.separate_stems(audio_path, output_dir)
    elapsed = time.monotonic() - start

    logger.info(f"Demucs separated {4 if stems.has_full_stems else 2} stems ({elapsed:.1f}s)")
    logger.info(f"  vocals: {stems.vocals} ({stems.vocals.stat().st_size // 1024}KB)")
    if stems.drums:
        logger.info(f"  drums:  {stems.drums} ({stems.drums.stat().st_size // 1024}KB)")
    if stems.bass:
        logger.info(f"  bass:   {stems.bass} ({stems.bass.stat().st_size // 1024}KB)")
    if stems.other:
        logger.info(f"  other:  {stems.other} ({stems.other.stat().st_size // 1024}KB)")
    logger.info(f"  Peak memory: {get_peak_memory_mb():.0f} MB")

    backend.release()
    return True


async def main():
    parser = argparse.ArgumentParser(description="Validate all-local music pipeline")
    parser.add_argument(
        "--quality",
        choices=["low", "high"],
        default="low",
        help="low=turbo+0.6B (fast, CI-safe), high=base+4B (production quality)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "immich-local-audio-test",
        help="Output directory for generated files",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("  LOCAL AUDIO PIPELINE VALIDATION")
    logger.info("=" * 60)

    # 1. Check dependencies
    deps = check_dependencies()
    logger.info(f"Dependencies: {deps}")
    missing = [k for k, v in deps.items() if not v]
    if missing:
        logger.warning(f"Missing packages: {missing}")
        logger.info("Install with: pip install 'immich-memories[demucs]' acestep")

    # 2. ACE-Step generation
    logger.info("")
    logger.info("--- ACE-Step (lib mode) ---")
    audio_path = await run_acestep(output_dir, args.quality)

    # 3. Demucs stem separation
    if audio_path:
        logger.info("")
        logger.info("--- Demucs (local) ---")
        stems_dir = output_dir / "stems"
        success = await run_demucs(audio_path, stems_dir)
    else:
        success = False

    # 4. Summary
    logger.info("")
    logger.info("=" * 60)
    if audio_path and success:
        logger.info("  PASS: Full local pipeline works!")
        logger.info(f"  Peak memory: {get_peak_memory_mb():.0f} MB")
        logger.info(f"  Output: {output_dir}")
    else:
        logger.info("  FAIL: See errors above")
        sys.exit(1)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
