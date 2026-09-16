"""Carry surrounding episode facts without expanding the pictures to analyse."""

from collections.abc import Collection

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_people import EditorialPeople
from immich_memories.analysis.editorial_text_gateway import semantic_text_model_identity
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.text_episode_answers import TEXT_EPISODE_SCHEMA_VERSION
from immich_memories.analysis.text_episode_reader import TEXT_EPISODE_PROMPT_VERSION
from immich_memories.config_loader import Config
from immich_memories.store.episode_readings import (
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
)


def context_evidence(rows, *, field="episode_context") -> str:
    """Conserve distinct surrounding context through story summaries."""
    return "\n".join(dict.fromkeys(row[field] for row in rows if row.get(field)))


def contextualize_episode_rows(tables, moment_assets, context):
    """Attach context to episodes while leaving picture/people associations untouched."""
    if not context:
        return tables
    fields, moments = tables["moments"]
    episode_at = fields.index("episode")
    by_episode: dict[str, dict[str, None]] = {}
    for moment in moments:
        texts = by_episode.setdefault(moment[episode_at], {})
        texts.update(
            dict.fromkeys(context[key] for key in moment_assets[moment[0]] if key in context)
        )
    fields, episodes = tables["episodes"]
    return tables | {
        "episodes": (
            fields,
            [[row[0], "\n".join((row[1], *by_episode.get(row[0], {})))] for row in episodes],
        )
    }


def capture_episode_context(
    prepared: PreparedEditorialSource,
    retained: Collection[str],
    *,
    people: EditorialPeople,
    config: Config | None = None,
) -> dict[str, str]:
    """Keep full eligible episode metadata beside its retained, selectable pictures."""
    selected = set(retained)
    groups = [
        group for group in prepared.episode_groups if selected.intersection(group.candidate_ids)
    ]
    # Complete groups already receive their reading downstream. Adding a cached
    # copy here would change the first prompt on its otherwise identical replay.
    wider_groups = [group for group in groups if not selected.issuperset(group.candidate_ids)]
    summaries = (
        _cached_summaries(prepared, wider_groups, people, config) if config is not None else {}
    )
    result = {}
    for group in groups:
        members = selected.intersection(group.candidate_ids)
        text = _metadata(group.candidates, people)
        if summary := summaries.get(group.group_id):
            text += f"; cached episode reading (inferred): {summary}"
        result.update(dict.fromkeys(members, text))
    return result


def _cached_summaries(prepared, groups, people, config):
    if not groups or config.editorial.resolve_reader(config.llm.model) == "rules":
        return {}
    path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    annotations = StoredAnnotationLineReader(
        store_path=path,
        candidates=prepared.candidates,
        description_model=config.editorial.description_model,
        head_versions=config.editorial.head_versions,
        pixel_producer_key=config.editorial.pixel_producer_key,
        people_context={
            person.id: fact
            for candidate in prepared.candidates
            for person in candidate.source.people or ()
            if (fact := people.fact_for_person_id(person.id)) is not None
        },
    )
    contract = annotations.contract
    producer = EpisodeReadingProducer(
        semantic_text_model_identity(config.llm, thinking=False),
        TEXT_EPISODE_PROMPT_VERSION,
        TEXT_EPISODE_SCHEMA_VERSION,
        contract.renderer_version,
        contract.producer_versions,
    )
    identities = []
    # Reading cached rows per episode avoids a library-sized annotation batch.
    # No requester or producer is constructed: a cache miss stays unknown.
    for group in groups:
        batch = annotations.lines_for(group.candidate_ids)
        if not batch.missing_asset_ids:
            identities.append(
                EpisodeReadingIdentity.from_annotations(
                    group_id=group.group_id,
                    producer_key=producer.key(),
                    annotation_lines=batch.as_mapping(),
                )
            )
    store = EpisodeReadingStore(path)
    try:
        return {key: value.what_happened for key, value in store.readings_for(identities).items()}
    finally:
        store.close()


def _participants(candidates, people):
    participants = {}
    for candidate in candidates:
        for person in candidate.source.people or ():
            fact = people.fact_for_person_id(person.id)
            name = fact.name if fact else person.name
            if name:
                participants[name] = (
                    f"{name} (owner relationship={fact.relationship}; {fact.relationship_source})"
                    if fact
                    else f"{name} (relationship unconfirmed)"
                )
    return sorted(participants.values())


def _metadata(candidates, people):
    places = {
        candidate.source.exif_info.city
        for candidate in candidates
        if candidate.source.exif_info and candidate.source.exif_info.city
    }
    names = _participants(candidates, people)
    shown = "; ".join(names[:16]) or "unknown"
    if len(names) > 16:
        shown += f"; {len(names) - 16} more named participants"
    return (
        "Surrounding episode metadata; participants are not necessarily in this picture: "
        f"{len(candidates)} captures, {candidates[0].taken_at.isoformat()} to "
        f"{candidates[-1].taken_at.isoformat()}; places: {', '.join(sorted(places)[:8]) or 'unknown'}; "
        f"episode participants: {shown}"
    )
