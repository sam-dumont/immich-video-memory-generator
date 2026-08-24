"""The catalogue's own days, proposed on their anniversaries.

Every day here is invented. Real catalogue entries name real people and real
places, and this file is public.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import MagicMock

from immich_memories.automation.candidate_discovery import _run_all_detectors
from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import (
    CandidateCategory,
    MemoryCandidate,
    make_memory_key,
)
from immich_memories.automation.catalogue import entries_from
from immich_memories.automation.generation_request import GenerationRequest
from immich_memories.automation.special_day_detector import SpecialDayDetector
from immich_memories.automation.special_day_scan import DiscoveredDay
from immich_memories.cli.auto_cmd import _candidates_to_json, _print_candidates_table
from immich_memories.config_models_automation import AutomationConfig


def _a_day(
    day: date,
    *,
    title: str = "A long evening out",
    photos: int = 200,
    active_hours: int = 8,
    window: tuple[datetime, datetime] | None = None,
) -> DiscoveredDay:
    return DiscoveredDay(
        day=day,
        title=title,
        subtitle="",
        what="people were out late",
        photos=photos,
        window=window,
        active_hours=active_hours,
    )


def _detect(catalogue: list[DiscoveredDay] | None, today: date):
    return SpecialDayDetector().detect(
        {},
        [],
        set(),
        None,
        today,
        catalogue=catalogue,
    )


class TestAnniversaryTrigger:
    def test_a_day_whose_anniversary_is_today_is_proposed(self):
        candidates = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))

        assert len(candidates) == 1
        assert candidates[0].category is CandidateCategory.EMERGENT_DAY
        assert candidates[0].memory_type == "special_day"

    def test_a_day_four_months_away_is_not_proposed(self):
        assert _detect([_a_day(date(2016, 6, 12))], date(2026, 2, 12)) == []


def _final_score(day: date, today: date) -> float:
    """What a 200-photo catalogued day is worth once the scorer has had it."""
    ranked = score_and_rank(
        _detect([_a_day(day, photos=200)], today),
        generated_keys=set(),
        today=today,
        last_runs_by_type={},
    )
    return round(ranked[0].score, 3)


class TestRoundnessLadder:
    """A decade outranks a half-decade outranks an ordinary year (design 2.5)."""

    def test_a_decade_scores_highest_of_all_detectors(self):
        # Above the monthly review's 0.776, which is the whole product claim.
        assert _final_score(date(2016, 6, 12), date(2026, 6, 12)) == 0.893

    def test_a_half_decade_sits_between_monthly_and_birthday(self):
        assert _final_score(date(2011, 6, 12), date(2026, 6, 12)) == 0.759

    def test_an_ordinary_anniversary_sits_between_multi_person_and_trip(self):
        assert _final_score(date(2019, 6, 12), date(2026, 6, 12)) == 0.536


# One candidate per existing detector, and what the scorer paid it before
# `recency_date` existed. Recorded by running origin/main's scorer, not by
# re-deriving its formula here: a pin that recomputes the implementation
# agrees with any change to it, which is the opposite of a pin.
_PINNED_TODAY = date(2026, 8, 24)
_PINNED_SCORES = {
    "monthly_highlights:monthly_review": 0.7717730044828872,
    "monthly_highlights:activity_burst": 0.6420805109650133,
    "person_spotlight:birthday": 0.5005479452054794,
    "year_in_review:year_in_review": 0.432,
    "on_this_day:on_this_day": 0.3897076512400188,
    "person_spotlight:person_spotlight": 0.36,
    "trip:trip": 0.2241019891817383,
    "multi_person:multi_person": 0.09899999999999999,
}
# category value -> (memory_type, start, end, raw detector score, assets)
_PINNED_INPUTS = {
    "monthly_review": ("monthly_highlights", "2026-07-01", "2026-07-31", 0.70, 683),
    "activity_burst": ("monthly_highlights", "2026-05-01", "2026-05-31", 0.70, 921),
    "year_in_review": ("year_in_review", "2025-01-01", "2025-12-31", 0.72, 13151),
    "person_spotlight": ("person_spotlight", "2025-01-01", "2025-12-31", 0.60, 16464),
    "birthday": ("person_spotlight", "2026-01-01", "2026-03-15", 0.75, 4210),
    "multi_person": ("multi_person", "2025-01-01", "2025-12-31", 0.55, 2564),
    "on_this_day": ("on_this_day", "2026-08-24", "2026-08-24", 0.35, 190),
    "trip": ("trip", "2025-07-26", "2025-08-10", 0.449, 960),
}


class TestExistingDetectorsAreUntouched:
    def test_a_candidate_without_a_recency_date_scores_exactly_as_before(self):
        """The eight detectors that never heard of `recency_date` must not move."""
        candidates = [
            MemoryCandidate(
                memory_type=memory_type,
                category=CandidateCategory(detector),
                date_range_start=date.fromisoformat(start),
                date_range_end=date.fromisoformat(end),
                person_names=[],
                memory_key=f"{memory_type}:{detector}",
                score=raw,
                reason="r",
                asset_count=assets,
            )
            for detector, (memory_type, start, end, raw, assets) in _PINNED_INPUTS.items()
        ]

        ranked = score_and_rank(
            candidates,
            generated_keys={"trip:trip"},
            today=_PINNED_TODAY,
            last_runs_by_type={"multi_person": date(2026, 8, 20)},
        )

        assert {c.memory_key: c.score for c in ranked} == _PINNED_SCORES


class TestMemoryKeyFormat:
    """`generated_keys` is durable history: a key that changes shape un-dedups it."""

    def test_a_key_without_a_discriminator_is_unchanged(self):
        assert (
            make_memory_key("monthly_highlights", date(2026, 7, 1), date(2026, 7, 31))
            == "monthly_highlights:2026-07-01:2026-07-31:"
        )
        assert (
            make_memory_key(
                "person_spotlight", date(2025, 1, 1), date(2025, 12, 31), ["Bea", "alex"]
            )
            == "person_spotlight:2025-01-01:2025-12-31:alex,bea"
        )

    def test_a_discriminator_appends_exactly_one_segment(self):
        assert (
            make_memory_key(
                "special_day", date(2016, 6, 12), date(2016, 6, 12), discriminator="10y"
            )
            == "special_day:2016-06-12:2016-06-12::10y"
        )

    def test_a_day_can_come_back_on_a_later_round_anniversary(self):
        """Ten years and fifteen years are two memories, not one already spent."""
        at_ten = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]
        at_fifteen = _detect([_a_day(date(2016, 6, 12))], date(2031, 6, 12))[0]

        assert at_ten.memory_key != at_fifteen.memory_key
        assert _detect([_a_day(date(2016, 6, 12))], date(2031, 6, 12)) != []
        assert (
            SpecialDayDetector().detect(
                {},
                [],
                {at_ten.memory_key},
                None,
                date(2026, 6, 12),
                catalogue=[_a_day(date(2016, 6, 12))],
            )
            == []
        )


class TestGenerationRequest:
    def test_an_emergent_day_generates_a_special_day_from_its_date(self):
        candidate = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]

        argv = GenerationRequest.from_candidate(candidate, upload=False).to_argv()

        assert argv[argv.index("--memory-type") + 1] == "special_day"
        assert argv[argv.index("--day") + 1] == "2016-06-12"

    def test_the_catalogue_title_never_reaches_the_command_line(self):
        """argv is readable in `ps` and in launchd's logs; the title names people."""
        title = "A long evening out"
        candidate = _detect([_a_day(date(2016, 6, 12), title=title)], date(2026, 6, 12))[0]

        rendered = " ".join(GenerationRequest.from_candidate(candidate, upload=False).to_argv())

        assert title not in rendered
        assert "evening" not in rendered
        assert "people were out late" not in rendered


