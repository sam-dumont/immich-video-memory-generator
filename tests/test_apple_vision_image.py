"""The numpy→CGImage conversions must not retain frame buffers.

CGDataProviderCreateWithData does not copy: PyObjC keeps the Python buffer
alive until the provider's release callback fires — and with a None callback,
that is never. Every Vision call then leaks its full RGBA frame (~8 MB at
1080p), invisible to gc because bytes objects are untracked. A full unit-suite
run accumulates 15+ GB and the 7 GB macOS CI runner kills pytest (exit 137).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import platform

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin", reason="CoreGraphics only exists on macOS"
)

_MACH_TASK_BASIC_INFO = 20


class _TaskBasicInfo(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("resident_size_max", ctypes.c_uint64),
        ("user_time", ctypes.c_uint64),
        ("system_time", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("suspend_count", ctypes.c_int32),
    ]


def _current_rss_mb() -> float:
    # WHY: ru_maxrss is a high-watermark — useless mid-suite where earlier tests
    # already pushed the peak. mach task_info reports *current* RSS.
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    info = _TaskBasicInfo()
    count = ctypes.c_uint32(ctypes.sizeof(_TaskBasicInfo) // 4)
    libc.task_info(
        libc.mach_task_self(), _MACH_TASK_BASIC_INFO, ctypes.byref(info), ctypes.byref(count)
    )
    return info.resident_size / (1024 * 1024)


def _assert_no_buffer_retention(convert) -> None:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    convert(frame)  # warmup: lazy framework imports, colorspace caches
    base = _current_rss_mb()
    for _ in range(30):
        convert(frame)
    growth = _current_rss_mb() - base
    # 30 retained RGBA buffers would be ~240 MB; a healthy run stays near zero.
    assert growth < 60, f"{growth:.0f} MB retained after 30 conversions of one 1080p frame"


def test_cg_image_conversion_does_not_accumulate_frame_buffers() -> None:
    pytest.importorskip("Quartz")
    from immich_memories.analysis.apple_vision_image import create_cg_image_from_numpy

    _assert_no_buffer_retention(create_cg_image_from_numpy)
