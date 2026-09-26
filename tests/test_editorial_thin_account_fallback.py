"""A run whose period has never been catalogued banks its account and gets the thin layer."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime

from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_runtime import EditorialRunContext
from immich_memories.analysis.editorial_runtime_backend import ProductionPostCardBackend
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from immich_memories.store.library_overviews import library_period_account
from immich_memories.timeperiod import DateRange
from tests.test_editorial_duration_planner_integration import source
from tests.test_library_catalogue import Reader

IDENTITY = EpisodeReadingIdentity(
    group_id="moving-episode", producer_key="producer-a", evidence_key="evidence-a"
)
MAY = DateRange(
    start=datetime(2020, 5, 1, tzinfo=UTC).date(), end=datetime(2020, 5, 31, tzinfo=UTC).date()
)


def whole_month(captured, bank, *, notable=()):
    """The same capture, presented as the whole calendar month a film of it would ask for."""
    identity = IDENTITY
    asset_ids = tuple(captured.assets)
    with closing(EpisodeReadingStore(bank)) as store:
        store.remember(
            [
                BankedEpisodeReading(
                    identity=identity,
                    full_asset_ids=asset_ids,
                    what_happened="A household packed up and moved across town.",
                    representatives=(
                        EpisodeRepresentative(asset_id=asset_ids[0], reason="the first box"),
                    ),
                    cull_decisions=(),
                    notable_moments=tuple(
                        EpisodeRepresentative(asset_id=asset_ids[n], reason=why)
                        for n, why in notable
                    ),
                )
            ]
        )
    return replace(
        captured,
        case=replace(captured.case, ranges=(MAY,)),
        store_path=bank,
        lineage={
            **captured.lineage,
            "episode_readings": [
                {
                    "group_id": identity.group_id,
                    "producer_key": identity.producer_key,
                    "evidence_key": identity.evidence_key,
                }
            ],
        },
    )


class BankedDemand:
    """# WHY: stands in for the on-demand episode reader, which has its own tests in
    test_episode_demand.py. Here the readings are already banked, which is what a second
    run of the same film sees."""

    def __init__(self, bank, identity):
        self.bank, self.identity = bank, identity
        self.asked: list[tuple[str, ...]] = []

    def unread_episodes(self, asset_ids):
        return {self.identity.group_id: tuple(asset_ids)}

    def readings_for(self, asset_ids):
        self.asked.append(tuple(asset_ids))
        with closing(EpisodeReadingStore(self.bank)) as store:
            return {
                reading.identity.group_id: reading
                for reading in store.readings_for((self.identity,)).values()
            }


def backend_for(captured, reader, demand=None):
    case = captured.case
    return ProductionPostCardBackend(
        config=captured.config,
        context=EditorialRunContext(
            case.key,
            case.label,
            case.product,
            case.ranges,
            case.target_seconds,
            captured.artifact_dir,
        ),
        people=adapt_editorial_people({}),
        thumbnail_cache=object(),
        store_path=captured.store_path,
        ports=EditorialRuntimePorts(catalogue_requester_factory=lambda _config: reader),
        episode_demand=demand,
    )


def model_reader(config):
    config.editorial.reader = "model"
    config.llm.model = "a-model"
    return config


def test_a_period_with_no_account_is_catalogued_when_the_draft_asks_for_it(tmp_path):
    """Nothing is read to set the layer up; the account arrives when the stories are known."""
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    model_reader(captured.config)
    reader = Reader()
    demand = BankedDemand(bank, IDENTITY)

    polish = backend_for(captured, reader, demand)._thin_polish(captured)

    assert library_period_account(bank, "2020-05") == ""
    account, records = polish["thin"].read_period({"S1": tuple(captured.assets)})
    assert demand.asked == [tuple(captured.assets)]

    assert account and account == library_period_account(bank, "2020-05")
    assert records == {}
    # One episode: its own reading is the month's account, so nothing was asked.
    assert reader.prompts == []


def test_a_short_film_reads_more_through_the_demand_and_gets_its_records(tmp_path):
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(
        source(tmp_path, seconds=60, pictures=3), bank, notable=[(1, "the first box packed")]
    )
    model_reader(captured.config)
    demand = BankedDemand(bank, IDENTITY)

    short = backend_for(captured, Reader(), demand)._thin_polish(captured)["thin"].short
    assets = tuple(captured.assets)

    assert short.unread(assets) == {IDENTITY.group_id: assets}
    assert short.records(assets[1:]) == {assets[1]: "the first box packed"}
    assert demand.asked == [assets[1:]]
    assert short.standing(assets[0]) in {0, 1, 2}


def test_a_short_film_whose_reading_fails_reads_no_records(tmp_path):
    class Failing(BankedDemand):
        def readings_for(self, asset_ids):
            raise RuntimeError("the reader is down")

    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    model_reader(captured.config)

    backend = backend_for(captured, Reader(), Failing(bank, IDENTITY))
    short = backend._thin_polish(captured)["thin"].short

    assert short.records(tuple(captured.assets)) == {}


def test_the_no_model_reader_leaves_the_period_uncatalogued(tmp_path):
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    captured.config.editorial.reader = "rules"
    captured.config.llm.model = ""

    polish = backend_for(captured, Reader())._thin_polish(captured)

    assert polish == {}
    assert library_period_account(bank, "2020-05") == ""


def test_separate_date_windows_keep_the_same_on_demand_refinement_route(tmp_path):
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    later = DateRange(
        datetime(2021, 5, 1, tzinfo=UTC).date(), datetime(2021, 5, 2, tzinfo=UTC).date()
    )
    captured = replace(captured, case=replace(captured.case, ranges=(MAY, later)))
    model_reader(captured.config)
    demand = BankedDemand(bank, IDENTITY)

    polish = backend_for(captured, Reader(), demand)._thin_polish(captured)

    assert demand.asked == []
    account, _ = polish["thin"].read_period({"S1": tuple(captured.assets)})
    assert account
    assert demand.asked == [tuple(captured.assets)]


def test_an_account_over_more_of_the_period_replaces_a_cut_s_partial_one(tmp_path):
    """`prepare --overviews` reads everything later; the fuller account is what a film reads."""
    from immich_memories.store.library_catalogue import CatalogueStore, LibraryAccount

    bank = tmp_path / "annotations.sqlite"
    with closing(CatalogueStore(bank)) as store:
        store.remember(
            [
                LibraryAccount("partial", "month", "2020-05", "the stories one cut told", ("e1",)),
                LibraryAccount("whole", "month", "2020-05", "the whole month", ("e1", "e2", "e3")),
            ]
        )

    assert library_period_account(bank, "2020-05") == "the whole month"


def test_a_year_with_no_account_is_catalogued_when_the_draft_asks_for_it(tmp_path):
    """A year's account sits over its months: a year cut writes both from what it read."""
    bank = tmp_path / "annotations.sqlite"
    month = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    year = DateRange(
        start=datetime(2020, 1, 1, tzinfo=UTC).date(), end=datetime(2020, 12, 31, tzinfo=UTC).date()
    )
    captured = replace(month, case=replace(month.case, ranges=(year,)))
    model_reader(captured.config)
    period = next(iter(captured.assets.values())).file_created_at.strftime("%Y")

    polish = backend_for(captured, Reader(), BankedDemand(bank, IDENTITY))._thin_polish(captured)
    account, _records = polish["thin"].read_period({"S1": tuple(captured.assets)})

    assert period == "2020"
    assert account and account == library_period_account(bank, "2020")


