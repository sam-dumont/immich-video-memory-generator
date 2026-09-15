"""Native worker capability contract, independent of the HTTP lifecycle."""


def test_a_cpu_only_host_still_renders_and_says_the_card_is_missing():
    """Measured 2026-09-14: NVENC 219 s against libx264 258 s on the same node.

    The card is worth about 15% of the render, so refusing without it turns a
    slower film into no film.
    """
    from immich_memories_render_worker.native import NativeRenderer

    # WHY: replace the machine capability boundary; CI has no NVIDIA device.
    health = NativeRenderer(capabilities=lambda: {"titles": "CPU", "encoders": []}).health()
    assert health["ready"] is True
    assert health["accelerated"] is False


def test_a_cuda_host_reports_itself_accelerated():
    from immich_memories_render_worker.native import NativeRenderer

    # WHY: replace the machine capability boundary; CI has no NVIDIA device.
    health = NativeRenderer(
        capabilities=lambda: {"titles": "CUDA", "encoders": ["h264_nvenc"]}
    ).health()
    assert health["accelerated"] is True


def test_missing_capabilities_are_named_one_by_one():
    from immich_memories_render_worker.native import _capability_degradations

    assert (
        _capability_degradations({"titles": "CUDA", "encoders": ["h264_nvenc"]}, "h264_nvenc") == []
    )
    reported = _capability_degradations({"titles": "CPU", "encoders": []}, "h264_nvenc")
    assert reported == [
        "title screens rendered on CPU",
        "h264_nvenc is not available on this host",
    ]
