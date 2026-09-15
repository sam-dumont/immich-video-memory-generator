"""Setup metadata keeps editorial producers available in installed distributions."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement


def test_editorial_extra_declares_each_optional_runtime_and_all_includes_it():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    extras = project["project"]["optional-dependencies"]
    assert {Requirement(value).name for value in extras["editorial"]} == {
        "onnxruntime",
        "huggingface-hub",
        "kaldi-native-fbank",
    }
    assert "immich-memories[editorial]" in extras["all"]
    assert "immich-memories[editorial]" in extras["all-mac"]


def test_a_cpu_install_resolves_no_cuda_wheel_and_no_torch_family():
    """The device split's whole point: `editorial` on a CPU box pulls neither.

    The torch family is what used to drag fifteen `nvidia-*` wheels into a Linux
    resolution, for one 5.6M-parameter classifier that now runs as an ONNX graph.
    """
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    extras = project["project"]["optional-dependencies"]
    cpu = {Requirement(value).name for value in extras["editorial"]}

    assert not cpu & {"torch", "torchvision", "timm", "onnxruntime-gpu"}
    assert {Requirement(value).name for value in extras["editorial-cuda"]} == {
        "onnxruntime-gpu",
        "huggingface-hub",
        "kaldi-native-fbank",
    }
    # The CUDA variant replaces the CPU one; two distributions owning the same
    # import name must never be resolved into one environment.
    assert "immich-memories[editorial-cuda]" not in extras["all"]
    assert "immich-memories[editorial-cuda]" not in extras["all-mac"]


def test_worker_is_self_contained_for_detector_only_python_environments():
    import ast

    path = (
        Path(__file__).parents[1]
        / "src/immich_memories/analysis/editorial_preparation_detectors.py"
    )
    tree = ast.parse(path.read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(module and module.startswith("immich_memories") for module in imports)
