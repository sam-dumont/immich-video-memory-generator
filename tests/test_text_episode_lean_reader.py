"""A film's on-demand reading asks only what the film reads back.

The polish reads an episode for two things: what happened (the account) and what the episode
records (notable moments). The full reading also asks for up to three representatives with
reasons and every Cull reject, which the polish never reads; on the measured cold year those two
were 62 % of the reader's output, and output is 89 % of a reading's time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_prompt import (
    TEXT_EPISODE_LEAN_PROMPT_VERSION,
    TEXT_EPISODE_PROMPT_VERSION,
)
from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.conftest import make_asset
from tests.test_text_episode_reader import _AnnotationLines

NOON = datetime(2026, 8, 25, 12, tzinfo=UTC)
LINES = {
    "cake": "birthday cake | with family | STARRED",
    "candles": "blowing out the candles | with family",
}


def producer(prompt_version: str) -> EpisodeReadingProducer:
    return EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version=prompt_version,
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )


def projections():
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("cake", file_created_at=NOON),
                make_asset("candles", file_created_at=NOON + timedelta(minutes=5)),
            )
        ),
    )
    return project_episode_groups(prepared, ("candles",))


def lean_reader(store, requester) -> CachedTextEpisodeReader:
    return CachedTextEpisodeReader(
        store=store,
        producer=producer(TEXT_EPISODE_LEAN_PROMPT_VERSION),
        annotations=_AnnotationLines(dict(LINES)),
        requester=requester,
        lean=True,
        served_by=producer(TEXT_EPISODE_PROMPT_VERSION),
    )


def test_a_lean_reading_asks_for_no_cull_and_one_representative_and_banks_the_answer(
    tmp_path: Path,
) -> None:
    asked: list[str] = []

    def requester(prompt: str) -> str:
        asked.append(prompt)
        return """{"schema_version": "episode-reading-text-v1", "episodes": [{
            "episode": 1,
            "what_happened": "A family birthday with a cake and candles.",
            "representatives": [{"asset": 2, "reason": "the candles"}],
            "notable_moments": [{"asset": 2, "reason": "the first birthday candles"}]}]}"""

    result = lean_reader(EpisodeReadingStore(tmp_path / "a.sqlite"), requester).read(projections())

    assert len(asked) == 1
    assert "cull" not in asked[0].casefold()
    assert "one to three" not in asked[0]
    reading = result.episodes[0].reading
    assert reading is not None
    assert reading.what_happened == "A family birthday with a cake and candles."
    assert reading.notable_moments == (
        EpisodeRepresentative("candles", "the first birthday candles"),
    )
    assert reading.cull_decisions == ()
    # the Cull list the lean question never asked for is not counted as a bad answer
    assert result.diagnostics.discarded_invalid_cull_rows == 0


def test_a_lean_reader_answers_from_a_full_reading_already_banked(tmp_path: Path) -> None:
    """A library `prepare --overviews` read, or an earlier full-route film, costs nothing."""
    group = projections()[0].group
    full = BankedEpisodeReading(
        identity=EpisodeReadingIdentity.from_annotations(
            group_id=group.group_id,
            producer_key=producer(TEXT_EPISODE_PROMPT_VERSION).key(),
            annotation_lines=LINES,
        ),
        full_asset_ids=group.candidate_ids,
        what_happened="A family birthday.",
        representatives=(EpisodeRepresentative("cake", "the cake"),),
        cull_decisions=(),
        notable_moments=(EpisodeRepresentative("candles", "the first candles"),),
    )
    store = EpisodeReadingStore(tmp_path / "a.sqlite")
    store.remember((full,))

    def forbidden(_prompt: str) -> str:
        raise AssertionError("a full reading already answers the lean question")

    result = lean_reader(store, forbidden).read(projections())

    assert result.actual_calls == 0
    assert result.episodes[0].reading == full
    assert result.episodes[0].cache_hit is True


def test_only_the_on_demand_reader_is_lean() -> None:
    from immich_memories.analysis.episode_demand import demand_reader_factory

    def rules(_prepared):
        return "rules"

    def text(_prepared):
        return "text"

    def lean(_prepared):
        return "lean"

    reader, demand = demand_reader_factory(rules, text, mode="model", on_demand=True, lean=lean)
    assert demand is not None and demand._on_demand is lean
    reader, demand = demand_reader_factory(rules, text, mode="model", on_demand=False, lean=lean)
    assert reader is text and demand is None
