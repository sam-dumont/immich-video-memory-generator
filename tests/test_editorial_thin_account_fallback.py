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

MAY = DateRange(
    start=datetime(2020, 5, 1, tzinfo=UTC).date(), end=datetime(2020, 5, 31, tzinfo=UTC).date()
)


def whole_month(captured, bank):
    """The same capture, presented as the whole calendar month a film of it would ask for."""
    identity = EpisodeReadingIdentity(
        group_id="moving-episode", producer_key="producer-a", evidence_key="evidence-a"
    )
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


def backend_for(captured, reader):
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
    )


def model_reader(config):
    config.editorial.reader = "model"
    config.llm.model = "a-model"
    return config


def test_a_period_with_no_account_is_catalogued_from_the_run_s_own_readings(tmp_path):
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    model_reader(captured.config)
    reader = Reader()

    polish = backend_for(captured, reader)._thin_polish(captured)

    assert library_period_account(bank, "2020-05")
    assert polish["thin"].account == library_period_account(bank, "2020-05")
    # One episode: its own reading is the month's account, so nothing was asked.
    assert reader.prompts == []


def test_the_no_model_reader_leaves_the_period_uncatalogued(tmp_path):
    bank = tmp_path / "annotations.sqlite"
    captured = whole_month(source(tmp_path, seconds=60, pictures=3), bank)
    captured.config.editorial.reader = "rules"
    captured.config.llm.model = ""

    polish = backend_for(captured, Reader())._thin_polish(captured)

    assert polish == {}
    assert library_period_account(bank, "2020-05") == ""
