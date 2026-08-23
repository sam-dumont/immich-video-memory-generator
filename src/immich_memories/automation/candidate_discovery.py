"""Turn one live Immich library snapshot into ranked memory candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.automation.failure_backoff import drop_backed_off
from immich_memories.automation.state_store import FailureStreak
from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets
from immich_memories.automation.variety import VarietyDecision, apply_variety_rules
from immich_memories.config_loader import Config
from immich_memories.config_models import AutomationConfig
from immich_memories.timeperiod import DateRange, birthday_year
from immich_memories.tracking.models import RunMetadata


class ImmichDiscoveryError(RuntimeError):
    """A live Immich library snapshot could not be collected."""


class MemoryHistoryReader(Protocol):
    """Small durable-history read seam required to avoid repeating a memory."""

    def get_generated_memory_keys(self) -> set[str]: ...

    def get_last_run_of_type(
        self,
        memory_type: str,
        source: str | None = None,
    ) -> RunMetadata | None: ...


class FailureStreakReader(Protocol):
    """Small attempt-history read seam required by failure backoff."""

    def consecutive_failures_by_key(self) -> dict[str, FailureStreak]: ...


@dataclass(frozen=True)
class DiscoveryResult:
    """Ranked candidates plus the filtering decisions that produced them."""

    candidates: list[MemoryCandidate]
    variety_decision: VarietyDecision
    backoff_skips: dict[str, str]


@dataclass(frozen=True)
class _LibrarySnapshot:
    """One live Immich read shared by every detector in a discovery pass."""

    buckets: list
    people: list
    person_asset_counts: dict[str, int]
    gps_assets: list | None


def _time_buckets_to_month_counts(
    buckets: list,
) -> dict[str, int]:
    """Convert Immich TimeBucket list to {YYYY-MM: count} dict."""
    result: dict[str, int] = {}
    for bucket in buckets:
        try:
            dt = datetime.fromisoformat(bucket.time_bucket)
            key = f"{dt.year}-{dt.month:02d}"
            result[key] = bucket.count
        except (ValueError, AttributeError):
            continue
    return result


def _trailing_year_range(today: date) -> DateRange:
    """Return one inclusive calendar-year lookback ending on ``today``."""
    try:
        start_day = today.replace(year=today.year - 1)
    except ValueError:
        # February 29 has no same-day counterpart in a non-leap year.
        start_day = today.replace(year=today.year - 1, day=28)

    return DateRange(
        start=datetime.combine(start_day, datetime.min.time()),
        end=datetime.combine(today, datetime.max.time()),
    )


def _build_last_runs_by_type(db: MemoryHistoryReader) -> dict[str, date]:
    """Query DB for the most recent completed run date per memory type."""
    result: dict[str, date] = {}
    for mem_type in (
        "monthly_highlights",
        "year_in_review",
        "person_spotlight",
        "trip",
        "multi_person",
    ):
        run = db.get_last_run_of_type(mem_type, source="auto")
        if run and run.created_at:
            result[mem_type] = (run.completed_at or run.created_at).date()
    return result


def _compute_upcoming_birthday_ids(people: list, today: date, lookahead_days: int = 7) -> set[str]:
    """Return person IDs whose birthday falls within the next N days."""
    ids: set[str] = set()
    for person in people:
        if not getattr(person, "birth_date", None):
            continue
        bday = person.birth_date
        next_bday = birthday_year(bday, today.year).start.date()
        if next_bday < today:
            next_bday = birthday_year(bday, today.year + 1).start.date()
        days_until = (next_bday - today).days
        if 0 <= days_until <= lookahead_days:
            ids.add(person.id)
    return ids


def _run_all_detectors(
    auto_cfg: AutomationConfig,
    assets_by_month: dict[str, int],
    people: list,
    generated_keys: set[str],
    config: Config,
    today: date,
    person_asset_counts: dict[str, int],
    gps_assets: list | None,
) -> list[MemoryCandidate]:
    """Run all enabled detectors and collect candidates."""
    from immich_memories.automation.calendar_detectors import (
        BirthdayDetector,
        MonthlyDetector,
        OnThisDayDetector,
        PersonSpotlightDetector,
        YearlyDetector,
    )
    from immich_memories.automation.event_detectors import (
        ActivityBurstDetector,
        MultiPersonDetector,
        TripDetector,
    )

    all_candidates: list[MemoryCandidate] = []

    if auto_cfg.detect_monthly:
        all_candidates.extend(
            MonthlyDetector().detect(assets_by_month, people, generated_keys, config, today)
        )
    if auto_cfg.detect_yearly:
        all_candidates.extend(
            YearlyDetector().detect(assets_by_month, people, generated_keys, config, today)
        )
    if auto_cfg.detect_person_spotlight:
        # WHY: suppress spotlights for people whose birthday is within 7 days
        # so BirthdayDetector fires at the right time instead
        upcoming_birthday_ids = _compute_upcoming_birthday_ids(people, today)
        all_candidates.extend(
            PersonSpotlightDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
                upcoming_birthday_ids=upcoming_birthday_ids,
            )
        )
        all_candidates.extend(
            MultiPersonDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
            )
        )
    if auto_cfg.detect_activity_burst:
        all_candidates.extend(
            ActivityBurstDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                burst_threshold=auto_cfg.burst_threshold,
            )
        )

    all_candidates.extend(
        OnThisDayDetector().detect(assets_by_month, people, generated_keys, config, today)
    )

    # Birthday detector — always on, high priority near birthdays
    if people:
        all_candidates.extend(
            BirthdayDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                person_asset_counts=person_asset_counts,
            )
        )

    if auto_cfg.detect_trips and gps_assets is not None:
        all_candidates.extend(
            TripDetector().detect(
                assets_by_month,
                people,
                generated_keys,
                config,
                today,
                assets=gps_assets,
            )
        )

    return all_candidates


class CandidateDiscovery:
    """Detect, filter, score, and rank memory candidates for one library snapshot."""

    def __init__(
        self,
        config: Config,
        runs: MemoryHistoryReader,
        attempts: FailureStreakReader,
    ) -> None:
        self._config = config
        self._runs = runs
        self._attempts = attempts

    def discover(
        self,
        *,
        limit: int,
        recent_auto_runs: list[RunMetadata],
    ) -> DiscoveryResult:
        """Detect, score, and rank memory candidates from the Immich library."""
        auto_cfg = self._config.automation
        generated_keys = self._runs.get_generated_memory_keys()
        last_runs = _build_last_runs_by_type(self._runs)
        today = date.today()

        snapshot = self._library_snapshot(auto_cfg, today)

        all_candidates = _run_all_detectors(
            auto_cfg,
            _time_buckets_to_month_counts(snapshot.buckets),
            snapshot.people,
            generated_keys,
            self._config,
            today,
            snapshot.person_asset_counts,
            snapshot.gps_assets,
        )

        all_candidates, backoff_skips = drop_backed_off(
            all_candidates,
            self._attempts.consecutive_failures_by_key(),
            datetime.now(tz=UTC),
        )

        variety_decision = apply_variety_rules(
            all_candidates,
            recent_auto_runs,
            today,
        )
        ranked = score_and_rank(
            variety_decision.eligible,
            generated_keys,
            today,
            last_runs,
        )
        return DiscoveryResult(
            candidates=ranked[:limit],
            variety_decision=variety_decision,
            backoff_skips=backoff_skips,
        )

    def _library_snapshot(self, auto_cfg: AutomationConfig, today: date) -> _LibrarySnapshot:
        """Collect every live read in one session; any transport fault ends discovery."""
        from immich_memories.api.immich import SyncImmichClient

        try:
            with SyncImmichClient(
                base_url=self._config.immich.url,
                api_key=self._config.immich.api_key,
                api_version=self._config.immich.api_version,
            ) as client:
                buckets = client.get_time_buckets()
                people = client.get_all_people() if auto_cfg.detect_person_spotlight else []
                person_asset_counts = (
                    _person_asset_counts(client, people) if auto_cfg.detect_person_spotlight else {}
                )
                gps_assets = (
                    self._trip_assets(client, buckets, today) if auto_cfg.detect_trips else None
                )
        except Exception as exc:
            raise ImmichDiscoveryError(str(exc)) from exc

        return _LibrarySnapshot(buckets, people, person_asset_counts, gps_assets)

    def _trip_assets(self, client: Any, buckets: list, today: date) -> list | None:
        """Trips are measured against a homebase; without one there is nothing to measure."""
        trips_cfg = self._config.trips
        if trips_cfg.homebase_latitude == trips_cfg.homebase_longitude == 0.0:
            return None
        return load_or_fetch_trip_assets(
            client,
            cache_root=self._config.cache.cache_path,
            server_url=self._config.immich.url,
            buckets=buckets,
            requested_range=_trailing_year_range(today),
            now=datetime.now(tz=UTC),
        )


def _person_asset_counts(client: Any, people: list) -> dict[str, int]:
    """Count assets for the top named people only — a spotlight cannot use the rest."""
    named = [p for p in people if p.name and p.thumbnail_path][:10]
    return {p.id: client.get_person_asset_count(p.id) for p in named}