def test_the_episodes_nobody_read_reach_the_account_as_their_facts(tmp_path):
    """The draft's own cards say what the rest of the month was, and cost nothing to pass on."""
    from immich_memories.analysis.editorial_structure_contract import EpisodeReadingCard

    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    quiet = EpisodeReadingCard(
        episode_id="quiet-episode",
        evidence_key="facts",
        what_happened="walking at the park",
        representative_asset_ids=(tuple(captured.assets)[-1],),
        cache_hit=False,
    )
    captured = replace(captured, episode_readings={**captured.episode_readings, "M999": quiet})
    model_reader(captured.config)
    reader = Reader()

    polish = backend_for(captured, reader, BankedDemand(bank, IDENTITY))._thin_polish(captured)
    account, _records = polish["thin"].read_period({"S1": tuple(captured.assets)})

    assert account
    assert len(reader.prompts) == 1
    assert "walking at the park" in reader.prompts[0]


def test_a_person_film_over_twenty_years_is_polished_over_one_account_a_year(tmp_path):
    """A birth-date-to-today window reads on demand and asks no account per month."""
    import sqlite3

    from immich_memories.analysis.editorial_structure_contract import EpisodeReadingCard
    from immich_memories.analysis.editorial_thin_layer import catalogued_period
    from tests.conftest import make_asset

    bank = tmp_path / "annotations.sqlite"
    month = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    lifetime = DateRange(
        start=datetime(2005, 12, 3, tzinfo=UTC), end=datetime(2026, 9, 23, 23, 59, tzinfo=UTC)
    )
    assets, cards = dict(month.assets), dict(month.episode_readings)
    years = range(2006, 2026)
    for year in years:
        for season in (3, 9):
            asset = make_asset(
                f"quiet-{year}-{season}", file_created_at=datetime(year, season, 1, tzinfo=UTC)
            )
            assets[asset.id] = asset
            cards[f"Q{year}{season}"] = EpisodeReadingCard(
                episode_id=f"quiet-{year}-{season}",
                evidence_key="facts",
                what_happened=f"an ordinary day in {year}",
                representative_asset_ids=(asset.id,),
                cache_hit=False,
            )
    captured = replace(
        month,
        case=replace(month.case, ranges=(lifetime,)),
        assets=assets,
        episode_readings=cards,
    )
    model_reader(captured.config)
    reader, demand = Reader(), BankedDemand(bank, IDENTITY)

    period = catalogued_period(captured.case.ranges)
    polish = backend_for(captured, reader, demand)._thin_polish(captured)
    account, _records = polish["thin"].read_period({"S1": tuple(month.assets)})

    assert demand.asked == [tuple(month.assets)]
    assert account and account == library_period_account(bank, period)
    assert len(reader.prompts) <= len(years) + 1
    with closing(sqlite3.connect(bank)) as connection:
        kinds = {kind for (kind,) in connection.execute("SELECT kind FROM library_overviews")}
    assert not kinds & {"month", "month-part"}


