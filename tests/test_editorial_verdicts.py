"""Verdicts about what a picture IS, remembered across memories."""

from __future__ import annotations

from pathlib import Path

# Bandit reads a "..._version" keyword as a credential; these are pass identities.
from immich_memories.analysis.selection_cull import CULL_PASS_VERSION

CULL_V1 = CULL_PASS_VERSION
CULL_V2 = "cull-v2"


def test_a_verdict_about_the_picture_survives_into_another_memory(tmp_path: Path) -> None:
    """A photographed receipt is a receipt in every memory it could appear in."""
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember(
        (("a-receipt", "notes"), ("a-smear", "failed")),
        pass_version=CULL_V1,
    )

    recalled = EditorialVerdicts(tmp_path / "verdicts.db").recall(
        ("a-receipt", "a-smear", "never-seen"), pass_version=CULL_V1
    )

    assert recalled == {"a-receipt": "notes", "a-smear": "failed"}


def test_changing_what_a_bucket_means_forgets_the_old_verdicts(tmp_path: Path) -> None:
    """The store answers for a definition, not for all time.

    Re-rendering a picture must not clear a verdict about what it is, but
    changing what `notes` MEANS has to, or a year of judgement silently answers
    a question nobody asked any more.
    """
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((("a-receipt", "notes"),), pass_version=CULL_V1)

    assert store.recall(("a-receipt",), pass_version=CULL_V2) == {}
    assert store.recall(("a-receipt",), pass_version=CULL_V1) == {"a-receipt": "notes"}


def test_a_later_verdict_replaces_an_earlier_one(tmp_path: Path) -> None:
    """One asset, one standing verdict; the most recent look wins."""
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((("argued-over", "notes"),), pass_version=CULL_V1)
    store.remember((("argued-over", "failed"),), pass_version=CULL_V1)

    assert store.recall(("argued-over",), pass_version=CULL_V1) == {"argued-over": "failed"}


def test_a_remembered_verdict_culls_without_asking_again(tmp_path: Path) -> None:
    """The picture was judged once; every later memory inherits that judgement."""
    from datetime import UTC, datetime

    from immich_memories.analysis.selection_cull import run_cull
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts
    from tests.test_selection_cull import _pass_zero_for

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((("a-receipt", "notes"),), pass_version=CULL_V1)

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
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("a-moment",)
    assert any("remembered" in warning for warning in result.warnings)


def test_a_star_outranks_a_remembered_verdict(tmp_path: Path) -> None:
    """The star settles it here as it settles every other hard gate."""
    from datetime import UTC, datetime

    from immich_memories.analysis.selection_cull import run_cull
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts
    from tests.test_selection_cull import _pass_zero_for

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((("a-receipt", "notes"),), pass_version=CULL_V1)

    prepared, pass_zero = _pass_zero_for(
        tmp_path,
        assets=("a-receipt",),
        when=datetime(2026, 8, 25, 12, tzinfo=UTC),
        favourites=("a-receipt",),
    )
    result = run_cull(
        prepared,
        pass_zero,
        review_output_dir=tmp_path / "review",
        verdicts=store,
    )

    assert result.rejected == ()
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("a-receipt",)
