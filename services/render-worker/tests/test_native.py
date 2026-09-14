"""Native worker capability contract, independent of the HTTP lifecycle."""


def test_native_worker_refuses_cpu_only_host():
    from immich_memories_render_worker.native import NativeRenderer

    # WHY: replace the machine capability boundary; CI has no NVIDIA device.
    renderer = NativeRenderer(capabilities=lambda: {"titles": "CPU", "encoders": []})
    assert renderer.health()["ready"] is False


def test_unavailable_gpu_refuses_render_before_contacting_immich(tmp_path):
    from uuid import uuid4

    import pytest
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native import NativeRenderer

    # WHY: the external GPU capability probe is unavailable on this host.
    renderer = NativeRenderer(capabilities=lambda: {"titles": "CPU", "encoders": []})
    request = RenderRequest.model_validate(
        {
            "request_id": str(uuid4()),
            "memory_key": "example",
            "immich": {"url": "http://127.0.0.1:1", "api_key": "scoped-test-key"},
            "plan": {
                "clips": [{"asset_id": str(uuid4()), "start": 0, "end": 3, "render_mode": "motion"}]
            },
        }
    )
    with pytest.raises(RuntimeError, match="CUDA titles and the requested NVENC encoder"):
        renderer.render(request, tmp_path, lambda *_: None)