class TestOneSurprisePerRun:
    def test_two_due_days_leave_one_candidate(self):
        """A night gets one unrequested memory, and it is the rounder one."""
        today = date(2026, 6, 12)
        catalogue = [
            _a_day(date(2019, 6, 11), title="A morning at the lake"),
            _a_day(date(2016, 6, 13), title="The day the kitchen flooded"),
        ]

        ranked = score_and_rank(
            _detect(catalogue, today),
            generated_keys=set(),
            today=today,
            last_runs_by_type={},
        )

        assert len(ranked) == 1
        assert ranked[0].date_range_start == date(2016, 6, 13)


class TestNoCatalogue:
    """Nobody has to run a twenty-year scan for the other eight to work."""

    def test_no_catalogue_at_all_yields_nothing(self):
        assert _detect(None, date(2026, 6, 12)) == []

    def test_an_empty_catalogue_yields_nothing(self):
        assert _detect([], date(2026, 6, 12)) == []

    def test_an_unreadable_file_reads_as_an_empty_catalogue(self, tmp_path):
        unreadable = tmp_path / "special-days.json"
        unreadable.write_text("{ this was half-written when the scan was killed")

        assert entries_from(unreadable) == []
        assert _detect(entries_from(unreadable), date(2026, 6, 12)) == []

    def test_discovery_still_runs_the_other_detectors(self):
        """A missing catalogue costs the emergent day and nothing else."""
        # WHY: Config is a large pydantic tree the detectors only read flags off;
        # the other eight detectors in this call are the real subject.
        config = MagicMock()
        today = date(2026, 8, 24)

        def run(catalogue):
            return _run_all_detectors(
                AutomationConfig(),
                {"2026-07": 683},
                [],
                set(),
                config,
                today,
                {},
                None,
                catalogue,
            )

        without = run(None)
        with_a_due_day = run([_a_day(date(2016, 8, 24))])

        assert [c.category for c in without] != []
        assert [c.category for c in with_a_due_day] == [
            *[c.category for c in without],
            CandidateCategory.EMERGENT_DAY,
        ]


