"""Tests for run ID generation and parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from immich_memories.tracking.run_id import generate_run_id, is_valid_run_id, parse_run_id


class TestGenerateRunId:
    """Tests for generate_run_id."""

    def test_format_matches_pattern(self):
        """Generated ID matches YYYYMMDD_HHMMSS_XXXX format."""
        run_id = generate_run_id()
        assert is_valid_run_id(run_id)

    def test_uses_provided_timestamp(self):
        """Timestamp portion reflects the provided datetime."""
        ts = datetime(2025, 7, 4, 14, 30, 52)
        run_id = generate_run_id(timestamp=ts)
        assert run_id.startswith("20250704_143052_")

    def test_unique_within_same_second(self):
        """Two IDs generated for the same second are distinct."""
        ts = datetime(2025, 1, 1, 0, 0, 0)
        # Only 4 hex chars of randomness (65536 values), so keep sample
        # small to avoid birthday-paradox collisions in CI.
        ids = {generate_run_id(timestamp=ts) for _ in range(5)}
        assert len(ids) == 5

    def test_length_is_20(self):
        """Generated ID is exactly 20 characters."""
        assert len(generate_run_id()) == 20

    def test_defaults_to_now(self):
        """Without a timestamp, uses the current time."""
        before = datetime.now()
        run_id = generate_run_id()
        after = datetime.now()
        parsed = parse_run_id(run_id)
        assert parsed is not None
        assert before.replace(microsecond=0) <= parsed <= after.replace(microsecond=0)


class TestParseRunId:
    """Tests for parse_run_id."""

    def test_roundtrip(self):
        """Parsing a generated ID recovers the original timestamp."""
        ts = datetime(2026, 3, 12, 9, 15, 0)
        run_id = generate_run_id(timestamp=ts)
        parsed = parse_run_id(run_id)
        assert parsed == ts

    def test_invalid_string_returns_none(self):
        """Non-ID strings return None."""
        assert parse_run_id("not-a-run-id") is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert parse_run_id("") is None

    def test_invalid_date_returns_none(self):
        """Correct format but invalid date returns None."""
        assert parse_run_id("20251301_000000_abcd") is None

    def test_short_string_returns_none(self):
        """String shorter than 15 chars returns None."""
        assert parse_run_id("2025") is None


class TestIsValidRunId:
    """Tests for is_valid_run_id."""

    @pytest.mark.parametrize(
        "run_id",
        [
            pytest.param("20250704_143052_a7b3", id="typical"),
            pytest.param("20000101_000000_0000", id="y2k"),
            pytest.param("29991231_235959_ffff", id="far-future"),
        ],
    )
    def test_valid_ids(self, run_id: str):
        """Well-formed IDs are accepted."""
        assert is_valid_run_id(run_id) is True

    @pytest.mark.parametrize(
        "run_id,reason",
        [
            pytest.param("", "empty", id="empty"),
            pytest.param("too-short", "wrong length", id="short"),
            pytest.param("20250704_143052_a7b3x", "too long", id="long"),
            pytest.param("20250704-143052-a7b3", "wrong separator", id="dashes"),
            pytest.param("2025070_1430522_a7b3", "wrong part lengths", id="misaligned"),
            pytest.param("abcdefgh_abcdef_abcd", "non-numeric date", id="alpha-date"),
            pytest.param("20251301_000000_abcd", "invalid month 13", id="bad-month"),
            pytest.param("20250732_000000_abcd", "invalid day 32", id="bad-day"),
        ],
    )
    def test_invalid_ids(self, run_id: str, reason: str):
        """Malformed IDs are rejected."""
        assert is_valid_run_id(run_id) is False
