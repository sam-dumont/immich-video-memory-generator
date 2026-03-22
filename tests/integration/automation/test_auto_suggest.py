"""Integration tests for auto suggest against a real Immich library.

Requires: Immich server with data (Sam's 2025 library is the reference dataset).
Run: make test-integration-automation
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from immich_memories.cli import main
from tests.integration.immich_fixtures import requires_immich


def _extract_json(output: str) -> list:
    """Extract JSON array from CLI output that may contain log lines."""
    # WHY: CliRunner mixes log lines (containing "[INFO]") with JSON output.
    # Find the line that starts with "[" followed by a newline or "{" (JSON array start).
    for i, line in enumerate(output.split("\n")):
        stripped = line.strip()
        if stripped.startswith("[") and (stripped == "[" or stripped.startswith("[{")):
            json_text = "\n".join(output.split("\n")[i:])
            return json.loads(json_text)
    msg = f"No JSON array found in output: {output[:200]}"
    raise ValueError(msg)


pytestmark = [pytest.mark.integration, requires_immich]


@pytest.fixture(scope="module")
def config():
    from immich_memories.config_loader import Config

    return Config.from_yaml(Config.get_default_path())


@pytest.fixture(scope="module")
def runner(config):
    from immich_memories.automation.runner import AutoRunner

    return AutoRunner(config)


@pytest.fixture(scope="module")
def candidates(runner):
    """Run suggest once for the whole module (expensive: hits Immich API)."""
    return runner.suggest(limit=20)


class TestAutoSuggest:
    def test_returns_candidates(self, candidates):
        assert len(candidates) > 0

    def test_candidates_have_required_fields(self, candidates):
        for c in candidates:
            assert c.memory_type
            assert c.memory_key
            assert c.reason
            assert 0 <= c.score <= 1
            assert c.date_range_start <= c.date_range_end

    def test_candidates_sorted_by_score(self, candidates):
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_per_type_cap_respected(self, candidates):
        from collections import Counter

        counts = Counter(c.memory_type for c in candidates)
        for mem_type, count in counts.items():
            if mem_type == "on_this_day":
                assert count <= 1
            elif mem_type == "multi_person":
                assert count <= 2
            else:
                assert count <= 3

    def test_no_duplicate_memory_keys(self, candidates):
        keys = [c.memory_key for c in candidates]
        assert len(keys) == len(set(keys))


class TestDetectorCoverage:
    """Verify that real detectors produce results against real data."""

    def test_monthly_highlights_detected(self, candidates):
        monthly = [c for c in candidates if c.memory_type == "monthly_highlights"]
        assert len(monthly) > 0
        assert all(c.asset_count > 0 for c in monthly)

    def test_year_in_review_detected(self, candidates):
        yearly = [c for c in candidates if c.memory_type == "year_in_review"]
        assert len(yearly) > 0
        assert all(c.asset_count > 100 for c in yearly)

    def test_person_spotlight_detected(self, candidates):
        people = [c for c in candidates if c.memory_type == "person_spotlight"]
        assert len(people) > 0
        assert all(c.person_names for c in people)
        assert all(c.asset_count > 0 for c in people)

    def test_trip_detected(self, candidates, config):
        """Trip detection requires homebase configured and GPS data."""
        if config.trips.homebase_latitude == config.trips.homebase_longitude == 0.0:
            pytest.skip("No homebase configured")
        trips = [c for c in candidates if c.memory_type == "trip"]
        assert len(trips) > 0
        for trip in trips:
            assert trip.asset_count > 0
            assert "trip" in trip.reason.lower() or "day" in trip.reason.lower()

    def test_multi_person_detected(self, candidates):
        pairs = [c for c in candidates if c.memory_type == "multi_person"]
        assert len(pairs) > 0
        for pair in pairs:
            assert len(pair.person_names) == 2
            assert pair.asset_count > 0
            assert "&" in pair.reason


class TestCliSuggest:
    """Test the CLI command output format via CliRunner."""

    def test_json_output_valid(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "suggest", "--json", "--limit", "5"])
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "memory_type" in data[0]
        assert "score" in data[0]
        assert "reason" in data[0]

    def test_table_output_has_headers(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "suggest", "--limit", "3"])
        assert result.exit_code == 0, result.output
        assert "Type" in result.output
        assert "Score" in result.output
        assert "Reason" in result.output

    def test_type_filter(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "suggest", "--json", "--type", "year_in_review"])
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert all(c["memory_type"] == "year_in_review" for c in data)


class TestCliRunDryRun:
    def test_dry_run_does_not_generate(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "run", "--dry-run"])
        assert result.exit_code == 0, result.output


class TestCliHistory:
    def test_history_does_not_crash(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "history"])
        assert result.exit_code == 0, result.output


class TestSystemScheduler:
    def test_install_show(self):
        runner = CliRunner()
        result = runner.invoke(main, ["auto", "install", "--show"])
        assert result.exit_code == 0, result.output
        assert len(result.output) > 50
