"""Platform defaults for the two exports of the same trained audience checkpoint."""

import platform
import sys

LAYA_MLX_NAME = "laya-audience-a79ad9fa.tar"
LAYA_ONNX_NAME = "laya-audience-onnx-90420ef3.tar.gz"
_RELEASE = (
    "https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v2/"
)
LAYA_MLX_URL = _RELEASE + LAYA_MLX_NAME
LAYA_ONNX_URL = _RELEASE + LAYA_ONNX_NAME


def default_laya_name() -> str:
    """Use MLX on Apple silicon and the portable ONNX export everywhere else."""
    return (
        LAYA_MLX_NAME
        if sys.platform == "darwin" and platform.machine() in {"arm64", "aarch64"}
        else LAYA_ONNX_NAME
    )


def default_laya_url() -> str:
    """The pinned release asset matching this platform's runtime."""
    return _RELEASE + default_laya_name()


def default_laya_path() -> str:
    """Keep both backend exports separately in the model cache."""
    return "~/.immich-memories/models/laya/" + default_laya_name()


def default_laya_threshold() -> float:
    """Use the threshold calibrated for the platform's default export."""
    return 0.186 if default_laya_name() == LAYA_MLX_NAME else 0.185
