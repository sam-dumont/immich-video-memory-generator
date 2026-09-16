"""Sampling can reuse a whole episode's banked meaning without asking a model."""

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_episode_context import capture_episode_context
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_text_gateway import semantic_text_model_identity
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.text_episode_answers import TEXT_EPISODE_SCHEMA_VERSION
from immich_memories.analysis.text_episode_reader import TEXT_EPISODE_PROMPT_VERSION
from immich_memories.config_loader import Config
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.test_annotation_lines import store_with
from tests.test_editorial_source_route import photo


def test_complete_cached_context_is_reused_and_changed_facts_invalidate_it(tmp_path):
    path = store_with(tmp_path)
    config = Config(editorial={"annotation_database": str(path)}, llm={"model": "test-reader"})
    sources = [photo("party"), photo("portrait")]
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )
    group = prepared.episode_groups[0]
    annotations = StoredAnnotationLineReader(
        store_path=path,
        candidates=prepared.candidates,
        description_model=config.editorial.description_model,
        head_versions=config.editorial.head_versions,
        pixel_producer_key=config.editorial.pixel_producer_key,
    )
    contract = annotations.contract
    producer = EpisodeReadingProducer(
        semantic_text_model_identity(config.llm, thinking=False),
        TEXT_EPISODE_PROMPT_VERSION,
        TEXT_EPISODE_SCHEMA_VERSION,
        contract.renderer_version,
        contract.producer_versions,
    )
    reading = BankedEpisodeReading(
        EpisodeReadingIdentity.from_annotations(
            group_id=group.group_id,
            producer_key=producer.key(),
            annotation_lines=annotations.lines_for(group.candidate_ids).as_mapping(),
        ),
        group.candidate_ids,
        "A birthday party with friends and a cake.",
        (EpisodeRepresentative("party", "Shows the occasion around the portrait."),),
        (),
    )
    store = EpisodeReadingStore(path)
    store.remember((reading,))
    try:
        context = capture_episode_context(
            prepared,
            ("portrait",),
            people=adapt_editorial_people({}),
            config=config,
        )
        assert set(context) == {"portrait"}
        assert reading.what_happened in context["portrait"]
        assert "2 captures" in context["portrait"]
        sources[0].is_favorite = True
        updated = prepare_editorial_source(
            EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
            EditorialDependencies(source_fetcher=lambda _: sources),
        )
        cold = capture_episode_context(
            updated,
            ("portrait",),
            people=adapt_editorial_people({}),
            config=config,
        )
        assert reading.what_happened not in cold["portrait"]
        assert "2 captures" in cold["portrait"]
    finally:
        store.close()
