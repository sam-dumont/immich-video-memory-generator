"""A polished film states the account it was polished against as its thesis."""

from __future__ import annotations

from types import SimpleNamespace

from immich_memories.analysis.editorial_story_reading import PeriodStory
from immich_memories.analysis.editorial_thin_step import polish_the_draft

ACCOUNT = "A month of a motorsport day, two beach outings and quiet evenings at home."


class Thin:
    """# WHY: stands in for ThinPolish, whose model calls have their own tests; only the account matters here."""

    def __init__(self, catalogue):
        self.catalogue = catalogue

    def catalogue_of(self, story, moment_assets, *, drafted):
        return self.catalogue

    def polish(self, carriers, **_options):
        return carriers


def _polish(story, catalogue):
    records = {}
    source = SimpleNamespace(
        config=SimpleNamespace(editorial=SimpleNamespace()),
        audience="family",
        owner_required_asset_ids=(),
        intent=SimpleNamespace(subject="", voice_per_partition=False),
        case=SimpleNamespace(product="monthly_highlights", people=()),
        people=None,
        render_timing=None,
        audience_annotations={},
        assets={},
    )
    ports = SimpleNamespace(
        thin=Thin(catalogue),
        rules=SimpleNamespace(standing=None),
        judge=None,
        thumbnail_hash=None,
        scene_print=None,
        laya=None,
    )
    selection = SimpleNamespace(story=story, lines={}, episodes=[])
    polish_the_draft(
        source,
        ports,
        SimpleNamespace(units={}),
        SimpleNamespace(),
        selection,
        SimpleNamespace(moment_assets={}),
        None,
        [{"asset_id": "a1"}],
        SimpleNamespace(final_content_cap=60.0, cut_carriers=[]),
        contract="",
        record=lambda name, value: records.__setitem__(name, value),
    )
    return records


def _story(thesis=""):
    return PeriodStory(
        thesis=thesis, episodes=[], connections=[], priorities=[], uncertainties=[], audit={}
    )


def test_a_polished_film_without_a_synthesis_thesis_takes_the_account():
    story = _story()

    records = _polish(story, SimpleNamespace(thesis=ACCOUNT))

    assert story.thesis == ACCOUNT
    assert records["period-story"]["thesis"] == ACCOUNT


def test_a_film_the_polish_could_not_read_keeps_its_empty_thesis():
    story = _story()

    records = _polish(story, None)

    assert story.thesis == ""
    assert "period-story" not in records
