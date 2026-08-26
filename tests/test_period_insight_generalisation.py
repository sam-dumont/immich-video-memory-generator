"""Generated controls keep Pass 0 structural, not topic-specific."""

import json

import pytest

from immich_memories.analysis.period_insight_answer import (
    read_episode_answers,
    read_period_answer,
)


@pytest.mark.parametrize(
    ("first_label", "second_label"),
    (
        ("cycling", "live-show"),
        ("live-show", "cycling"),
        ("glassblowing", "night-market"),
    ),
)
def test_topic_label_swaps_preserve_grounded_insight_shape(
    first_label: str,
    second_label: str,
) -> None:
    """Labels may change prose, but they cannot alter qualified pixel-to-ID grounding."""
    episode_ids = ("ep::δ/42", "occasion|q9")
    page_id = "generated-pack-π-001"
    asset_ids = ("asset#left:001", "asset/right?002")
    episode_raw = json.dumps(
        {
            "schema_version": "episode-scan-v2",
            "pack": 1,
            "episode_readings": [
                {
                    "episode": number,
                    "page": 1,
                    "visual_summary": f"Generated {label} evidence.",
                    "representative_tiles": [number],
                    "representative_reason": f"The {label} contribution is visible.",
                }
                for number, (_episode_id, label) in enumerate(
                    zip(episode_ids, (first_label, second_label), strict=True),
                    start=1,
                )
            ],
        }
    )
    episode_answer = read_episode_answers(
        episode_raw,
        pack_alias=1,
        expected_observations=tuple((episode_id, page_id) for episode_id in episode_ids),
        observation_map={
            (number, 1): (episode_id, page_id)
            for number, episode_id in enumerate(episode_ids, start=1)
        },
        tile_map={
            (number, 1, number): asset_id
            for number, (_episode_id, asset_id) in enumerate(
                zip(episode_ids, asset_ids, strict=True),
                start=1,
            )
        },
    )
    assert episode_answer is not None

    period_raw = json.dumps(
        {
            "schema_version": "period-insight-v1",
            "period_insight": {
                "thesis": f"{first_label} contrasts with {second_label}.",
                "evidence": [
                    {
                        "observation": f"Generated relationship {index}.",
                        "representative_tiles": [index],
                    }
                    for index in (1, 2)
                ],
                "tensions": ["generated contrast"],
                "recurring_threads": ["generated recurrence"],
                "unavailable_reason": None,
            },
        }
    )
    period_answer = read_period_answer(
        period_raw,
        page_ids=("generated-period-001",),
        tile_map={
            ("generated-period-001", number): (episode_id, asset_id)
            for number, (episode_id, asset_id) in enumerate(
                zip(episode_ids, asset_ids, strict=True),
                start=1,
            )
        },
    )
    assert period_answer is not None

    shape = (
        len(episode_answer.readings),
        tuple(len(reading.representative_asset_ids) for reading in episode_answer.readings),
        len(period_answer.evidence),
        tuple(len(item.episode_ids) for item in period_answer.evidence),
        tuple(len(item.asset_ids) for item in period_answer.evidence),
        len(period_answer.tensions),
        len(period_answer.recurring_threads),
    )
    assert shape == (2, (1, 1), 2, (1, 1), (1, 1), 1, 1)
    assert tuple(reading.representative_asset_ids for reading in episode_answer.readings) == (
        (asset_ids[0],),
        (asset_ids[1],),
    )
    assert tuple(item.asset_ids for item in period_answer.evidence) == (
        (asset_ids[0],),
        (asset_ids[1],),
    )
