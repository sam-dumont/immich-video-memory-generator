"""DINOv2-small through ONNX Runtime, pooled into the pack the heads were trained on.

The encoder key binds features to (model revision, weights digest, preprocess
version, layout version); a bundle refuses to run on any other key.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from immich_memories.triage.preprocess import PREPROCESS_VERSION

LAYOUT_VERSION = "cls+mean+quad2x2/pca256/fp16"
TOKEN_DIM = 384
PATCH_GRID = 16
PACK_DIM = 6 * TOKEN_DIM
DINOV2_SMALL_ID = "facebook/dinov2-small@ed25f3a31f01632728cabb09d1542f84ab7b0056"
DINOV2_SMALL_ONNX_SHA256 = "478164cd290ee78e5ddb4fcc474136eec714b4b8253a3609cc7164b592e958af"


class TokenSession(Protocol):
    """The slice of an ORT InferenceSession the encoder uses."""

    def get_inputs(self) -> Sequence[Any]: ...

    def run(self, output_names: Any, input_feed: dict[str, np.ndarray]) -> Sequence[Any]: ...


def encoder_key(
    encoder_id: str,
    weights_sha256: str,
    *,
    preprocess_version: str = PREPROCESS_VERSION,
    layout_version: str = LAYOUT_VERSION,
) -> str:
    material = encoder_id + weights_sha256 + preprocess_version + layout_version
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def pool_token_pack(tokens: np.ndarray) -> np.ndarray:
    """Pool ``[CLS | patches]`` into CLS, mean, and four 8x8 quadrant means."""
    array = np.asarray(tokens, dtype=np.float32)
    expected = (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM)
    if array.ndim != 3 or array.shape[1:] != expected:
        raise ValueError(
            f"expected [batch, {expected[0]}, {expected[1]}] tokens, got {array.shape}"
        )
    cls = array[:, 0, :]
    patches = array[:, 1:, :].reshape(-1, PATCH_GRID, PATCH_GRID, TOKEN_DIM)
    pooled = [cls, patches.mean(axis=(1, 2))]
    half = PATCH_GRID // 2
    pooled.extend(
        quadrant.mean(axis=(1, 2))
        for quadrant in (
            patches[:, :half, :half, :],
            patches[:, :half, half:, :],
            patches[:, half:, :half, :],
            patches[:, half:, half:, :],
        )
    )
    return np.concatenate(pooled, axis=1).astype(np.float32, copy=False)


def _token_output(outputs: Sequence[Any]) -> np.ndarray:
    for output in outputs:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[1:] == (1 + PATCH_GRID * PATCH_GRID, TOKEN_DIM):
            return array.astype(np.float32, copy=False)
    shapes = [np.asarray(output).shape for output in outputs]
    raise ValueError(f"encoder did not return [batch, 257, 384] tokens; outputs={shapes}")


@dataclass(frozen=True)
class DinoEncoder:
    session: TokenSession
    key: str

    def embed(self, batch: np.ndarray) -> np.ndarray:
        """``[n, 3, 224, 224]`` pixels → ``[n, PACK_DIM]`` packs."""
        input_name = self.session.get_inputs()[0].name
        tokens = _token_output(self.session.run(None, {input_name: batch}))
        return pool_token_pack(tokens)

    @classmethod
    def open(cls, model_path: Path, *, provider: str = "auto") -> DinoEncoder:
        """Load the pinned DINOv2-small export; refuse any other graph."""
        with model_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != DINOV2_SMALL_ONNX_SHA256:
            raise RuntimeError(
                f"{model_path}: digest {digest[:12]} is not the pinned {DINOV2_SMALL_ID} export"
            )
        return cls(
            session=_create_session(model_path, provider),
            key=encoder_key(DINOV2_SMALL_ID, digest),
        )


# The execution providers each choice offers ONNX Runtime, in order.
_PROVIDER_CHAINS: dict[str, tuple[str, ...]] = {
    "cpu": ("CPUExecutionProvider",),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
    "coreml": ("CoreMLExecutionProvider", "CPUExecutionProvider"),
}


def provider_chain(provider: str, available: Collection[str]) -> tuple[str, ...]:
    """The providers to offer ORT for this choice, best first.

    `auto` takes CUDA where the EP is present and CPU everywhere else. It never
    takes CoreML, which is a deliberate exception rather than an oversight:
    measured on this export, the CoreML EP claims 274 of its 513 nodes and splits
    the graph into 87 partitions, so a tensor crosses the accelerator boundary
    dozens of times per image. It runs 6-8x slower than the CPU EP, holds 9x the
    resident memory, and gets *worse* as the batch grows while the CPU EP gets
    better. Naming `coreml` still selects it, so the measurement can be redone
    when the EP's partitioning improves.
    """
    if provider == "auto":
        provider = "cuda" if "CUDAExecutionProvider" in available else "cpu"
    chain = _PROVIDER_CHAINS.get(provider)
    if chain is None:
        raise ValueError(f"unknown provider: {provider}")
    if chain[0] not in available:
        raise RuntimeError(f"{chain[0]} is unavailable")
    return chain


def _create_session(model_path: Path, provider: str) -> Any:
    import onnxruntime as ort

    providers = list(provider_chain(provider, ort.get_available_providers()))
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 2) - 1))
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
