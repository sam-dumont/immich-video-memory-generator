"""One contract for what the CLI and the web UI must agree on.

    Same memory specification -> same date windows, person filter, target
    duration and fetch calls, regardless of entrypoint, unless explicitly
    documented otherwise.

Every memory type is resolved twice from a single spec -- once the way
``cli/generate.py`` resolves it, once the way ``ui/pages/step1_presets.py``
does -- and the two answers are compared. A type with no entry in ``SPECS`` is
a failure, so a new memory type cannot ship without declaring parity or an
exception.

Differences the project decided on live in ``DOCUMENTED_DURATION_SPLIT`` and
are asserted *exactly*, so an intentional split still fails the day either side
moves. Differences nobody decided on are recorded as strict ``xfail`` with the
description they need in a tracker. Repairing them is not this file's job --
each changes what a memory contains and wants its own PR and contact sheets.
The point is to make the next divergence loud, not to quietly fix this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime

import pytest

from immich_memories.api.models import Person
from immich_memories.cli._date_resolution import default_duration_for_type, resolve_date_range
from immich_memories.cli._pipeline_runner import fetch_photos, fetch_videos_and_live_photos
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange
from immich_memories.ui.pages import step2_loading
from immich_memories.ui.state import AppState


@dataclass(frozen=True)
class MemorySpec:
    """One request, phrased once, replayed through both entrypoints.

    The CLI reads these as flags and the UI as ``memory_preset_params`` keys,
    so a field here is the single place a memory's inputs are written down.
    """

    year: int | None = None
    month: int | None = None
    season: str | None = None
    hemisphere: str = "north"
    holiday: str | None = None
    years_back: int | None = None
    on_this_day_target: date | None = None
    trip_start: date | None = None
    trip_end: date | None = None
    location_name: str | None = None
    people: tuple[Person, ...] = ()

    def as_preset_params(self) -> dict:
        """The spec as the UI's ``memory_preset_params`` bag."""
        params = {
            "person_names": [person.name for person in self.people] or None,
            "year": self.year,
            "month": self.month,
            "season": self.season,
            "hemisphere": self.hemisphere,
            "holiday": self.holiday,
            "years_back": self.years_back,
            "target_date": self.on_this_day_target,
            "trip_start": self.trip_start,
            "trip_end": self.trip_end,
            "location_name": self.location_name,
        }
        return {key: value for key, value in params.items() if value is not None}


# The people a spec can name, as the wizard would have picked them.
ALICE = Person(id="person-alice", name="Alice")
BOB = Person(id="person-bob", name="Bob")

# Every memory type that has a preset, with a spec both surfaces can be handed.
# ALBUM is absent on purpose -- see ALBUM_HAS_NO_PRESET.
SPECS: dict[MemoryType, MemorySpec] = {
    MemoryType.YEAR_IN_REVIEW: MemorySpec(year=2024),
    MemoryType.SEASON: MemorySpec(year=2024, season="summer"),
    MemoryType.PERSON_SPOTLIGHT: MemorySpec(year=2024, people=(ALICE,)),
    MemoryType.MULTI_PERSON: MemorySpec(year=2024, people=(ALICE, BOB)),
    MemoryType.MONTHLY_HIGHLIGHTS: MemorySpec(year=2024, month=3),
    MemoryType.ON_THIS_DAY: MemorySpec(on_this_day_target=date(2024, 6, 15), years_back=5),
    MemoryType.HOLIDAY: MemorySpec(year=2024, holiday="christmas", years_back=5),
    MemoryType.THEN_AND_NOW: MemorySpec(year=2024, years_back=10),
    MemoryType.TRIP: MemorySpec(
        year=2024,
        trip_start=date(2024, 7, 1),
        trip_end=date(2024, 7, 10),
        location_name="Rome",
    ),
}

