"""Seconds per picture, charged to the producer that actually paid them."""

from __future__ import annotations

from immich_memories.analysis.preparation_report import (
    ProducerClock,
    human_duration,
    rate_report,
    total_seconds_per_picture,
)


def _clock(steps: list[tuple[str, float]], *, pictures: int) -> ProducerClock:
    """Advance a fake clock by each step's seconds, then let that stage report."""
    elapsed = [0.0]
    clock = ProducerClock(now=lambda: elapsed[0])
    for stage, seconds in steps:
        elapsed[0] += seconds
        clock.report(stage, pictures, pictures)
    return clock


def test_time_before_a_report_belongs_to_the_producer_that_reported() -> None:
    clock = _clock([("previews", 10.0), ("public_heads", 90.0)], pictures=100)

    costs = {cost.producer: cost.seconds for cost in clock.costs()}

    assert costs == {"previews": 10.0, "public_heads": 90.0}


def test_a_producer_reporting_several_times_accumulates_its_own_time() -> None:
    clock = _clock(
        [("previews", 4.0), ("previews", 6.0), ("pixels", 20.0), ("previews", 1.0)], pictures=10
    )

    costs = {cost.producer: cost.seconds for cost in clock.costs()}

    assert costs == {"previews": 11.0, "pixels": 20.0}
    assert total_seconds_per_picture(clock.costs(), 10) == 3.1


def test_the_report_names_every_producer_its_total_and_a_library_projection() -> None:
    clock = _clock([("pixels", 25.0), ("public_heads", 75.0)], pictures=100)

    lines = "\n".join(rate_report(clock.costs(), pictures=100, library_size=10_000))

    assert "pixels" in lines
    assert "public_heads" in lines
    assert "1.0000" in lines
    assert "At this rate 10,000 pictures would take 2 h 46 min." in lines


def test_wall_clock_is_stated_in_the_unit_a_person_would_say_it_in() -> None:
    assert human_duration(26) == "26 s"
    assert human_duration(200) == "3 min"
    assert human_duration(13_260) == "3 h 41 min"


def test_a_producer_that_ran_elsewhere_shows_what_that_machine_itself_spent() -> None:
    """0.69 s a picture against 0.03 s of compute is a wire problem, and only the split says so."""
    clock = _clock([("pixels", 5.0), ("remote_facts", 69.0)], pictures=100)

    lines = rate_report(
        clock.costs(), pictures=100, library_size=100, service_seconds={"remote_facts": 3.0}
    )

    assert "service s/pic" in lines[0]
    assert "0.0300" in [line for line in lines if line.startswith("remote_facts")][0]
    assert "—" in [line for line in lines if line.startswith("pixels")][0]


def test_a_run_with_nothing_offloaded_grows_no_column_it_cannot_fill() -> None:
    clock = _clock([("pixels", 5.0)], pictures=100)

    assert "service" not in "\n".join(rate_report(clock.costs(), pictures=100, library_size=100))
