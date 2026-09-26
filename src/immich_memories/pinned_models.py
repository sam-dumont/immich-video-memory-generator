"""The digest-pinned model artifacts an install downloads, and the fetch itself.

One table, read from both sides: `immich-memories models fetch` prepares a
laptop from it, and the inference service seeds a cold cache volume from it. A
second copy of the URLs or the digests is how a service starts banking facts the
app cannot reproduce, so there is one.
"""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from immich_memories.analysis.editorial_preparation_detectors import MARQO_ONNX_SHA256
from immich_memories.config_models_editorial_preparation import MARQO_ONNX_URL
from immich_memories.config_models_triage import DINOV2_SMALL_ONNX_URL
from immich_memories.laya_checkpoints import LAYA_MLX_URL, LAYA_ONNX_URL
from immich_memories.triage.encoder import DINOV2_SMALL_ONNX_SHA256

# The largest pinned artifact is the 88 MB encoder; the cap only exists so a
# wrong URL cannot fill a disk.
MAX_MODEL_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PinnedModel:
    """One artifact: what to call it, where it comes from, and what it must hash to."""

    label: str
    url: str
    sha256: str


ENCODER = PinnedModel(
    "the pinned DINOv2 ONNX export", DINOV2_SMALL_ONNX_URL, DINOV2_SMALL_ONNX_SHA256
)
MARQO_ONNX = PinnedModel("the pinned Marqo ONNX export", MARQO_ONNX_URL, MARQO_ONNX_SHA256)
# Two exports of the Apache-2.0 audience checkpoint, fetched by gpu/full or --laya.
LAYA_AUDIENCE = PinnedModel(
    "the pinned Laya audience checkpoint",
    LAYA_MLX_URL,
    "a79ad9fa3e4b5ae23e6b7ca9233f2bed50746a72baf0c3803dbecd68a6625dc4",
)
LAYA_AUDIENCE_ONNX = PinnedModel(
    "the pinned Laya audience ONNX checkpoint",
    LAYA_ONNX_URL,
    "90420ef38a5af3184bfbf9c3d5cdd030f1acac91d6dd911942d11b36e6c07c1a",
)
LAYA_MAX_BYTES = 1024 * 1024 * 1024

_CAPTION_BASE = (
    "https://huggingface.co/ggml-org/SmolVLM2-500M-Video-Instruct-GGUF/resolve/"
    "ccd7aae53bcb1997355c2f094959e72b3642ce17/"
)
CAPTION_MODEL = PinnedModel(
    "SmolVLM2-500M caption model (Q8)",
    _CAPTION_BASE + "SmolVLM2-500M-Video-Instruct-Q8_0.gguf",
    "6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc",
)
CAPTION_PROJECTOR = PinnedModel(
    "SmolVLM2-500M image projector (Q8)",
    _CAPTION_BASE + "mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf",
    "921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b",
)


def fetch_pinned_model(
    *,
    url: str,
    destination: Path,
    sha256: str,
    force: bool = False,
    max_bytes: int = MAX_MODEL_BYTES,
) -> str:
    """Put a digest-pinned model artifact at ``destination``; return what it took.

    ``"present"`` when the file already carries the pinned digest, ``"downloaded"``
    when it was fetched. The bytes are hashed in a temporary file and only renamed
    into place once they match, so a bad download never leaves a loadable path.
    """
    if not force and destination.is_file() and _digest_of(destination) == sha256:
        return "present"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.partial")
    try:
        digest = _stream_to(url, temporary, max_bytes=max_bytes)
        if digest != sha256:
            raise ValueError(f"{url}: digest {digest[:12]} is not the pinned {sha256[:12]}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "downloaded"


def _digest_of(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _stream_to(url: str, destination: Path, *, max_bytes: int) -> str:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"{url}: model downloads must be HTTP(S)")
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(  # noqa: S310 — the scheme is checked above
        url, headers={"User-Agent": "immich-memories"}
    )
    with (
        urllib.request.urlopen(  # noqa: S310 — the scheme is checked above
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response,
        destination.open("wb") as handle,
    ):
        while chunk := response.read(_CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                raise ValueError(f"{url}: refused past {max_bytes} bytes")
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()