# An album brings its own assets, so neither surface resolves a window for it:
# --from-album on the CLI, the Album card in the wizard, and both take the span
# from what the album holds. Registering a preset for it must fail this file.
ALBUM_HAS_NO_PRESET = MemoryType.ALBUM

# A special day has a preset but nothing to reach it with yet. Its scope comes
# from a catalogue entry -- the day, its window, its title -- and no flag or
# card carries one, so there is no spec either surface could be handed. It
# joins SPECS when `generate --day` and the Surprise me card land, and this
# exception has to be deleted in the same PR.
SPECIAL_DAY_HAS_NO_SURFACE_YET = MemoryType.SPECIAL_DAY

# --memory-type's choices, copied from cli/generate_options.py so a type added to the
# registry and not to the flag is caught here rather than by a user.
CLI_MEMORY_TYPE_CHOICES = frozenset(
    {
        "year_in_review",
        "season",
        "person_spotlight",
        "multi_person",
        "monthly_highlights",
        "on_this_day",
        "trip",
        "holiday",
        "then_and_now",
    }
)


@dataclass(frozen=True)
class DocumentedDifference:
    """A difference the project decided on, and the record that allows it."""

    cli: float
    ui: float
    recorded_at: str


# The wizard's cards carry a fixed length; the CLI fits a curve through the
# date range. Both numbers are editable defaults for the surface they belong
# to, which is the product decision #630 wrote down. Asserted to the value, so
# a change on either side still lands here.
_SPLIT_RECORD = "docs/create/memory-types/monthly-person-season.mdx#ui-and-cli-defaults-disagree"
DOCUMENTED_DURATION_SPLIT: dict[MemoryType, DocumentedDifference] = {
    MemoryType.SEASON: DocumentedDifference(cli=195.02, ui=135, recorded_at=_SPLIT_RECORD),
    MemoryType.PERSON_SPOTLIGHT: DocumentedDifference(cli=600.0, ui=120, recorded_at=_SPLIT_RECORD),
    MemoryType.MULTI_PERSON: DocumentedDifference(cli=600.0, ui=300, recorded_at=_SPLIT_RECORD),
    MemoryType.MONTHLY_HIGHLIGHTS: DocumentedDifference(
        cli=62.30, ui=60, recorded_at=_SPLIT_RECORD
    ),
}

# Divergences nobody decided on. Strict xfail, so the day one is repaired the
# entry has to be deleted -- a fix cannot land while the record still claims
# the surfaces disagree.
TRIP_WINDOW_DIVERGENCE = (
    "The CLI resolves --memory-type trip to the whole calendar year: "
    "_resolve_memory_type_dates falls through to calendar_year() because no flag "
    "carries the trip's dates. The window it really fetches is built a third time, "
    "inline at cli/_trip_generation.py, from the detected trip -- and that copy ends "
    "the last day at 23:59:59.999999 where date_builders.build_trip ends it at "
    "23:59:59. The wizard calls build_trip through the preset. One rule, three "
    "implementations, which is #658's disease on a second memory type. Duration "
    "follows the window, so the CLI plans 300s off a 366-day span where the wizard "
    "shows the trip's own editorial length."
)


def _bounds(date_range: DateRange) -> tuple[datetime, datetime]:
    return (date_range.start, date_range.end)


def _windows(resolved: DateRange | list[DateRange]) -> list[tuple[datetime, datetime]]:
    """A date resolution as plain comparable bounds, single or multi-window."""
    ranges = resolved if isinstance(resolved, list) else [resolved]
    return [_bounds(r) for r in ranges]


def _cli_resolution(memory_type: MemoryType, spec: MemorySpec) -> DateRange | list[DateRange]:
    return resolve_date_range(
        spec.year,
        None,
        None,
        None,
        None,
        memory_type=str(memory_type),
        season=spec.season,
        month=spec.month,
        hemisphere=spec.hemisphere,
        years_back=spec.years_back,
        on_this_day_target=spec.on_this_day_target,
        holiday=spec.holiday,
    )


