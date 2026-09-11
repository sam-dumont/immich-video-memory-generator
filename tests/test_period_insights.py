"""Period meaning is banked against its exact ordered episode grounding."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest


def test_period_insight_round_trips_only_for_its_exact_grounding(tmp_path: Path) -> None:
    from immich_memories.store.period_insights import (
        BankedInsightEvidence,
        BankedPeriodInsight,
        PeriodEpisodeGrounding,
        PeriodInsightIdentity,
        PeriodInsightProducer,
        PeriodInsightStore,
    )

    producer = PeriodInsightProducer(
        model_id="qwen3-vl-30b",
        prompt_version="period-text-v1",
        schema_version="period-insight-text-v1",
    )
    episodes = (
        PeriodEpisodeGrounding(
            episode_id="episode-morning",
            evidence_key="reading-evidence-morning",
            rendered_line="Morning — 12 assets — packing for the race.",
            representative_asset_ids=("bag", "doorway"),
        ),
        PeriodEpisodeGrounding(
            episode_id="episode-race",
            evidence_key="reading-evidence-race",
            rendered_line="Afternoon — 34 assets — finishing a first race.",
            representative_asset_ids=("finish-line",),
        ),
    )
    identity = PeriodInsightIdentity.from_grounding(
        producer_key=producer.key(),
        episodes=episodes,
    )
    insight = BankedPeriodInsight(
        identity=identity,
        episode_grounding=episodes,
        thesis="Preparation gives way to the relief of finishing.",
        evidence=(
            BankedInsightEvidence(
                observation="The packed bag resolves at the finish line.",
                episode_ids=("episode-morning", "episode-race"),
                asset_ids=("bag", "finish-line"),
            ),
        ),
        tensions=("Nerves before the start versus relief afterward.",),
        recurring_threads=("Showing up for difficult things.",),
    )
    store = PeriodInsightStore(tmp_path / "annotations.sqlite")
    store.remember(insight)
    store.close()

    reopened = PeriodInsightStore(tmp_path / "annotations.sqlite")
    changed_identity = PeriodInsightIdentity.from_grounding(
        producer_key=replace(producer, prompt_version="period-text-v2").key(),
        episodes=episodes,
    )

    assert reopened.insight_for(identity) == insight
    assert reopened.insight_for(changed_identity) is None


def test_period_evidence_cannot_borrow_an_asset_from_an_uncited_episode() -> None:
    from immich_memories.store.period_insights import (
        BankedInsightEvidence,
        BankedPeriodInsight,
        PeriodEpisodeGrounding,
        PeriodInsightIdentity,
    )

    episodes = (
        PeriodEpisodeGrounding("morning", "evidence-a", "Morning packing.", ("bag",)),
        PeriodEpisodeGrounding("race", "evidence-b", "Afternoon finish.", ("medal",)),
    )
    identity = PeriodInsightIdentity.from_grounding(
        producer_key="period-producer-v1",
        episodes=episodes,
    )

    with pytest.raises(
        ValueError,
        match="period evidence asset must belong to a cited episode",
    ):
        BankedPeriodInsight(
            identity=identity,
            episode_grounding=episodes,
            thesis="Preparation leads into the race.",
            evidence=(
                BankedInsightEvidence(
                    observation="Morning preparation already shows the reward.",
                    episode_ids=("morning",),
                    asset_ids=("medal",),
                ),
            ),
            tensions=(),
            recurring_threads=(),
        )
