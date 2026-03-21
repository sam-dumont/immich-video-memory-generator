"""Local Demucs stem separation backend.

Runs Facebook Research's Demucs directly in-process — no API server needed.
Requires the `demucs` package: pip install 'immich-memories[demucs]'

Hardware:
- Apple Silicon: Uses MPS (Metal Performance Shaders) for GPU acceleration
- NVIDIA GPU: Uses CUDA
- CPU: Functional but slower (~10x vs GPU)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from immich_memories.audio.music_generator_models import MusicStems

logger = logging.getLogger(__name__)


def _is_demucs_importable() -> bool:
    """Check if the demucs package is installed."""
    try:
        import importlib.util

        return importlib.util.find_spec("demucs") is not None
    except (ImportError, ModuleNotFoundError):
        return False


def _detect_device() -> str:
    """Auto-detect best available device for Demucs inference."""
    from contextlib import suppress

    with suppress(ImportError):
        import torch

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    return "cpu"


class DemucsLocalBackend:
    """Local Demucs stem separation — direct Python import, no HTTP.

    Lazy-loads the htdemucs model on first use (~80MB download).
    Runs apply_model() in an executor to avoid blocking the event loop.
    """

    def __init__(self, model_name: str = "htdemucs", device: str | None = None):
        self._model_name = model_name
        self._device = device  # None = auto-detect
        self._model: Any = None

    @property
    def name(self) -> str:
        return f"Demucs (local, {self._model_name})"

    async def is_available(self) -> bool:
        return _is_demucs_importable()

    def _load_model(self) -> None:
        """Lazy-load the Demucs model. Called on first separation request."""
        if self._model is not None:
            return

        from demucs.pretrained import get_model  # type: ignore[import-not-found]

        logger.info(f"Loading Demucs model: {self._model_name}")
        self._model = get_model(self._model_name)

        device = self._device or _detect_device()
        if device != "cpu":
            import torch

            self._model.to(torch.device(device))

        logger.info(f"Demucs model loaded on {device}")

    async def separate_stems(
        self,
        audio_path: Path,
        output_dir: Path,
        progress_callback: Any | None = None,
    ) -> MusicStems:
        """Separate audio into 4 stems: drums, bass, other, vocals.

        Args:
            audio_path: Input audio file (WAV, MP3, etc.)
            output_dir: Directory for output stem files.
            progress_callback: Optional callback(status, progress, detail).

        Returns:
            MusicStems with paths to the 4 separated stem files.
        """
        import asyncio

        self._load_model()
        output_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback("Separating stems...", 10, {})

        device = self._device or _detect_device()

        def _run_separation() -> dict[str, Path]:
            import torchaudio  # type: ignore[import-untyped,import-not-found]
            from demucs.apply import apply_model  # type: ignore[import-untyped,import-not-found]

            wav, sr = torchaudio.load(str(audio_path))

            # Normalize to avoid clipping artifacts
            ref = wav.mean(0)
            wav_norm = (wav - ref.mean()) / (ref.std() + 1e-8)

            sources = apply_model(
                self._model,
                wav_norm[None].to(device),
                device=device,
            )
            # sources shape: (batch=1, num_sources=4, channels, samples)
            # htdemucs order: drums, bass, other, vocals
            sources = sources[0]  # Remove batch dim
            sources = sources * ref.std() + ref.mean()

            stem_names = self._model.sources  # e.g. ['drums', 'bass', 'other', 'vocals']
            paths: dict[str, Path] = {}
            for i, stem_name in enumerate(stem_names):
                out_path = output_dir / f"{stem_name}.wav"
                torchaudio.save(str(out_path), sources[i].cpu(), sr)
                paths[stem_name] = out_path

            return paths

        loop = asyncio.get_event_loop()
        paths = await loop.run_in_executor(None, _run_separation)

        if progress_callback:
            progress_callback("Stems separated", 100, {})

        logger.info(f"Demucs separated {len(paths)} stems to {output_dir}")

        return MusicStems(
            vocals=paths["vocals"],
            drums=paths.get("drums"),
            bass=paths.get("bass"),
            other=paths.get("other"),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": await self.is_available(),
            "model": self._model_name,
            "device": self._device or _detect_device(),
            "loaded": self._model is not None,
        }

    def release(self) -> None:
        """Release model memory."""
        self._model = None
