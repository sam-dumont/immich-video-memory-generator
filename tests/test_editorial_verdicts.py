"""Verdicts about what a picture IS, remembered across memories.

The two Cull-through cases arrive with the slice that ports `selection_cull`.
"""

from __future__ import annotations

from pathlib import Path

# Bandit reads a "..._version" keyword as a credential; these are pass identities.
CULL_V1 = "pass-1-cull-v3"  # noqa: S105 - editorial pass identity
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


def test_nothing_to_remember_or_recall_touches_no_rows(tmp_path: Path) -> None:
    """An empty pass is free; recalling nothing never widens into everything."""
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    store.remember((), pass_version=CULL_V1)
    store.remember((("a-receipt", "notes"),), pass_version=CULL_V1)

    assert store.recall((), pass_version=CULL_V1) == {}
