"""Verdicts about what a picture IS, remembered across memories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.analysis.cull_answer import CullDecision
from immich_memories.analysis.editorial_contracts import DecisionProvenance
from immich_memories.analysis.selection_cull import cull_pass_version, run_cull_decisions
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    PreparedEditorialSource,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.store.episode_readings import EpisodeReadingProducer
from tests.conftest import make_asset

# Bandit reads a "..._version" keyword as a credential; these are pass identities.
CULL_V1 = "pass-1-cull-v3"  # noqa: S105 - editorial pass identity
CULL_V2 = "cull-v2"


def _prepared(*asset_ids: str) -> PreparedEditorialSource:
    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=noon + timedelta(minutes=index))
                for index, asset_id in enumerate(asset_ids)
            )
        ),
    )


def _reading(prompt_version: str) -> EpisodeReadingProducer:
    return EpisodeReadingProducer(
        model_id="a-text-reader",
        prompt_version=prompt_version,
        schema_version="episode-reading-text-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:v1",),
    )


def _provenance(
    prepared: PreparedEditorialSource, reading: EpisodeReadingProducer
) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name="pass-1-cull",  # noqa: S106 - editorial pass label, not a credential.
        pass_version=cull_pass_version(reading),
        schema_version=reading.schema_version,
        model_identity=reading.model_id,
        input_ids=prepared.candidate_ids,
        sheet_hashes=(),
        request_key="test",
        cache_hit=False,
    )


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


def test_a_bumped_reading_prompt_does_not_union_with_the_old_reading_s_rejects(
    tmp_path: Path,
) -> None:
    """#1059: one day carried 61 old rejects and 36 new ones at once, and lost the film.

    The bank is keyed on what a verdict answered. When the episode prompt
    changes, the question changes, so the previous prompt's rejects must not
    decide a cut made under the new one.
    """
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    old, new = _reading("episode-prompt-v1"), _reading("episode-prompt-v2-names-from-facts")

    first = _prepared("a-screen", "a-blur", "a-keeper")
    run_cull_decisions(
        first,
        (CullDecision("a-screen", "notes"),),
        provenance=_provenance(first, old),
        verdicts=store,
    )

    second = _prepared("a-screen", "a-blur", "a-keeper")
    result = run_cull_decisions(
        second,
        (CullDecision("a-blur", "failed"),),
        provenance=_provenance(second, new),
        verdicts=store,
    )

    assert [candidate.asset_id for candidate in result.survivors] == ["a-screen", "a-keeper"]


def test_the_same_reading_still_lends_its_verdict_to_the_next_memory(tmp_path: Path) -> None:
    """Retiring on a prompt bump must not retire on every run: a screen stays a screen."""
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

    store = EditorialVerdicts(tmp_path / "verdicts.db")
    reading = _reading("episode-prompt-v1")

    first = _prepared("a-screen", "a-keeper")
    run_cull_decisions(
        first,
        (CullDecision("a-screen", "notes"),),
        provenance=_provenance(first, reading),
        verdicts=store,
    )

    second = _prepared("a-screen", "a-keeper")
    result = run_cull_decisions(second, (), provenance=_provenance(second, reading), verdicts=store)

    assert [candidate.asset_id for candidate in result.survivors] == ["a-keeper"]
