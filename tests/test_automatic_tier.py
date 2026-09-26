"""The configured inference capability chooses preparation and selection together."""

import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml

from immich_memories.config_compute import local_inference_acceleration
from immich_memories.config_loader import Config


@pytest.fixture
def local_runtime_probe(monkeypatch, isolated_inference_compute):
    # WHY: these tests inspect simulated runtime APIs instead of the suite's CPU-only fixture.
    monkeypatch.setattr(
        "immich_memories.config_compute.local_inference_acceleration", local_inference_acceleration
    )


@pytest.mark.parametrize(
    "llm,tier,reader",
    [
        ({}, "gpu", "rules"),
        ({"model": "local-reader", "base_url": "http://localhost:9999/v1"}, "full", "model"),
    ],
)
def test_a_gpu_service_selects_the_profile_without_a_tier_setting(monkeypatch, llm, tier, reader):
    seen = []

    def health(url, **_kwargs):
        seen.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"status": "ok", "provider": "CUDAExecutionProvider"},
        )

    # WHY: discover the remote runtime through its real HTTP contract, without a GPU server.
    monkeypatch.setattr(httpx, "get", health)
    config = Config(inference={"facts_base_url": "http://gpu.test:8092"}, llm=llm)

    assert config.tier == tier
    assert config.editorial.reader == reader
    assert config.editorial.preparation.demands_captions
    assert config.editorial.laya_audience
    assert seen == ["http://gpu.test:8092/health"]


@pytest.mark.parametrize(
    "status,provider", [("ok", "CPUExecutionProvider"), ("degraded", "CUDAExecutionProvider")]
)
def test_a_service_without_healthy_gpu_compute_keeps_model_selection_off(
    monkeypatch, status, provider
):
    # WHY: emulate a CPU or unhealthy service at its HTTP boundary.
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **_kwargs: httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"status": status, "provider": provider},
        ),
    )
    config = Config(
        inference={"facts_base_url": "http://gpu.test:8092"},
        llm={"model": "local-reader", "base_url": "http://localhost:9999/v1"},
    )

    assert config.tier == "nas"
    assert config.editorial.reader == "rules"
    assert not config.editorial.preparation.demands_captions
    assert not config.editorial.laya_audience
    assert config.llm.model == "local-reader"


@pytest.mark.parametrize("runtime", ["cuda", "metal"])
def test_available_local_inference_compute_enables_light_refinement(
    monkeypatch, runtime, local_runtime_probe
):
    # WHY: optional inference runtimes stand in for the machine's GPU boundary.
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: [
                "CUDAExecutionProvider" if runtime == "cuda" else "CPUExecutionProvider"
            ],
            OrtValue=SimpleNamespace(
                ortvalue_from_shape_and_type=lambda *_args: SimpleNamespace(
                    device_name=lambda: "cuda"
                )
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx.core",
        SimpleNamespace(metal=SimpleNamespace(is_available=lambda: runtime == "metal")),
    )

    config = Config()

    assert config.tier == "gpu"
    assert config.editorial.preparation.demands_captions
    assert config.editorial.laya_audience
    assert config.editorial.reader == "rules"


def test_an_unreachable_optional_service_can_use_a_local_gpu(monkeypatch, local_runtime_probe):
    def unavailable(url, **kwargs):
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    # WHY: simulate network loss and an installed local CUDA runtime.
    monkeypatch.setattr(httpx, "get", unavailable)
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: ["CUDAExecutionProvider"],
            OrtValue=SimpleNamespace(
                ortvalue_from_shape_and_type=lambda *_args: SimpleNamespace(
                    device_name=lambda: "cuda"
                )
            ),
        ),
    )
    config = Config(inference={"facts_base_url": "http://gpu.test:8092"})

    assert config.tier == "gpu"
    assert config.editorial.preparation.demands_captions


def test_saving_does_not_pin_the_machine_observed_when_auto_was_loaded(monkeypatch, tmp_path):
    provider = "CUDAExecutionProvider"
    # WHY: the service can change between saving and reloading the same config.
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **_kwargs: httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"status": "ok", "provider": provider},
        ),
    )
    config = Config(inference={"facts_base_url": "http://gpu.test:8092"})
    assert config.tier == "gpu"
    path = tmp_path / "saved.yaml"
    config.save_yaml(path)

    assert "tier" not in yaml.safe_load(path.read_text())
    provider = "CPUExecutionProvider"
    reloaded = Config.from_yaml(path)

    assert reloaded.tier == "nas"
    assert not reloaded.editorial.preparation.demands_captions
    assert not reloaded.editorial.laya_audience


def test_a_required_offline_service_is_not_silently_downgraded(monkeypatch):
    def unavailable(url, **kwargs):
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    # WHY: network failure must honor the explicit no-local-fallback deployment policy.
    monkeypatch.setattr(httpx, "get", unavailable)
    with pytest.raises(ValueError, match="Cannot verify the required inference service"):
        Config(inference={"facts_base_url": "http://gpu.test", "fallback_to_local": False})


def test_a_cuda_wheel_without_a_usable_device_does_not_enable_gpu(monkeypatch, local_runtime_probe):
    def no_device(*_args, **_kwargs):
        raise RuntimeError("no CUDA device exposed")

    # WHY: the wheel can advertise CUDA while a container has no device access.
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: ["CUDAExecutionProvider"],
            OrtValue=SimpleNamespace(ortvalue_from_shape_and_type=no_device),
        ),
    )
    monkeypatch.setitem(
        sys.modules, "mlx.core", SimpleNamespace(metal=SimpleNamespace(is_available=lambda: False))
    )

    config = Config(llm={"model": "local-reader", "base_url": "http://localhost:9999/v1"})

    assert config.tier == "nas"
    assert not config.editorial.preparation.demands_captions


def test_explicit_nas_config_does_not_import_inference_runtimes():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from immich_memories import Config; Config(tier='nas'); "
            "assert not {'numpy', 'onnxruntime', 'mlx.core'} & sys.modules.keys()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
