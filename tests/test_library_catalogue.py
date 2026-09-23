"""Cataloguing writes the account of a period; a film reads it and never writes one."""

from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime

import pytest

from immich_memories.analysis.catalogue_runtime import (
    catalogue_banked_episodes,
    catalogue_requester,
)
from immich_memories.analysis.library_catalogue import (
    LibraryEpisode,
    bank_month_accounts,
    build_catalogue,
)
from immich_memories.config_loader import Config
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from immich_memories.store.library_catalogue import CatalogueStore, LibraryAccount
from immich_memories.store.library_overviews import library_period_account

PRODUCER = "producer-key-a"
DATES = {
    "a1": datetime(2024, 2, 3, 10, 0, tzinfo=UTC),
    "a2": datetime(2024, 2, 3, 10, 5, tzinfo=UTC),
    "b1": datetime(2024, 2, 14, 10, 0, tzinfo=UTC),
    "c1": datetime(2024, 3, 2, 10, 0, tzinfo=UTC),
}


class Reader:
    """Answers every offered key, and keeps what it was asked."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        offered = prompt.split("Return an account for EACH of these exact keys: ")[1]
        keys = [key.strip() for key in offered.split(".\n")[0].split(",")]
        return json.dumps({"accounts": {key: f"what {key} was about" for key in keys}})


def reading(group: str, assets: tuple[str, ...], *, evidence: str = "v1") -> BankedEpisodeReading:
    return BankedEpisodeReading(
        identity=EpisodeReadingIdentity(
            group_id=group, producer_key=PRODUCER, evidence_key=f"{group}-{evidence}"
        ),
        full_asset_ids=assets,
        what_happened=f"what happened in {group}",
        representatives=(EpisodeRepresentative(asset_id=assets[0], reason="it carries the day"),),
        cull_decisions=(),
    )


def episode(group: str, assets: tuple[str, ...], *, evidence: str = "v1") -> LibraryEpisode:
    return LibraryEpisode(
        reading=reading(group, assets, evidence=evidence), taken_at=DATES[assets[0]]
    )


FEBRUARY = (episode("e1", ("a1", "a2")), episode("e2", ("b1",)))


def _two_episodes():
    """Two canonical episodes of February, with complete annotation lines for both."""
    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        SourceScope,
        prepare_editorial_source,
    )
    from immich_memories.analysis.selection_source_groups import project_episode_groups
    from tests.conftest import make_asset
    from tests.test_text_episode_reader import _AnnotationLines

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset, file_created_at=DATES[asset]) for asset in ("a1", "b1")
            )
        ),
    )
    lines = _AnnotationLines({asset: f"{asset} | one complete line" for asset in ("a1", "b1")})
    return prepared, project_episode_groups(prepared, prepared.candidate_ids), lines


def catalogued(bank, events, asked, *, producer: str = "model-a") -> dict:
    with closing(CatalogueStore(bank)) as store:
        return bank_month_accounts(events, store=store, requester=asked, producer=producer)


def config_for(tmp_path, *, reader: str = "model") -> Config:
    config = Config()
    config.cache.directory = str(tmp_path)
    config.editorial.reader = reader
    config.llm.model = "a-model" if reader == "model" else ""
    return config


def banked_readings(bank, events) -> None:
    with closing(EpisodeReadingStore(bank)) as store:
        store.remember([event.reading for event in events])


def test_a_month_account_is_written_where_a_film_reads_it(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"

    months = catalogued(bank, FEBRUARY, Reader())

    assert set(months) == {"2024-02"}
    assert library_period_account(bank, "2024-02") == months["2024-02"].account
    assert library_period_account(bank, "2024-03") == ""


def test_the_same_readings_are_never_paid_for_twice(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    cold, warm = Reader(), Reader()

    catalogued(bank, FEBRUARY, cold)
    catalogued(bank, FEBRUARY, warm)

    assert len(cold.prompts) == 1
    assert warm.prompts == []


def test_one_changed_reading_reopens_its_own_month_and_no_other(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    march = (episode("e9", ("c1",)),)
    catalogued(bank, (*FEBRUARY, *march), Reader())
    again = Reader()

    catalogued(bank, (FEBRUARY[0], episode("e2", ("b1",), evidence="v2"), *march), again)

    assert len(again.prompts) == 1
    assert "2024-02" in again.prompts[0]
    assert "2024-03" not in again.prompts[0]


def test_a_producer_change_is_a_new_account(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    catalogued(bank, FEBRUARY, Reader(), producer="model-a")
    other = Reader()

    catalogued(bank, FEBRUARY, other, producer="model-b")

    assert len(other.prompts) == 1


def test_a_year_is_written_over_its_months_without_a_second_reading(tmp_path) -> None:
    asked = Reader()

    with closing(CatalogueStore(tmp_path / "annotations.sqlite")) as store:
        catalogue = build_catalogue(FEBRUARY, store=store, requester=asked, producer="model-a")

    assert set(catalogue.months) == {"2024-02"}
    assert set(catalogue.years) == {"2024"}
    # One month over two episodes is one request; a year holding one month is copied, not re-read.
    assert len(asked.prompts) == 1


def test_a_run_banks_the_account_of_readings_it_has_already_paid_for(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    banked_readings(bank, FEBRUARY)
    asked = Reader()

    catalogue_banked_episodes(
        [event.reading.identity for event in FEBRUARY],
        store_path=bank,
        capture_dates=DATES,
        config=config_for(tmp_path),
        requester=asked,
    )

    assert len(asked.prompts) == 1
    assert library_period_account(bank, "2024-02")


def test_an_episode_this_run_cannot_place_is_left_out_of_the_account(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    banked_readings(bank, FEBRUARY)
    asked = Reader()

    catalogue_banked_episodes(
        [event.reading.identity for event in FEBRUARY],
        store_path=bank,
        # "a2" is absent, so its episode has no capture date this run can vouch for.
        capture_dates={"b1": DATES["b1"]},
        config=config_for(tmp_path),
        requester=asked,
    )

    # One placeable episode: the month copies that episode's own reading, and asks nothing.
    assert asked.prompts == []
    assert library_period_account(bank, "2024-02") == "what happened in e2"


def test_the_no_model_reader_writes_no_account(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    banked_readings(bank, FEBRUARY)
    rules = config_for(tmp_path, reader="rules")

    with pytest.raises(ValueError, match="model reader"):
        catalogue_requester(rules)
    with pytest.raises(ValueError, match="model reader"):
        catalogue_banked_episodes(
            [event.reading.identity for event in FEBRUARY],
            store_path=bank,
            capture_dates=DATES,
            config=rules,
        )

    assert library_period_account(bank, "2024-02") == ""


class OneEpisodeRefuses:
    """A reader that answers every episode but one, and counts what it was asked."""

    def __init__(self, refuses: str) -> None:
        self.refuses = refuses
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        aliases = re.findall(r"^episode (\d+)$", prompt, re.MULTILINE)
        if "Return an account for EACH" in prompt:
            return Reader()(prompt)
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": int(alias),
                        "what_happened": "Something happened.",
                        "representatives": [{"asset": 1, "reason": "it carries the day"}],
                        "cull": [],
                    }
                    for alias in aliases
                    if self.refuses not in _assets_of(prompt, alias)
                ],
            }
        )


def _assets_of(prompt: str, alias: str) -> str:
    block = prompt.split(f"episode {alias}\n")[-1].split("\n\nepisode ")[0]
    return block


def test_an_episode_the_reader_cannot_read_is_asked_once_across_two_runs(tmp_path) -> None:
    """And the month is still written, from the episodes that did read."""
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
    from immich_memories.store.episode_readings import EpisodeReadingProducer

    bank = tmp_path / "annotations.sqlite"
    producer = EpisodeReadingProducer(
        model_id="a-model",
        prompt_version="episode-prompt-v3",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    prepared, projections, lines = _two_episodes()
    asked = OneEpisodeRefuses(refuses="b1")

    for _run in range(2):
        with closing(EpisodeReadingStore(bank)) as store:
            result = CachedTextEpisodeReader(
                store=store, producer=producer, annotations=lines, requester=asked
            ).read(projections)

    readable = [e.reading for e in result.episodes if e.reading is not None]
    # Three rounds on the first run, and nothing at all on the second.
    assert len(asked.prompts) == 3
    assert len(readable) == 1

    with closing(CatalogueStore(bank)) as store:
        bank_month_accounts(
            [
                LibraryEpisode(reading=reading, taken_at=DATES[reading.full_asset_ids[0]])
                for reading in readable
            ],
            store=store,
            requester=Reader(),
            producer="model-a",
        )

    assert library_period_account(bank, "2024-02")


def test_a_cut_s_account_reads_the_facts_of_the_episodes_it_did_not_read(tmp_path) -> None:
    """The rest of the month is told from what the no-model reader already knows, for free."""
    bank = tmp_path / "annotations.sqlite"
    asked = Reader()
    facts = LibraryAccount("facts-e2", "episode-facts", "2024-02", "a quiet day in the park", ())

    with closing(CatalogueStore(bank)) as store:
        months = bank_month_accounts(
            FEBRUARY[:1], store=store, requester=asked, producer="model-a", unread_facts=(facts,)
        )

    assert len(asked.prompts) == 1
    assert "a quiet day in the park" in asked.prompts[0]
    # The account indexes what was read; the facts shape it without joining its children.
    assert months["2024-02"].children == (FEBRUARY[0].key,)


def test_a_reading_of_the_whole_month_replaces_a_cut_s_account_over_facts(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    facts = LibraryAccount("facts-e2", "episode-facts", "2024-02", "a quiet day in the park", ())
    with closing(CatalogueStore(bank)) as store:
        bank_month_accounts(
            FEBRUARY[:1], store=store, requester=Reader(), producer="model-a", unread_facts=(facts,)
        )

    whole = catalogued(bank, FEBRUARY, Reader())

    assert library_period_account(bank, "2024-02") == whole["2024-02"].account
