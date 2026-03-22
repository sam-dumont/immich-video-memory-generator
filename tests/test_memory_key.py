"""Tests for memory key generation — dedup fingerprint for automation."""

from datetime import date

from immich_memories.automation.candidates import make_memory_key


class TestMakeMemoryKey:
    def test_basic_key_no_persons(self):
        key = make_memory_key(
            memory_type="year_in_review",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
        )
        assert key == "year_in_review:2025-01-01:2025-12-31:"

    def test_key_with_single_person(self):
        key = make_memory_key(
            memory_type="person_spotlight",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            person_names=["Alice"],
        )
        assert key == "person_spotlight:2025-01-01:2025-12-31:alice"

    def test_key_with_multiple_persons_sorted(self):
        """Person names are sorted and lowered for deterministic keys."""
        key1 = make_memory_key(
            memory_type="multi_person",
            date_range_start=date(2025, 6, 1),
            date_range_end=date(2025, 6, 30),
            person_names=["Bob", "Alice"],
        )
        key2 = make_memory_key(
            memory_type="multi_person",
            date_range_start=date(2025, 6, 1),
            date_range_end=date(2025, 6, 30),
            person_names=["alice", "bob"],
        )
        assert key1 == key2
        assert key1 == "multi_person:2025-06-01:2025-06-30:alice,bob"

    def test_key_deterministic(self):
        """Same inputs always produce the same key."""
        args = {
            "memory_type": "monthly_highlights",
            "date_range_start": date(2025, 7, 1),
            "date_range_end": date(2025, 7, 31),
        }
        assert make_memory_key(**args) == make_memory_key(**args)

    def test_empty_person_list_same_as_none(self):
        key1 = make_memory_key("trip", date(2025, 8, 1), date(2025, 8, 10))
        key2 = make_memory_key("trip", date(2025, 8, 1), date(2025, 8, 10), person_names=[])
        assert key1 == key2
