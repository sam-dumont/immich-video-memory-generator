"""ACE-Step must decline a render it cannot fit, rather than let jetsam decide.

Loading the XL/4B profile allocates tens of gigabytes before a single sample exists.
When it does not fit, macOS answers with SIGKILL — and on a machine that is also
serving a local LLM that took the whole session down (#573).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from immich_memories.audio.generators import memory_budget
from immich_memories.audio.generators.memory_budget import (
    MemoryShortfall,
    available_memory_bytes,
    memory_shortfall,
    parse_meminfo,
    parse_vm_stat,
    required_memory_bytes,
)

_VM_STAT_SAMPLE = (
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    "Pages free:                        100.\n"
    "Pages active:                     9000.\n"
    "Pages inactive:                    200.\n"
    "Pages speculative:                  50.\n"
    "Pages throttled:                     0.\n"
    "Pages wired down:                 5000.\n"
    "Pages purgeable:                    25.\n"
    "Pages purged:                    12345.\n"
)

_GIB = 1024**3
_XL = "acestep-v15-xl-turbo"
_SMALL = "acestep-v15-turbo"
_LM_4B = "acestep-5Hz-lm-4B"

# The peak a full XL/4B render reaches, per docs/create/pipeline/audio-and-music.md.
_DOCUMENTED_XL_PEAK = 53 * _GIB


class TestRequiredMemory:
    def test_the_floor_stays_well_under_the_documented_render_peak(self) -> None:
        """Testing against the peak would refuse renders that complete today."""
        assert required_memory_bytes(_XL, _LM_4B) < _DOCUMENTED_XL_PEAK / 1.5

    def test_the_xl_profile_needs_its_published_checkpoint_sizes_resident(self) -> None:
        """XL DiT ~19 GB + 4B planner ~8 GB + shared ~2 GB, from the model-cache table."""
        assert required_memory_bytes(_XL, _LM_4B) == 29 * _GIB

    def test_a_2b_dit_needs_less_than_the_4b_one(self) -> None:
        assert required_memory_bytes(_SMALL, None) < required_memory_bytes(_XL, None)

    def test_the_default_small_profile_fits_a_16gb_mac(self) -> None:
        """turbo without a planner is what the docs send low-memory machines to."""
        assert required_memory_bytes(_SMALL, None) <= 8 * _GIB

    def test_disabling_the_planner_lowers_the_requirement(self) -> None:
        assert required_memory_bytes(_XL, None) < required_memory_bytes(_XL, _LM_4B)

    def test_an_unrecognized_lm_is_costed_as_the_largest_one(self) -> None:
        """Guessing low would defeat the guard; an unknown planner is assumed big."""
        assert required_memory_bytes(_XL, "acestep-5Hz-lm-experimental") == required_memory_bytes(
            _XL, _LM_4B
        )


class TestMemoryShortfall:
    def test_a_host_that_completes_this_render_today_is_left_alone(self) -> None:
        """40 GB free is under the 53 GB peak and still fine — do not refuse it."""
        # WHY: available_memory_bytes shells out to vm_stat / reads /proc
        with patch(
            "immich_memories.audio.generators.memory_budget.available_memory_bytes",
            return_value=40 * _GIB,
        ):
            assert memory_shortfall(_XL, _LM_4B) is None

    def test_the_573_pressure_reports_what_is_missing(self) -> None:
        """The 03:00 machine had a co-resident LLM server holding ~91 GB."""
        # WHY: available_memory_bytes shells out to vm_stat / reads /proc
        with patch(
            "immich_memories.audio.generators.memory_budget.available_memory_bytes",
            return_value=4 * _GIB,
        ):
            shortfall = memory_shortfall(_XL, _LM_4B)

        assert shortfall == MemoryShortfall(
            required_bytes=29 * _GIB, available_bytes=4 * _GIB, profile="acestep-v15-xl-turbo+4B"
        )
        assert "29" in str(shortfall)
        assert "4 GB is available" in str(shortfall)

    def test_an_unreadable_host_never_blocks_a_render(self) -> None:
        """No memory reading is not evidence of pressure — stay out of the way."""
        # WHY: available_memory_bytes shells out to vm_stat / reads /proc
        with patch(
            "immich_memories.audio.generators.memory_budget.available_memory_bytes",
            return_value=None,
        ):
            assert memory_shortfall(_XL, _LM_4B) is None


class TestAvailableMemory:
    def test_this_host_reports_a_plausible_figure(self) -> None:
        """Real vm_stat / procfs, because parsing them correctly is the whole job."""
        available = available_memory_bytes()

        if available is None:
            pytest.skip("host memory is not readable on this platform")
        assert 0 < available < 8 * 1024 * _GIB


class TestVmStatParsing:
    """Pure text in, bytes out — so the macOS format is checked on every runner."""

    def test_counts_free_and_reclaimable_pages_at_the_reported_page_size(self) -> None:
        assert parse_vm_stat(_VM_STAT_SAMPLE) == (100 + 200 + 50 + 25) * 16384

    def test_active_and_wired_pages_are_not_available(self) -> None:
        """Those are in use; counting them would let a doomed render start."""
        assert parse_vm_stat(_VM_STAT_SAMPLE) < 9000 * 16384

    def test_purged_is_not_mistaken_for_purgeable(self) -> None:
        """`Pages purged` is a lifetime counter, not memory anyone can have back."""
        assert parse_vm_stat(_VM_STAT_SAMPLE) < 12345 * 16384

    def test_a_missing_page_size_header_falls_back_to_4k(self) -> None:
        assert parse_vm_stat("Mach Virtual Memory Statistics:\nPages free: 10.\n") == 10 * 4096

    def test_output_with_no_usable_counters_is_unknown(self) -> None:
        assert parse_vm_stat("") is None


class TestMeminfoParsing:
    """Pure text in, bytes out — so the Linux format is checked on every runner."""

    def test_reads_mem_available_as_kilobytes(self) -> None:
        contents = "MemTotal:       32768 kB\nMemAvailable:    2048 kB\nSwapFree: 0 kB\n"

        assert parse_meminfo(contents) == 2048 * 1024

    def test_absent_mem_available_is_unknown(self) -> None:
        """Kernels before 3.14 have no MemAvailable; guessing from MemFree overstates it."""
        assert parse_meminfo("MemTotal:       32768 kB\nMemFree:  512 kB\n") is None


class TestAvailableMemoryPerPlatform:
    """Force each OS branch, so neither runner leaves the other's path unexecuted."""

    # WHY: platform.system() returns the real OS — force "Darwin" for the macOS branch
    @patch("immich_memories.audio.generators.memory_budget.platform")
    # WHY: subprocess.run would shell out to vm_stat, which only exists on macOS
    @patch("immich_memories.audio.generators.memory_budget.subprocess.run")
    def test_darwin_reads_vm_stat(self, run: object, system: object) -> None:
        system.system.return_value = "Darwin"  # type: ignore[attr-defined]
        run.return_value = SimpleNamespace(returncode=0, stdout=_VM_STAT_SAMPLE)  # type: ignore[attr-defined]

        assert available_memory_bytes() == (100 + 200 + 50 + 25) * 16384

    # WHY: platform.system() returns the real OS — force "Darwin" for the macOS branch
    @patch("immich_memories.audio.generators.memory_budget.platform")
    # WHY: subprocess.run would shell out to vm_stat, which only exists on macOS
    @patch("immich_memories.audio.generators.memory_budget.subprocess.run")
    def test_a_failed_vm_stat_is_unknown(self, run: object, system: object) -> None:
        system.system.return_value = "Darwin"  # type: ignore[attr-defined]
        run.return_value = SimpleNamespace(returncode=1, stdout="")  # type: ignore[attr-defined]

        assert available_memory_bytes() is None

    # WHY: platform.system() returns the real OS — force "Linux" for the procfs branch
    @patch("immich_memories.audio.generators.memory_budget.platform")
    def test_linux_reads_proc_meminfo(
        self, system: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system.system.return_value = "Linux"  # type: ignore[attr-defined]
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal: 32768 kB\nMemAvailable:  4096 kB\n")
        monkeypatch.setattr(memory_budget, "_MEMINFO_PATH", meminfo)

        assert available_memory_bytes() == 4096 * 1024

    # WHY: platform.system() returns the real OS — force an OS with neither probe
    @patch("immich_memories.audio.generators.memory_budget.platform")
    def test_an_unsupported_platform_is_unknown(self, system: object) -> None:
        system.system.return_value = "Windows"  # type: ignore[attr-defined]

        assert available_memory_bytes() is None

    # WHY: platform.system() returns the real OS — force the probe to fail outright
    @patch("immich_memories.audio.generators.memory_budget.platform")
    def test_an_unreadable_probe_is_unknown_rather_than_fatal(self, system: object) -> None:
        """A memory reading that errors must not take the whole render down with it."""
        system.system.side_effect = OSError("boom")  # type: ignore[attr-defined]

        assert available_memory_bytes() is None