class TestWhereItCameFrom:
    """A proposal has to say what volunteered it, or it reads as a coincidence."""

    def test_the_candidate_names_its_source(self):
        candidate = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]

        assert candidate.extra_params["source"] == "special-days catalogue"

    def test_suggest_renders_the_source_in_json(self):
        candidate = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]

        rows = json.loads(_candidates_to_json([candidate]))

        assert rows[0]["source"] == "special-days catalogue"

    def test_suggest_renders_the_source_in_the_table(self, capsys):
        candidate = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]

        _print_candidates_table([candidate])

        # Rich wraps the cell to the terminal, so "special-days" can arrive
        # hyphenated; "catalogue" is the word that survives any width.
        assert "catalogue" in capsys.readouterr().out

    def test_a_candidate_with_no_source_renders_a_null_not_a_missing_key(self):
        rows = json.loads(
            _candidates_to_json(
                [
                    MemoryCandidate(
                        memory_type="trip",
                        category=CandidateCategory.TRIP,
                        date_range_start=date(2025, 7, 26),
                        date_range_end=date(2025, 8, 10),
                        person_names=[],
                        memory_key="trip:x",
                        score=0.4,
                        reason="16-day trip",
                        asset_count=960,
                    )
                ]
            )
        )

        assert rows[0]["source"] is None


class TestEvidenceNotTitle:
    def test_the_reason_is_evidence_and_never_the_catalogue_title(self):
        """`reason` reaches suggest --json, the attempt row, and notifications."""
        title = "The day the kitchen flooded"
        candidate = _detect(
            [_a_day(date(2016, 6, 12), title=title, photos=289, active_hours=18)],
            date(2026, 6, 12),
        )[0]

        assert candidate.reason == "10 years ago today · 289 photos over 18 hours"
        assert title not in candidate.reason

    def test_no_real_name_enters_durable_run_history(self):
        """person_names lands in RunMetadata.memory_people, which is kept."""
        candidate = _detect([_a_day(date(2016, 6, 12))], date(2026, 6, 12))[0]

        assert candidate.person_names == []