def test_a_season_is_polished_over_one_account_of_its_own_window(tmp_path):
    """A spring film reads one account for March to May, not a year's and a window's."""
    import sqlite3

    from immich_memories.analysis.editorial_structure_contract import EpisodeReadingCard
    from immich_memories.analysis.editorial_thin_layer import catalogued_period
    from tests.conftest import make_asset

    bank = tmp_path / "annotations.sqlite"
    month = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    spring = DateRange(
        start=datetime(2020, 3, 1, tzinfo=UTC), end=datetime(2020, 5, 31, 23, 59, tzinfo=UTC)
    )
    march = make_asset("a-march-walk", file_created_at=datetime(2020, 3, 14, tzinfo=UTC))
    quiet = EpisodeReadingCard(
        episode_id="a-march-walk",
        evidence_key="facts",
        what_happened="a walk in the rain in March",
        representative_asset_ids=(march.id,),
        cache_hit=False,
    )
    captured = replace(
        month,
        case=replace(month.case, ranges=(spring,)),
        assets={**month.assets, march.id: march},
        episode_readings={**month.episode_readings, "M900": quiet},
    )
    model_reader(captured.config)
    reader = Reader()

    period = catalogued_period(captured.case.ranges)
    polish = backend_for(captured, reader, BankedDemand(bank, IDENTITY))._thin_polish(captured)
    account, _records = polish["thin"].read_period({"S1": tuple(month.assets)})

    assert period == "2020-03-01..2020-05-31"
    assert account and account == library_period_account(bank, period)
    assert len(reader.prompts) == 1
    with closing(sqlite3.connect(bank)) as connection:
        nodes = connection.execute("SELECT kind, period FROM library_overviews").fetchall()
    assert ("span", period) in nodes
    assert not {kind for kind, _period in nodes} & {"year", "year-part", "month", "month-part"}


def test_a_period_read_that_fails_once_is_asked_again(tmp_path):
    """One dropped call must not ship the draft without its polish."""

    class FlakyOnce(BankedDemand):
        def readings_for(self, asset_ids):
            if not self.asked:
                self.asked.append(("dropped",))
                raise RuntimeError("the reader dropped the call")
            return super().readings_for(asset_ids)

    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    model_reader(captured.config)

    polish = backend_for(captured, Reader(), FlakyOnce(bank, IDENTITY))._thin_polish(captured)
    account, _records = polish["thin"].read_period({"S1": tuple(captured.assets)})

    assert account and account == library_period_account(bank, "2020-05")