def cli_windows(memory_type: MemoryType, spec: MemorySpec) -> list[tuple[datetime, datetime]]:
    """The windows ``cli/generate.py`` searches for this spec."""
    return _windows(_cli_resolution(memory_type, spec))


def cli_duration(memory_type: MemoryType, spec: MemorySpec) -> float | None:
    """The target length ``cli/generate.py`` picks when --duration is absent.

    Mirrors _resolve_generation_scope, which collapses a multi-window memory to
    one span before asking for a duration.
    """
    resolved = _cli_resolution(memory_type, spec)
    if isinstance(resolved, list):
        resolved = DateRange(start=resolved[-1].start, end=resolved[0].end)
    return default_duration_for_type(str(memory_type), resolved)


def ui_windows(memory_type: MemoryType, spec: MemorySpec) -> list[tuple[datetime, datetime]]:
    """The windows the wizard writes into ``state.date_ranges`` for this spec.

    Mirrors step1_presets._apply_preset_to_state, the UI's only call into
    create_preset, which copies preset.date_ranges verbatim.
    """
    return _windows(create_preset(memory_type, **spec.as_preset_params()).date_ranges)


def ui_duration(memory_type: MemoryType, spec: MemorySpec) -> float | None:
    """The target length the wizard puts in the duration box for this spec."""
    return create_preset(memory_type, **spec.as_preset_params()).default_duration_seconds


def _parametrized_types(divergent: dict[MemoryType, str]):
    """Every type in SPECS, with the ones known to disagree marked xfail."""
    return [
        pytest.param(
            memory_type,
            marks=pytest.mark.xfail(reason=divergent[memory_type], strict=True)
            if memory_type in divergent
            else (),
            id=str(memory_type),
        )
        for memory_type in SPECS
    ]


class TestRegistryCoverage:
    """A new memory type cannot ship without declaring what parity means for it."""

    def test_every_memory_type_has_parity_data(self) -> None:
        declared = set(SPECS) | {ALBUM_HAS_NO_PRESET, SPECIAL_DAY_HAS_NO_SURFACE_YET}
        missing = set(MemoryType) - declared
        assert not missing, (
            f"Memory types with no parity data: {sorted(str(m) for m in missing)}. "
            "Add a MemorySpec to SPECS so both surfaces are compared for it, or "
            "name the type as a documented exception."
        )

    def test_cli_offers_every_type_that_has_a_preset(self) -> None:
        assert {str(memory_type) for memory_type in SPECS} == CLI_MEMORY_TYPE_CHOICES

    def test_album_resolves_no_window_on_either_surface(self) -> None:
        assert str(ALBUM_HAS_NO_PRESET) not in CLI_MEMORY_TYPE_CHOICES
        with pytest.raises(ValueError, match="No preset factory"):
            create_preset(ALBUM_HAS_NO_PRESET)


class TestDateWindowParity:
    """Same spec, same windows to search."""

    @pytest.mark.parametrize(
        "memory_type", _parametrized_types({MemoryType.TRIP: TRIP_WINDOW_DIVERGENCE})
    )
    def test_windows_match(self, memory_type: MemoryType) -> None:
        spec = SPECS[memory_type]
        assert cli_windows(memory_type, spec) == ui_windows(memory_type, spec)


class TestTargetDurationParity:
    """Same spec, same default length -- or the split #630 wrote down."""

    @pytest.mark.parametrize(
        "memory_type", _parametrized_types({MemoryType.TRIP: TRIP_WINDOW_DIVERGENCE})
    )
    def test_duration_matches_or_matches_the_record(self, memory_type: MemoryType) -> None:
        spec = SPECS[memory_type]
        cli, ui = cli_duration(memory_type, spec), ui_duration(memory_type, spec)
        documented = DOCUMENTED_DURATION_SPLIT.get(memory_type)
        if documented is None:
            assert cli == pytest.approx(ui)
            return
        assert cli == pytest.approx(documented.cli, abs=0.01), (
            f"The CLI's length for {memory_type} moved; {documented.recorded_at} still "
            f"says {documented.cli}s."
        )
        assert ui == documented.ui, (
            f"The wizard's card for {memory_type} moved; {documented.recorded_at} still "
            f"says {documented.ui}s."
        )


