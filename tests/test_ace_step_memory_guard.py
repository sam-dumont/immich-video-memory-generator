"""ACE-Step must decline a render it cannot fit, rather than let jetsam decide.

Loading the XL/4B profile allocates tens of gigabytes before a single sample exists.
When it does not fit, macOS answers with SIGKILL — and on a machine that is also
serving a local LLM that took the whole session down (#573).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from immich_memories.audio.generators.memory_budget import (
    MemoryShortfall,
    available_memory_bytes,
    memory_shortfall,
    required_memory_bytes,
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
