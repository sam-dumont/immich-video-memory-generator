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
moves. Differences nobody decided on are recorded as strict ``xfail`` carrying
the reason, so repairing one forces its record to be deleted in the same commit
-- a fix cannot land while the file still claims the surfaces disagree.

No xfail is outstanding. A new divergence is recorded as one -- ``FetchScenario``
carries the reason and ``_fetch_params`` marks it strict -- and an xfail that
needs a product decision rather than a repair says so in its first line.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime

import pytest

from immich_memories.api.models import Person
from immich_memories.cli._asset_fetch import fetch_photos, fetch_videos
from immich_memories.cli._date_resolution import default_duration_for_type, resolve_date_range
from immich_memories.config import Config
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
    day: date | None = None
    window: tuple[datetime, datetime] | None = None
    title: str | None = None
    active_hours: float | None = None

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
            "location_name": self.location_name,
            **self.as_cli_preset_params(),
        }
        return {key: value for key, value in params.items() if value is not None}

    def as_cli_preset_params(self) -> dict:
        """What ``generate`` forwards to the preset factory, beside the flags.

        Two memory types have inputs the CLI cannot spell as date flags, and
        both are discovered rather than typed: a special day's window and title
        come out of the catalogue that ``--day`` names, and a trip's dates come
        out of GPS detection. ``--start``/``--end`` mean something else on a
        trip -- they pick which detected trip to render -- so the trip's own
        span travels as preset parameters on both surfaces alike.
        """
        discovered = {
            "day": self.day,
            "window": self.window,
            "title": self.title,
            "active_hours": self.active_hours,
            "trip_start": self.trip_start,
            "trip_end": self.trip_end,
        }
        return {key: value for key, value in discovered.items() if value is not None}


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
    # An invented day, as the docs' fixtures name them. The catalogue that
    # feeds this on both surfaces names real people and places.
    MemoryType.SPECIAL_DAY: MemorySpec(
        day=date(2016, 6, 12),
        window=(datetime(2016, 6, 12, 10, 20), datetime(2016, 6, 12, 19, 50)),
        title="An afternoon at the track",
        active_hours=12.0,
    ),
}

# An album brings its own assets, so neither surface resolves a window for it:
# --from-album on the CLI, the Album card in the wizard, and both take the span
# from what the album holds. Registering a preset for it must fail this file.
ALBUM_HAS_NO_PRESET = MemoryType.ALBUM

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
        "special_day",
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


# ── Differences this file deliberately does not assert ────────────────────────
#
# The three below came out of #680's first run as findings rather than failures:
# nothing here compares them, so each is written down with the reason it is not
# drift. A finding that turns out to be drift becomes an xfail above, not a
# longer comment.
#
# "All Time" has no CLI counterpart. The wizard's year pickers offer it and
# _apply_preset_to_state answers it directly, without create_preset, because no
# preset covers "every year you own" -- there is no window to build from a year
# that was never chosen. On the CLI the same memory is --start/--end, which is
# the manual path, not a memory type. Nothing to reconcile: the surfaces differ
# because one has an affordance the other spells out.
#
# Each memory type computes its length its own way -- the span curve, the trip
# and special-day editorial curves, a flat preset constant. That is three
# formulas but not a surface divergence: what this file owns is that both
# surfaces get the *same* answer per type, which TestTargetDurationParity
# asserts, and DOCUMENTED_DURATION_SPLIT pins where the project chose otherwise.
# Whether one curve should serve every type is a selection question, not a
# parity one.
#
# A trip is not narrowed to a person on either surface. handle_trip_generation
# fetches the trip's window with no person ids, and the wizard's Trip card is
# the one card #666 left without a person picker, so both take the window
# whole. That is deliberate rather than left over: on the CLI --person scopes
# trip *detection* -- which people's GPS trail is scanned -- and the trip that
# comes back is then rendered entire. A picker on the wizard's Trip card would
# narrow its fetch and nothing else's, which is how a divergence gets made
# while another is being closed. Widening it means changing what a trip
# selects on both surfaces at once, which is a selection change with its own
# contact sheets, not this file's business.


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
        preset_params=spec.as_cli_preset_params() or None,
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
    return default_duration_for_type(
        str(memory_type), resolved, spec.as_cli_preset_params() or None
    )


def ui_windows(memory_type: MemoryType, spec: MemorySpec) -> list[tuple[datetime, datetime]]:
    """The windows the wizard writes into ``state.date_ranges`` for this spec.

    Mirrors step1_presets._apply_preset_to_state, the UI's only call into
    create_preset, which copies preset.date_ranges verbatim.
    """
    return _windows(create_preset(memory_type, **spec.as_preset_params()).date_ranges)


