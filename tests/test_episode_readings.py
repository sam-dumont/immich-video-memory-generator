"""Reusable episode meaning is stored at full-membership identity."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path


def test_episode_reading_round_trips_only_for_the_version_that_produced_it(
    tmp_path: Path,
) -> None:
    from immich_memories.store.episode_readings import (
        BankedEpisodeReading,
        EpisodeCullDecision,
        EpisodeReadingIdentity,
        EpisodeReadingStore,
        EpisodeRepresentative,
    )

    identity = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-full-library-membership",
        producer_key="episode-text:model-a:prompt-v1:schema-v1",
        annotation_lines={
            "party-wide": "party-wide | birthday party",
            "friend-closeup": "friend-closeup | friend beside cake",
            "receipt": "receipt | photographed receipt",
        },
    )
    reading = BankedEpisodeReading(
        identity=identity,
        full_asset_ids=("party-wide", "friend-closeup", "receipt"),
        what_happened="A friend joins a birthday party.",
        representatives=(
            EpisodeRepresentative(
                asset_id="friend-closeup",
                reason="Shows the friend beside the cake.",
            ),
        ),
        cull_decisions=(EpisodeCullDecision(asset_id="receipt", bucket="notes"),),
    )
    path = tmp_path / "annotations.sqlite"
    store = EpisodeReadingStore(path)
    store.remember((reading,))
    store.close()

    reopened = EpisodeReadingStore(path)

    assert (
        reopened.readings_for((identity,)),
        reopened.readings_for(
            (
                replace(
                    identity,
                    producer_key="episode-text:model-a:prompt-v2:schema-v1",
                ),
            )
        ),
    ) == ({identity.group_id: reading}, {})


def test_episode_producer_key_covers_every_semantic_input() -> None:
    from immich_memories.store.episode_readings import EpisodeReadingProducer

    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=(
            "description:smolvlm2-envelope-v2",
            "heads:public-v1",
        ),
    )

    keys = {
        producer.key(),
        replace(producer, model_id="qwen3-vl-30b-v2").key(),
        replace(producer, prompt_version="episode-prompt-v2").key(),
        replace(producer, schema_version="episode-schema-v2").key(),
        replace(producer, annotation_renderer_version="annotation-line-v2").key(),
        replace(producer, annotation_versions=("description:smolvlm2-envelope-v3",)).key(),
    }

    assert len(keys) == 6


def test_episode_identity_changes_when_rendered_annotation_evidence_changes() -> None:
    from immich_memories.store.episode_readings import EpisodeReadingIdentity

    original = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-full-library-membership",
        producer_key="producer-v1",
        annotation_lines={"asset-a": "asset-a | at home", "asset-b": "asset-b | outside"},
    )
    reordered = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-full-library-membership",
        producer_key="producer-v1",
        annotation_lines={"asset-b": "asset-b | outside", "asset-a": "asset-a | at home"},
    )
    changed = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-full-library-membership",
        producer_key="producer-v1",
        annotation_lines={"asset-a": "asset-a | at home", "asset-b": "asset-b | at a race"},
    )

    assert original == reordered
    assert original != changed


def test_changed_episode_evidence_is_a_cache_miss(tmp_path: Path) -> None:
    from immich_memories.store.episode_readings import (
        BankedEpisodeReading,
        EpisodeReadingIdentity,
        EpisodeReadingStore,
        EpisodeRepresentative,
    )

    old_identity = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-full-library-membership",
        producer_key="producer-v1",
        annotation_lines={"asset-a": "asset-a | an ordinary walk"},
    )
    changed_identity = EpisodeReadingIdentity.from_annotations(
        group_id=old_identity.group_id,
        producer_key=old_identity.producer_key,
        annotation_lines={"asset-a": "asset-a | a charity walk"},
    )
    reading = BankedEpisodeReading(
        identity=old_identity,
        full_asset_ids=("asset-a",),
        what_happened="An ordinary walk.",
        representatives=(EpisodeRepresentative("asset-a", "Shows the walk."),),
        cull_decisions=(),
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    store.remember((reading,))

    assert store.readings_for((old_identity,)) == {old_identity.group_id: reading}
    assert store.readings_for((changed_identity,)) == {}


def test_typed_store_coexists_with_the_legacy_probe_episode_table(tmp_path: Path) -> None:
    from immich_memories.store.episode_readings import (
        BankedEpisodeReading,
        EpisodeReadingIdentity,
        EpisodeReadingStore,
        EpisodeRepresentative,
    )

    path = tmp_path / "annotations.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE episode_readings ("
            "episode_key TEXT, producer_key TEXT, asset_ids TEXT, what_happened TEXT, "
            "PRIMARY KEY (episode_key, producer_key))"
        )
        connection.execute(
            "INSERT INTO episode_readings VALUES (?, ?, ?, ?)",
            ("legacy-key", "legacy-producer", '["legacy-asset"]', "Legacy meaning."),
        )

    identity = EpisodeReadingIdentity.from_annotations(
        group_id="episode-v1-current-membership",
        producer_key="producer-v2",
        annotation_lines={"current-asset": "current annotation line"},
    )
    reading = BankedEpisodeReading(
        identity=identity,
        full_asset_ids=("current-asset",),
        what_happened="Current meaning.",
        representatives=(EpisodeRepresentative("current-asset", "Shows the event."),),
        cull_decisions=(),
    )
    store = EpisodeReadingStore(path)
    store.remember((reading,))

    assert store.readings_for((identity,)) == {identity.group_id: reading}
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT what_happened FROM episode_readings WHERE episode_key = 'legacy-key'"
        ).fetchone() == ("Legacy meaning.",)
