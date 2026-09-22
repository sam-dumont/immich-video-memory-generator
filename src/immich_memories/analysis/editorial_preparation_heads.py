"""Required public context heads, using the existing verified DINO encoder."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.triage.encoder import DinoEncoder
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle

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
        raise FileNotFoundError(
            f"public heads need the pinned DINOv2 ONNX export at {encoder_path}; set triage.encoder"
        )
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