def ui_duration(memory_type: MemoryType, spec: MemorySpec) -> float | None:
    """The target length the wizard puts in the duration box for this spec."""
    return create_preset(memory_type, **spec.as_preset_params()).default_duration_seconds


def _every_type_with_a_spec():
    """Every type in SPECS, each as its own case.

    No window or duration divergence is outstanding. A new one is recorded the
    way the fetch scenarios record theirs -- a strict xfail carrying the reason
    -- so that repairing it forces the record to be deleted.
    """
    return [pytest.param(memory_type, id=str(memory_type)) for memory_type in SPECS]


class TestRegistryCoverage:
    """A new memory type cannot ship without declaring what parity means for it."""

    def test_every_memory_type_has_parity_data(self) -> None:
        declared = set(SPECS) | {ALBUM_HAS_NO_PRESET}
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

    @pytest.mark.parametrize("memory_type", _every_type_with_a_spec())
    def test_windows_match(self, memory_type: MemoryType) -> None:
        spec = SPECS[memory_type]
        assert cli_windows(memory_type, spec) == ui_windows(memory_type, spec)

    def test_a_holiday_with_no_year_given_skips_the_one_that_has_not_happened(self) -> None:
        """Both surfaces default the year, so both must apply the same guard.

        Asking for Christmas in August with no year spends one of the requested
        years on a window no photo can fall in. The CLI refused to; the preset
        the wizard calls had no way to know the year had been defaulted rather
        than chosen, so it did not. SPECS pins an explicit year for every type,
        which is the case where the two agreed all along.

        Vacuous between Christmas and New Year, when there is no unhappened
        Christmas left to skip. build_holiday's own guard is pinned against a
        fixed ``today`` in tests/test_holiday_memory.py.
        """
        spec = MemorySpec(holiday="christmas", years_back=5)

        assert cli_windows(MemoryType.HOLIDAY, spec) == ui_windows(MemoryType.HOLIDAY, spec)


class TestTargetDurationParity:
    """Same spec, same default length -- or the split #630 wrote down."""

    @pytest.mark.parametrize("memory_type", _every_type_with_a_spec())
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
    """What ``cli/_asset_fetch`` asks Immich for, given windows and people."""
    client = RecordingClient()
    person_ids = [person.id for person in people]
    fetch_videos(
        client=client,
        # Read only on the live-photo branch, which use_live_photos closes.
        progress=SilentProgress(),
        date_ranges=windows,
        person_ids=person_ids,
    )
    if include_photos:
        fetch_photos(client=client, date_ranges=windows, person_ids=person_ids)
    return client.calls


def _wizard_state(
    memory_type: MemoryType, windows: list[DateRange], people: tuple[Person, ...]
) -> AppState:
    """The state the wizard leaves behind once a card has been filled in.

    Every card ends at ``AppState.apply_preset``, which is where the preset's
    windows, length and person filter become state -- so this builds the state
    the same way ``step1_presets._apply_preset_to_state`` does rather than
    imitating the widgets. ``people`` is the roster Immich returned, which is
    what a filter's names resolve against.
    """
    spec = replace(SPECS[memory_type], people=people)
    state = AppState()
    # The wizard always has a config by the time it fetches: step 1 sets it.
    # Without it the fetch cannot read the burst merge window, and the two
    # surfaces would differ over a fixture gap rather than a real divergence.
    state.config = Config()
    state.people = list(people)
    state.apply_preset(create_preset(memory_type, **spec.as_preset_params()))
    assert _windows(state.date_ranges) == _windows(windows), (
        "apply_preset disagreed with the windows the fetch was handed"
    )
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


# A person filter is not a two-card feature. #666 ruled that the wizard offers
# one wherever the CLI's --person reaches, and that several names mean the same
# thing on both surfaces: an intersection, the way a multi-person memory has
# always meant "both on the picture". So a Year in Review narrowed to Alice and
# Bob is a request both surfaces can now phrase, and both answer identically.
FETCH_SCENARIOS = (
    FetchScenario(MemoryType.YEAR_IN_REVIEW, ()),
    FetchScenario(MemoryType.PERSON_SPOTLIGHT, (ALICE,)),
    FetchScenario(MemoryType.MULTI_PERSON, (ALICE, BOB)),
    FetchScenario(MemoryType.MULTI_PERSON, (ALICE,)),
    FetchScenario(MemoryType.YEAR_IN_REVIEW, (ALICE, BOB)),
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
