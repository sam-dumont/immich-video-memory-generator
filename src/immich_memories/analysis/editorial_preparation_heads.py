"""Required public context heads, using the existing verified DINO encoder."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
from PIL import UnidentifiedImageError

from immich_memories.analysis.editorial_clip_frames import clip_frames_fact
from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.triage.encoder import DinoEncoder
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle
from immich_memories.triage.preprocess import preprocess_image_bytes

PUBLIC_HEAD_VERSIONS = {
    "activity": "public-v1",
    "children": "public-v1",
    "location": "public-v1",
    "people": "public-v1",
    "venue": "oi-v3",
    # Distilled from a typed picture reader's answers on the same public corpus. The two
    # binary heads carry their operating band in their coefficients; `public-v1-strict`
    # says so in the version string, because the band is what makes `screen` add-only.
    "frame_kind": "public-v1",
    "screen": "public-v1-strict",
    "uncovered_person": "public-v1",
}


def missing_encoder_message(encoder_path: Path) -> str:
    """The one sentence a missing encoder is reported with, in preflight and mid-run alike."""
    return (
        f"public heads need the pinned DINOv2 ONNX export at {encoder_path}. "
        "Run `immich-memories models fetch` to download it, or point "
        "advanced.triage.encoder at your copy of the export."
    )


def prepare_heads(
    *,
    asset_ids: Sequence[str],
    store_path: Path,
    bundle_path: Path,
    encoder_path: Path,
    head_versions: Mapping[str, str],
    preview_for: Callable[[str], bytes],
    batch_size: int,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
    provider: str = "auto",
) -> None:
    bundle = HeadBundle.load(bundle_path)
    supplied = {head.name: head.version for head in bundle.heads}
    if any(supplied.get(head) != version for head, version in head_versions.items()):
        raise ValueError(
            "configured head bundle does not contain the required public head versions"
        )
    # Refuse silent optional-triage fallback: preparation requires these facts.
    if not encoder_path.is_file():
        raise FileNotFoundError(missing_encoder_message(encoder_path))
    check_cancelled()
    encoder = DinoEncoder.open(encoder_path, provider=provider)
    store = HeadFactStore(store_path)
    try:
        engine = TriageEngine(encoder=encoder, bundle=bundle, store=store)
        for start in range(0, len(asset_ids), batch_size):
            check_cancelled()
            chunk = asset_ids[start : start + batch_size]
            engine.run(chunk, preview_for, batch_size=batch_size)
            progress("public_heads", start + len(chunk), len(asset_ids))
    finally:
        store.close()


def prepare_clip_frames(
    *,
    frame_paths: Mapping[str, Sequence[Path]],
    store_path: Path,
    bundle_path: Path,
    encoder_path: Path,
    check_cancelled: Callable[[], None],
    provider: str = "auto",
    open_encoder: Callable[..., DinoEncoder] = DinoEncoder.open,
) -> dict[str, str]:
    """Bank each clip's `clip_frames` fact from the frame head's reading of its sampled frames.

    Returns the clips whose frames could not be read, with why; they keep the reading of their
    preview, which is all any clip had before.
    """
    bundle = HeadBundle.load(bundle_path)
    encoder = open_encoder(encoder_path, provider=provider)
    if bundle.encoder_key != encoder.key:
        raise ValueError("head bundle was trained on another encoder")
    store = HeadFactStore(store_path)
    failures: dict[str, str] = {}
    try:
        for asset_id, paths in frame_paths.items():
            check_cancelled()
            try:
                pixels = np.stack([preprocess_image_bytes(Path(p).read_bytes()) for p in paths])
            except (OSError, ValueError, UnidentifiedImageError) as exc:
                failures[asset_id] = f"{type(exc).__name__}: {exc}"
                continue
            kinds = [fact.label for fact in bundle.decide(encoder.embed(pixels))["frame_kind"]]
            if fact := clip_frames_fact(kinds):
                store.remember_facts(asset_id, [fact], encoder_key=encoder.key)
    finally:
        store.close()
    return failures
