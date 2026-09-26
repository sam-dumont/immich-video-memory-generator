"""Discover inference compute independently of video-encoding hardware."""

from __future__ import annotations

from contextlib import suppress
from importlib import import_module

import httpx

from immich_memories.config_models_inference import InferenceConfig


def inference_acceleration(inference: InferenceConfig) -> tuple[bool, str]:
    """Report the inference service's provider without loading models or sending pictures."""
    if not inference.enabled:
        return local_inference_acceleration()
    try:
        response = httpx.get(f"{inference.facts_base_url}/health", timeout=2.0)
        response.raise_for_status()
        body = response.json()
        provider = body.get("provider") if isinstance(body, dict) else None
        status = body.get("status") if isinstance(body, dict) else None
        if status == "ok" and provider == "CUDAExecutionProvider":
            return True, "The inference service reports CUDA"
        available, local_reason = local_inference_acceleration()
        return available, f"Service status {status}, provider {provider}. {local_reason}"
    except (httpx.HTTPError, ValueError) as exc:
        if not inference.fallback_to_local:
            raise ValueError("Cannot verify the required inference service's compute") from exc
        available, local_reason = local_inference_acceleration()
        return available, f"The inference service could not be verified. {local_reason}"


def local_inference_acceleration() -> tuple[bool, str]:
    """Inspect optional inference runtimes without loading any model weights."""
    with suppress(ImportError, OSError, RuntimeError):
        ort = import_module("onnxruntime")
        if "CUDAExecutionProvider" in ort.get_available_providers():
            import numpy as np

            # A CUDA wheel can be installed in a container with no exposed device.
            probe = ort.OrtValue.ortvalue_from_shape_and_type([1], np.float32, "cuda", 0)
            if probe.device_name().lower() == "cuda":
                return True, "The local ONNX runtime can allocate on CUDA"
    with suppress(ImportError, OSError):
        mlx = import_module("mlx.core")
        if mlx.metal.is_available():
            return True, "The local MLX runtime supports Metal"
    return False, "No supported local GPU runtime or GPU inference service is available"
