"""Open the triage engine from config, or nothing: heads never block a run."""

from __future__ import annotations

import logging
from pathlib import Path

from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.config_models_triage import TriageConfig
from immich_memories.triage.encoder import DinoEncoder
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle

logger = logging.getLogger(__name__)

# Trained on public data only (see docs/research/2026-09-02-triage-good-enough.md).
PUBLIC_BUNDLE_PATH = Path(__file__).parent / "bundled_heads" / "public-location-v1.npz"


def open_triage(config: TriageConfig, *, store_path: Path) -> TriageEngine | None:
    """Build the engine when triage is on and its weights are usable; else ``None``.

    Every failure short of a bug is a WARNING and a ``None``: a missing encoder,
    an ONNX Runtime that is not installed, a bundle trained on another encoder.
    The fact store is only created once the engine is known to open, so a
    disabled or broken triage leaves no file behind.
    """
    if not config.enabled:
        return None
    bundle_path = config.bundle_path or PUBLIC_BUNDLE_PATH
    for path in (config.encoder_path, bundle_path):
        if not path.is_file():
            logger.warning("triage: %s is missing; continuing without head facts", path)
            return None
    try:
        encoder = DinoEncoder.open(config.encoder_path)
        bundle = HeadBundle.load(bundle_path)
        return TriageEngine(encoder=encoder, bundle=bundle, store=HeadFactStore(store_path))
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.warning("triage: cannot open (%s); continuing without head facts", exc)
        return None
