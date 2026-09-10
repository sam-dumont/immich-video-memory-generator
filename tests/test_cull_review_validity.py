"""What makes a Cull review sheet unjudgeable, and what merely makes it noisy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from immich_memories.analysis.selection_cull import CULL_PASS_VERSION, run_cull
from immich_memories.cache.editorial_verdicts import EditorialVerdicts
from tests.test_selection_cull import _pass_zero_for


def test_a_remembered_verdict_does_not_invalidate_the_sheet(tmp_path: Path) -> None:
    """A cache hit is the pass working, and must not read as the pass failing.

    `!!` means one thing in this design: the sheet is invalid for an owner
    verdict. A verdict recalled from a previous look is the durable per-asset
    bank doing its job, so it belongs on the sheet as provenance and not as a
    failure -- otherwise the only accumulating cache in the engine marks every
    successful reuse as a defect, and no warm run is ever judgeable.
    """
    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((("a-receipt", "notes"),), pass_version=CULL_PASS_VERSION)

    prepared, pass_zero = _pass_zero_for(
        tmp_path,
        assets=("a-receipt", "a-moment"),
        when=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )
    result = run_cull(
        prepared,
        pass_zero,
        review_output_dir=tmp_path / "review",
        verdicts=store,
    )

    assert tuple(decision.asset_id for decision in result.rejected) == ("a-receipt",)
    # still said out loud -- a replayed decision is not a silent one
    assert any("remembered" in warning for warning in result.warnings)
    # but not as a failure
    assert not any(warning.startswith("!!") for warning in result.warnings)


def test_a_real_failure_still_invalidates_the_sheet(tmp_path: Path) -> None:
    """The channel only stays useful while genuine failures keep their `!!`."""
    prepared, pass_zero = _pass_zero_for(
        tmp_path,
        assets=("a-moment",),
        when=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )
    prepared.trace.warnings.append("!! Pass 1 unreadable episode scan: made-up-page")

    result = run_cull(prepared, pass_zero, review_output_dir=tmp_path / "review")

    assert any(warning.startswith("!!") for warning in result.warnings)
