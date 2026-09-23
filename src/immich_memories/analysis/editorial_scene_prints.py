"""A frame's scene print: the pooled DINOv2 pack of its preview, banked like its hash.

The final review reads two frames as one scene when their prints agree (see
`editorial_final_hash_review`). The encoder is the pinned export every install already runs for
its public heads, so this reads no model the install does not have and asks nothing remote. A
print is read only for the frames a cut holds and the pictures offered to refill it, and banked
by the preview's contents, so a second run reads none.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import numpy as np

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections
from immich_memories.triage.contracts import PackEncoder
from immich_memories.triage.encoder import (
    DINOV2_SMALL_ID,
    DINOV2_SMALL_ONNX_SHA256,
    DinoEncoder,
)
from immich_memories.triage.preprocess import PREPROCESS_VERSION, preprocess_image_bytes

METHOD = f"scene-print-v1-{DINOV2_SMALL_ID}-{DINOV2_SMALL_ONNX_SHA256[:12]}-{PREPROCESS_VERSION}"
_SCHEMA = """CREATE TABLE IF NOT EXISTS scene_prints (
    source_key TEXT PRIMARY KEY, scene_print BLOB NOT NULL
)"""


class CachedScenePrints:
    """A preview's contents and the encoder identify its reusable print."""

    def __init__(
        self,
        cache_path: Path,
        read_preview: Callable[[str], bytes | None],
        *,
        open_encoder: Callable[[], PackEncoder | None],
    ) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.touch(mode=0o600, exist_ok=True)
        cache_path.chmod(0o600)
        self._connections = ThreadOwnedConnections(cache_path, _SCHEMA)
        self._read_preview = read_preview
        self._open_encoder = open_encoder
        self._encoder: PackEncoder | None = None
        self._encoder_opened = False

    def __call__(self, asset_id: str) -> np.ndarray | None:
        payload = self._read_preview(asset_id)
        if not payload:
            return None
        key = hashlib.sha256(f"{METHOD}\0".encode() + payload).hexdigest()
        with self._connections.connection() as connection:
            row = connection.execute(
                "SELECT scene_print FROM scene_prints WHERE source_key=?", (key,)
            ).fetchone()
            if row is not None:
                return np.frombuffer(row[0], dtype=np.float16).astype(np.float32)
            encoder = self._encoder_or_none()
            if encoder is None:
                return None
            pack = encoder.embed(preprocess_image_bytes(payload)[np.newaxis])[0]
            stored = np.asarray(pack, dtype=np.float16)
            connection.execute(
                "INSERT OR REPLACE INTO scene_prints VALUES (?, ?)", (key, stored.tobytes())
            )
            connection.commit()
            return stored.astype(np.float32)

    def _encoder_or_none(self) -> PackEncoder | None:
        if not self._encoder_opened:
            self._encoder_opened = True
            self._encoder = self._open_encoder()
        return self._encoder

    def close(self) -> None:
        self._connections.close()


def pinned_encoder(encoder_path: Path, provider: str) -> Callable[[], PackEncoder | None]:
    """Open the pinned export the public heads run on; None on an install without it."""

    def open_encoder() -> PackEncoder | None:
        if not encoder_path.is_file():
            return None
        return DinoEncoder.open(encoder_path, provider=provider)

    return open_encoder
