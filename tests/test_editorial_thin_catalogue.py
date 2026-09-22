"""The catalogued period a thin polish reads before it looks at any film."""

from __future__ import annotations

from immich_memories.analysis.editorial_thin_catalogue import (
    BankedCatalogue,
    ThinCatalogue,
    banked_catalogue,
)

ROWS = [
    {
        "key": "S001",
        "title": "First",
        "purpose": "why",
        "episodes": ["e1", "e2"],
        "gate": "remarkable",
        "first_day": "2024-02-01",
    },
    {
        "key": "S002",
        "title": "Second",
        "purpose": "",
        "episodes": ["e3"],
        "gate": "maybe",
        "first_day": "2024-02-09",
    },
]
ASSETS = {"S001": ["a1", "a2"], "S002": ["b1"]}


def _catalogue(**overrides):
    arguments = {
        "account": "What this month was about.",
        "story_rows": ROWS,
        "hints": {"e1": {"day": "2024-02-01", "pictures": 4}},
        "asset_ids_of": ASSETS,
    }
    return banked_catalogue(**(arguments | overrides))


def test_a_library_with_no_account_of_the_period_has_no_catalogue():
    assert _catalogue(account="") is None
    assert _catalogue(account="   \n ") is None


def test_the_catalogue_carries_the_account_its_stories_and_their_hints():
    catalogue = _catalogue()
    assert catalogue is not None
    assert isinstance(catalogue, ThinCatalogue)
    assert catalogue.thesis == "What this month was about."
    assert [story.key for story in catalogue.stories] == ["S001", "S002"]
    first = catalogue.stories[0]
    assert first.episodes == ("e1", "e2")
    assert first.asset_ids == ("a1", "a2")
    assert first.tier == "remarkable"
    assert catalogue.hints["e1"]["pictures"] == 4


def test_a_story_the_period_holds_no_pictures_of_is_not_catalogued():
    catalogue = _catalogue(asset_ids_of={"S001": ["a1"]})
    assert catalogue is not None
    assert [story.key for story in catalogue.stories] == ["S001"]
    assert _catalogue(asset_ids_of={}) is None


def test_an_unknown_worthiness_word_reads_as_background():
    catalogue = _catalogue(story_rows=[dict(ROWS[0], gate="")])
    assert catalogue is not None
    assert catalogue.stories[0].tier == "background"


def test_a_catalogue_can_be_supplied_whole_by_a_caller_that_has_one():
    """The layer depends on the protocol, never on how a catalogue was assembled."""
    catalogue = BankedCatalogue(thesis="T", stories=(), hints={"e1": {"day": "2024-02-01"}})
    assert isinstance(catalogue, ThinCatalogue)
    assert catalogue.thesis == "T"
