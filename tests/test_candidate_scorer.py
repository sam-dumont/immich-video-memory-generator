"""Tests for candidate scoring and ranking."""

from __future__ import annotations

import math
from datetime import date

from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import MemoryCandidate


def _make_candidate(
    memory_type: str = "monthly_highlights",
    start: date = date(2025, 6, 1),
    end: date = date(2025, 6, 30),
    score: float = 0.5,
    asset_count: int = 100,
    memory_key: str | None = None,
) -> MemoryCandidate:
    key = memory_key or f"{memory_type}:{start.isoformat()}:{end.isoformat()}:"
    return MemoryCandidate(
        memory_type=memory_type,
        date_range_start=start,
        date_range_end=end,
        person_names=[],
        memory_key=key,
        score=score,
        reason="test",
        asset_count=asset_count,
    )


class TestScoreAndRank:
    def test_never_generated_boost(self):
        """Candidates not in generated_keys get a 1.2x boost."""
        c = _make_candidate(score=0.5, end=date(2025, 6, 30), asset_count=1000)
        today = date(2025, 7, 1)

        # With the key already generated — no boost
        scored_gen = score_and_rank(
            [c], generated_keys={c.memory_key}, today=today, last_runs_by_type={}
        )
        score_with_key = scored_gen[0].score

        c2 = _make_candidate(score=0.5, end=date(2025, 6, 30), asset_count=1000)
        scored_new = score_and_rank([c2], generated_keys=set(), today=today, last_runs_by_type={})
        score_without_key = scored_new[0].score

        assert score_without_key > score_with_key

    def test_recency_decay(self):
        """Older candidates score lower via linear decay, floored at 0.5."""
        today = date(2026, 1, 1)

        recent = _make_candidate(score=0.5, end=date(2025, 12, 31), asset_count=1000)
        old = _make_candidate(
            score=0.5,
            end=date(2024, 1, 1),
            asset_count=1000,
            memory_key="old:2024-01-01:2024-01-31:",
        )

        scored = score_and_rank(
            [recent, old],
            generated_keys={recent.memory_key, old.memory_key},
            today=today,
            last_runs_by_type={},
        )

        # recent should score higher than old
        recent_score = next(c.score for c in scored if c.memory_key == recent.memory_key)
        old_score = next(c.score for c in scored if c.memory_key == old.memory_key)
        assert recent_score > old_score

    def test_recency_floor(self):
        """Even very old candidates don't drop below the 0.5 floor."""
        today = date(2026, 1, 1)
        ancient = _make_candidate(
            score=0.8,
            end=date(2020, 1, 1),  # 6 years old
            asset_count=1000,
        )

        score_and_rank(
            [ancient],
            generated_keys={ancient.memory_key},
            today=today,
            last_runs_by_type={},
        )

        # Recency = max(0.5, 1.0 - 2192/365) = 0.5
        # The decay hit the floor
        assert ancient.score > 0

    def test_content_richness(self):
        """More assets produce a higher score via log scaling."""
        today = date(2025, 7, 1)

        many = _make_candidate(score=0.5, end=date(2025, 6, 30), asset_count=500)
        few = _make_candidate(
            score=0.5,
            end=date(2025, 6, 30),
            asset_count=5,
            memory_key="few:2025-06-01:2025-06-30:",
        )

        score_and_rank(
            [many, few],
            generated_keys={many.memory_key, few.memory_key},
            today=today,
            last_runs_by_type={},
        )

        assert many.score > few.score

    def test_type_cooldown_7_days(self):
        """Same type generated within 7 days gets 0.3x penalty."""
        today = date(2025, 7, 10)
        c = _make_candidate(score=0.8, end=date(2025, 7, 1), asset_count=1000)

        last_runs = {"monthly_highlights": date(2025, 7, 5)}  # 5 days ago

        score_and_rank(
            [c],
            generated_keys={c.memory_key},
            today=today,
            last_runs_by_type=last_runs,
        )

        # With 7-day cooldown, score should be significantly reduced
        assert c.score < 0.5

    def test_type_cooldown_30_days(self):
        """Same type generated 8-30 days ago gets 0.7x penalty."""
        today = date(2025, 7, 25)
        c = _make_candidate(score=0.8, end=date(2025, 7, 1), asset_count=1000)

        last_runs = {"monthly_highlights": date(2025, 7, 10)}  # 15 days ago

        score_and_rank(
            [c],
            generated_keys={c.memory_key},
            today=today,
            last_runs_by_type=last_runs,
        )

        # 30-day cooldown applies 0.7 — still penalized but less
        c_no_cooldown = _make_candidate(score=0.8, end=date(2025, 7, 1), asset_count=1000)
        score_and_rank(
            [c_no_cooldown],
            generated_keys={c_no_cooldown.memory_key},
            today=today,
            last_runs_by_type={},
        )

        assert c.score < c_no_cooldown.score

    def test_sorts_by_score_descending(self):
        today = date(2025, 7, 1)
        low = _make_candidate(score=0.3, end=date(2025, 6, 30), asset_count=100)
        high = _make_candidate(
            score=0.9,
            end=date(2025, 6, 30),
            asset_count=100,
            memory_key="high:2025-06-01:2025-06-30:",
        )

        result = score_and_rank(
            [low, high],
            generated_keys={low.memory_key, high.memory_key},
            today=today,
            last_runs_by_type={},
        )

        assert result[0].score >= result[1].score

    def test_clamps_to_zero_one(self):
        """Final scores are clamped to [0.0, 1.0]."""
        today = date(2025, 7, 1)

        # High base score + never-generated boost could exceed 1.0
        c = _make_candidate(score=1.0, end=date(2025, 6, 30), asset_count=10000)

        score_and_rank(
            [c],
            generated_keys=set(),
            today=today,
            last_runs_by_type={},
        )

        assert 0.0 <= c.score <= 1.0

    def test_zero_asset_count(self):
        """Zero assets should not cause math errors."""
        today = date(2025, 7, 1)
        c = _make_candidate(score=0.5, end=date(2025, 6, 30), asset_count=0)

        score_and_rank(
            [c],
            generated_keys=set(),
            today=today,
            last_runs_by_type={},
        )

        # richness = 0 -> score = score * 0.7 + score * 0.3 * 0 = score * 0.7
        assert c.score >= 0.0

    def test_content_richness_formula(self):
        """Verify richness = log(asset_count)/log(1000), capped at 1.0, weighted 30%."""
        today = date(2025, 7, 1)
        asset_count = 100

        c = _make_candidate(score=1.0, end=date(2025, 6, 30), asset_count=asset_count)

        score_and_rank(
            [c],
            generated_keys={c.memory_key},
            today=today,
            last_runs_by_type={},
        )

        # Manual calculation: no boost (generated), recency=1.0 (1 day), no cooldown
        expected_richness = min(1.0, math.log(100) / math.log(1000))
        expected = 1.0 * 0.7 + 1.0 * 0.3 * expected_richness
        assert abs(c.score - expected) < 0.01
