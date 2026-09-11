"""Product-specific editorial stance is a stable production contract."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from immich_memories.analysis import editorial_product_brief as product_brief
from immich_memories.timeperiod import DateRange


def _range(year: int, month: int, day: int) -> DateRange:
    start = datetime(year, month, day, tzinfo=UTC)
    return DateRange(start, start.replace(hour=23, minute=59, second=59))


def test_monthly_stance_preserves_the_base_and_capacity_guard() -> None:
    result = product_brief.build_editorial_brief(
        "monthly_highlights",
        (_range(2022, 6, 1),),
        base="Keep this month truthful.",
    )

    assert result.startswith("Keep this month truthful.\n\nTHIS PRODUCT'S TEXTURE:")
    assert "A quiet month is allowed to look quiet" in result
    assert result.endswith(
        "The texture guides WHICH moments and frames carry the memory -- never HOW MANY; "
        "every stated capacity and slot budget stands unchanged."
    )


def test_ritual_stance_names_exact_disjoint_years_before_the_flavor() -> None:
    ranges = (
        _range(2019, 12, 25),
        _range(2021, 12, 25),
        _range(2022, 12, 25),
    )

    result = product_brief.build_editorial_brief(
        "holiday",
        ranges,
        base="Remember the recurring day.",
    )

    assert (
        "WHAT THIS MEMORY IS: the same day each year -- December 25 -- across "
        "3 years (2019-2022). This scope was chosen specifically for that yearly return."
    ) in result
    assert result.index("WHAT THIS MEMORY IS") < result.index("THIS PRODUCT'S TEXTURE")


def test_unflavored_custom_memory_keeps_only_the_explicit_ask() -> None:
    assert (
        product_brief.build_editorial_brief(
            "custom",
            (_range(2022, 1, 1),),
            base="Follow the red bicycle across the year.",
        )
        == "Follow the red bicycle across the year."
    )


def test_blank_base_or_product_is_rejected() -> None:
    for product, base in (("", "brief"), ("year_in_review", "")):
        try:
            product_brief.build_editorial_brief(product, (), base=base)
        except ValueError as exc:
            assert "nonblank" in str(exc)
        else:
            raise AssertionError("blank editorial contract should fail")


@pytest.mark.parametrize(
    "product,expected",
    [
        ("monthly_highlights", "b66b1a35dc7e8b8541aab337f707286b83b00e8cefeb36a0692a10e282abc2f5"),
        ("trip", "cebc9fa253be7015b5541d309256ca7353c244d07d7caf544169fb45de373ed1"),
        ("special_day", "0c00913d66b1f3354238f94f8f1e0233f6a6a6c26a32f2ff37b1295450f3adb7"),
        ("album", "e35b6c42a755119bc61f81d49f355372aebcab2733f3c11f1bd6a5f9bd976a3b"),
        ("holiday", "497d8fff1a8510609d53b2c74156bbd80aed39d81bd7d8b530c19af197ed6bb2"),
        ("person_spotlight", "f088e1e274b70386fee5a36fa3fb89bb31c31a06d47e590b0099ef3d30e3d6b4"),
        ("multi_person", "f088e1e274b70386fee5a36fa3fb89bb31c31a06d47e590b0099ef3d30e3d6b4"),
        ("year_in_review", "66cbc58df3dd75dffe29dd8c05a162ecf91adb0a5766e26d47a4f47d58e00a1d"),
        ("season", "66cbc58df3dd75dffe29dd8c05a162ecf91adb0a5766e26d47a4f47d58e00a1d"),
        ("on_this_day", "66cbc58df3dd75dffe29dd8c05a162ecf91adb0a5766e26d47a4f47d58e00a1d"),
        ("custom", "66cbc58df3dd75dffe29dd8c05a162ecf91adb0a5766e26d47a4f47d58e00a1d"),
    ],
)
def test_default_base_matches_evaluated_matrix_contract(product, expected):
    # Digests extracted independently from all 46 sealed execution contracts.
    # A shortened but plausible default caused the native selection regression.
    base = product_brief.build_editorial_brief(product, ()).split("\n\n", 1)[0]
    assert hashlib.sha256(base.encode()).hexdigest() == expected
