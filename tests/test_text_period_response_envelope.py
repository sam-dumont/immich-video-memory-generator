"""Recover complete period JSON envelopes without choosing or inventing an answer."""

from __future__ import annotations

import json

import pytest

from immich_memories.analysis.text_period_wire import (
    _period_response_object,
    _read_response,
    _response_problem,
)
from immich_memories.store.period_insights import PeriodEpisodeGrounding, PeriodInsightIdentity


def _payload() -> dict:
    return {
        "schema_version": "period-insight-text-v1",
        "thesis": "Preparation gives way to the relief of finishing.",
        "evidence": [{"observation": "They prepare and then finish.", "episodes": [1, 2]}],
        "tensions": ["Nerves versus relief."],
        "recurring_threads": ["Showing up together."],
    }


def _grounding() -> tuple[PeriodEpisodeGrounding, ...]:
    return tuple(
        PeriodEpisodeGrounding(
            episode_id=f"episode-{index}",
            evidence_key=f"evidence-{index}",
            rendered_line=f"Episode {index} is readable.",
            representative_asset_ids=(f"asset-{index}",),
        )
        for index in (1, 2)
    )


@pytest.mark.parametrize(
    "envelope",
    (
        "{response}",
        "```json\n{response}\n```",
        "Here is the reading:\n{response}",
        "{response}\nNote: Based on the supplied episodes.",
        "```json\n{response}\n```\nNote: Based on the supplied episodes.",
        "```\n{response}\n```\nNote: Based on the supplied episodes.",
    ),
)
def test_period_envelope_preserves_the_complete_answer_and_grounding(envelope: str) -> None:
    payload = _payload()
    grounding = _grounding()
    identity = PeriodInsightIdentity.from_grounding(producer_key="test", episodes=grounding)
    raw = envelope.format(response=json.dumps(payload))

    assert _period_response_object(raw) == payload
    result = _read_response(raw, identity, grounding)

    assert result is not None
    assert result.thesis == payload["thesis"]
    assert result.evidence[0].observation == payload["evidence"][0]["observation"]
    assert result.evidence[0].episode_ids == ("episode-1", "episode-2")
    assert result.evidence[0].asset_ids == ("asset-1", "asset-2")
    assert result.tensions == tuple(payload["tensions"])
    assert result.recurring_threads == tuple(payload["recurring_threads"])


@pytest.mark.parametrize(
    "envelope",
    (
        '{response}\n{{"thesis":"A conflicting answer."}}\nNote: Done.',
        '{{"wrapper":{response}\nNote: The outer object never finished.',
        "[{response}]\nNote: This is an array, not a leading object.",
        "Here is the answer:\n{response}\nNote: Do not scan past this prefix.",
        "```json\n{response}\nNote: The closing fence is missing.",
        "```python\n{response}\n```\nNote: Unsupported fence.",
        "{response}\n[1, 2]\nNote: Another JSON value.",
        "{response}\n[1, 2",
        '{response}\n"A second JSON value."',
        "{response}\nnull",
        "{response}\n42",
        "{response}\n```json\n[1]\n```",
    ),
)
def test_period_commentary_recovery_rejects_ambiguous_or_incomplete_envelopes(
    envelope: str,
) -> None:
    raw = envelope.format(response=json.dumps(_payload()))

    assert _period_response_object(raw) is None


def test_period_commentary_recovery_does_not_complete_truncated_json() -> None:
    raw = json.dumps(_payload())[:-1] + "\nNote: The answer was cut off."

    assert _period_response_object(raw) is None


@pytest.mark.parametrize("aliases", ([0], [3], [True], [1, 1], []))
def test_commentary_does_not_make_invalid_episode_aliases_valid(aliases: list) -> None:
    payload = _payload()
    payload["evidence"][0]["episodes"] = aliases
    grounding = _grounding()
    identity = PeriodInsightIdentity.from_grounding(producer_key="test", episodes=grounding)
    raw = json.dumps(payload) + "\nNote: Based on the supplied episodes."

    assert _read_response(raw, identity, grounding) is None
    assert _response_problem(raw, grounding) != "not one JSON object"


def test_commentary_does_not_make_a_wrong_schema_valid() -> None:
    payload = {**_payload(), "schema_version": "wrong-schema"}
    grounding = _grounding()
    identity = PeriodInsightIdentity.from_grounding(producer_key="test", episodes=grounding)
    raw = json.dumps(payload) + "\nNote: Based on the supplied episodes."

    assert _read_response(raw, identity, grounding) is None
    assert _response_problem(raw, grounding) == "schema_version missing or wrong"