# WHY: RecordingClient and SilentProgress replace the only two boundaries
# either fetch path crosses -- the Immich HTTP API and the terminal. The client
# records each query instead of answering it, because the contract is about
# what a surface asks for, not what comes back. Both surfaces get one of these
# and nothing else, so a difference in the two logs is a difference in the two
# surfaces rather than in what they were tested against.
@dataclass
class RecordingClient:
    """Stands in for the Immich HTTP API on both surfaces at once.

    The three video endpoints normalise to one recorded shape, so the
    comparison is about which people and which window rather than which method
    name got there: asking for one person by id and asking for a group of one
    mean the same thing.
    """

    calls: list[tuple[str, tuple[str, ...], tuple[datetime, datetime]]] = field(
        default_factory=list
    )

    def get_videos_for_all_persons(self, person_ids, date_range) -> list:
        self.calls.append(("videos", tuple(person_ids), _bounds(date_range)))
        return []

    def get_videos_for_person_and_date_range(self, person_id, date_range) -> list:
        self.calls.append(("videos", (person_id,), _bounds(date_range)))
        return []

    def get_videos_for_date_range(self, date_range) -> list:
        self.calls.append(("videos", (), _bounds(date_range)))
        return []

    def get_photos_for_date_range(self, date_range, person_id=None, person_ids=None) -> list:
        people = tuple(person_ids) if person_ids else ((person_id,) if person_id else ())
        self.calls.append(("photos", people, _bounds(date_range)))
        return []

    def __enter__(self) -> RecordingClient:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class SilentProgress:
    """The CLI fetch's ProgressDisplay protocol, writing nowhere."""

    def add_task(self, _description: str, **_fields) -> int:
        return 0

    def update(self, _task_id: int, **_kwargs) -> None:
        return None


def cli_fetch_calls(
    windows: list[DateRange], people: tuple[Person, ...], *, include_photos: bool
) -> list:
    """What ``cli/_pipeline_runner`` asks Immich for, given windows and people."""
    client = RecordingClient()
    person_ids = [person.id for person in people]
    fetch_videos_and_live_photos(
        client=client,
        # Read only on the live-photo branch, which use_live_photos closes.
        config=None,
        progress=SilentProgress(),
        date_ranges=windows,
        person_ids=person_ids,
        use_live_photos=False,
    )
    if include_photos:
        fetch_photos(client=client, date_ranges=windows, person_ids=person_ids)
    return client.calls


def _wizard_state(
    memory_type: MemoryType, windows: list[DateRange], people: tuple[Person, ...]
) -> AppState:
    """The state the wizard's two person widgets leave behind.

    step1_presets writes a single pick into ``state.selected_person`` and a
    multi-person pick into ``memory_preset_params["person_ids"]`` -- two
    different fields, and only the Person Spotlight and Multi-Person cards
    render a person widget at all.
    """
    state = AppState()
    state.date_ranges = windows
    if memory_type is MemoryType.PERSON_SPOTLIGHT and people:
        state.selected_person = people[0]
        state.memory_preset_params = {"person_id": people[0].id}
    elif memory_type is MemoryType.MULTI_PERSON:
        state.memory_preset_params = {"person_ids": [person.id for person in people]}
    return state


def ui_fetch_calls(
    memory_type: MemoryType,
    windows: list[DateRange],
    people: tuple[Person, ...],
    *,
    include_photos: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> list:
    """What ``ui/pages/step2_loading`` asks Immich for, given the same inputs.

    The page builds its own SyncImmichClient with no seam to inject one, so the
    class is swapped for the recorder. Its two fetch helpers are private and
    called here anyway: they are where the wizard's query is decided, and the
    public route around them wants a running NiceGUI app to reach.
    """
    client = RecordingClient()
    monkeypatch.setattr(step2_loading, "SyncImmichClient", lambda **_kwargs: client)
    state = _wizard_state(memory_type, windows, people)
    step2_loading._fetch_assets(state)
    if include_photos:
        step2_loading._fetch_photos(state)
    return client.calls


@dataclass(frozen=True)
class FetchScenario:
    """A memory type and the people a user named, replayed on both surfaces."""

    memory_type: MemoryType
    people: tuple[Person, ...]
    divergence: str | None = None


MULTI_PERSON_OF_ONE_DIVERGENCE = (
    "A Multi-Person memory naming one person is unfiltered in the wizard and "
    "filtered on the CLI. step2_loading._fetch_assets only reads "
    "memory_preset_params['person_ids'] when it holds two or more, and the "
    "Multi-Person card never sets state.selected_person, so the single id falls "
    "through to the whole-window query. The CLI's fetch_videos_and_live_photos "
    "branches on len(person_ids) == 1 and filters. Same request, one video of "
    "Alice and one of everybody."
)

PERSON_FILTER_ON_NON_PERSON_TYPE_DIVERGENCE = (
    "--person narrows any memory type on the CLI and no type but two in the "
    "wizard. fetch_videos_and_live_photos takes whatever person_ids generate.py "
    "resolved, so `--memory-type year_in_review --person Alice --person Bob` "
    "fetches only what holds both; the wizard renders a person widget for Person "
    "Spotlight and Multi-Person alone, and create_preset's PersonFilter -- which "
    "does carry the names for every type -- is discarded by "
    "_apply_preset_to_state. This is the stills-filter and union-vs-intersection "
    "family: the filter exists on one surface only."
)

FETCH_SCENARIOS = (
    FetchScenario(MemoryType.YEAR_IN_REVIEW, ()),
    FetchScenario(MemoryType.PERSON_SPOTLIGHT, (ALICE,)),
    FetchScenario(MemoryType.MULTI_PERSON, (ALICE, BOB)),
    FetchScenario(MemoryType.MULTI_PERSON, (ALICE,), MULTI_PERSON_OF_ONE_DIVERGENCE),
    FetchScenario(
        MemoryType.YEAR_IN_REVIEW, (ALICE, BOB), PERSON_FILTER_ON_NON_PERSON_TYPE_DIVERGENCE
    ),
)


def _fetch_params():
    return [
        pytest.param(
            scenario,
            marks=pytest.mark.xfail(reason=scenario.divergence, strict=True)
            if scenario.divergence
            else (),
            id=f"{scenario.memory_type}-{len(scenario.people)}p",
        )
        for scenario in FETCH_SCENARIOS
    ]


class TestFetchParity:
    """Same windows and same people, same queries against Immich."""

    @pytest.mark.parametrize("scenario", _fetch_params())
    @pytest.mark.parametrize("include_photos", [False, True], ids=["videos", "videos+photos"])
    def test_fetch_calls_match(
        self,
        scenario: FetchScenario,
        include_photos: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spec = replace(SPECS[scenario.memory_type], people=scenario.people)
        windows = create_preset(scenario.memory_type, **spec.as_preset_params()).date_ranges
        cli = cli_fetch_calls(windows, scenario.people, include_photos=include_photos)
        ui = ui_fetch_calls(
            scenario.memory_type,
            windows,
            scenario.people,
            include_photos=include_photos,
            monkeypatch=monkeypatch,
        )
        # Two silent surfaces would agree on nothing at all; the contract is
        # about queries that happen.
        assert cli, "the CLI asked Immich for nothing"
        assert cli == ui
