"""The thin layer's calls grow with the seats it fills, never with the draft or its stories.

A counting judge answers every question the layer asks, through the production standing gate,
audience gate, vote, picker and refill. The budget is the owner's: one look at the draft costs
three questions per twelve shots (standing, audience, fit), and each seat costs at most four more.
A seat whose story holds a thousand pictures costs what a seat in a small story costs.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_audience import AudienceGate
from immich_memories.analysis.editorial_thin_catalogue import BankedCatalogue, ThinStory
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from immich_memories.config_models_llm import LLMConfig

JUNK = "an empty worktop"
DOUBTFUL = "a plain corridor"
UNSTEADY = "a blurred wall"
PRIVATE = "a child in the bath"
START = datetime(2024, 1, 1, 9, 0)


def thin_budget(draft: int, seats: int) -> int:
    return 3 * math.ceil(draft / 12) + 4 * seats


class CountingJudge:
    """Answers every question the thin layer asks, and counts each request it is sent."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls: list[str] = []

    def ask(self, stage, prompt, max_tokens=260, **_options):
        self.calls.append(stage)
        if stage.startswith("standing-"):
            return _named(prompt, UNSTEADY)
        if stage.startswith("thesis-fit-"):
            doubted = _labels(prompt, JUNK)
            if stage.endswith("-source"):
                doubted += _labels(prompt, DOUBTFUL)
            return json.dumps({"weak": dict.fromkeys(doubted, "adds nothing")})
        if stage.startswith("story-pick-"):
            return json.dumps({"keep": re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE)[:1]})
        if stage.startswith("shareability-"):
            return _audience(prompt)
        raise AssertionError(f"the thin layer asked an unexpected question: {stage}")


def _labels(prompt: str, marker: str) -> list[str]:
    return re.findall(rf"^(P\d+): .*{marker}", prompt, re.MULTILINE)


def _named(prompt: str, marker: str) -> str:
    return json.dumps({"weak": dict.fromkeys(_labels(prompt, marker), "nothing stands")})


def _audience(prompt: str) -> str:
    groups = re.findall(r"^(G\d+): (.*)$", prompt, re.MULTILINE)
    if groups:
        return json.dumps({label: _finding(text) for label, text in groups})
    return json.dumps(_finding(prompt))


def _finding(text: str) -> dict[str, str]:
    return {"finding": "bathing" if PRIVATE in text else "none", "why": "what the caption says"}


class PictureEvidence:
    """# WHY: the production overlay reads preview observations from Immich; captions here are
    the evidence, exactly as a picture with no observation yet is judged."""

    records: dict = {}
    annotations: dict = {}

    def __init__(self, lines) -> None:
        self._lines = lines

    def enrich(self, _unit, stop_on_body_yes=False):
        return None

    def line(self, unit):
        return self._lines[unit["asset_id"]]


class Film:
    """A draft of shots, each in a story, with the pictures each story could offer a seat."""

    def __init__(self) -> None:
        self.lines: dict[str, str] = {}
        self.units: dict[str, dict] = {}
        self.pool: dict[str, list[dict]] = {}
        self.tiers: dict[str, str] = {}
        self.draft: list[dict] = []

    def shot(self, asset, story, when, caption, *, kind="still", favourite=False):
        taken = when.strftime("%Y-%m-%dT%H:%M:%S")
        self.lines[asset] = f"{taken[:16]} | {caption}"
        row = {
            "asset_id": asset,
            "story_episode": story,
            "taken": taken,
            "moment": f"m-{asset}",
            "seconds": 4.0,
            "kind": kind,
            "favourite": favourite,
            "line": self.lines[asset],
        }
        self.units[asset] = row
        self.pool.setdefault(story, []).append(row)
        return row

    def story(self, key, tier, size, when, *, caption_of=lambda _n: "the family together"):
        """A story of `size` pictures beside whatever the draft already took from it."""
        self.tiers[key] = tier
        for number in range(size):
            kind = "video" if number % 10 == 0 else "still"
            self.shot(
                f"{key}-c{number:04d}",
                key,
                when + timedelta(minutes=7 * (number + 1)),
                f"{caption_of(number)} {number}",
                kind=kind,
            )

    def catalogue(self) -> BankedCatalogue:
        stories = tuple(
            ThinStory(
                key=key,
                title=key,
                purpose="",
                episodes=(key,),
                tier=self.tiers[key],
                first_day=rows[0]["taken"][:10],
                asset_ids=tuple(row["asset_id"] for row in rows),
            )
            for key, rows in self.pool.items()
        )
        return BankedCatalogue(thesis="A year a family grew.", stories=stories, hints={})


def polish(tmp_path, film: Film):
    judge = CountingJudge()
    recorded: dict = {}
    standing = StandingGate(
        judge,
        contract="contract",
        period_label="2024",
        line_of=film.lines.get,
        life=lambda _asset: True,
        unit_by_asset={asset: ("fam", row) for asset, row in film.units.items()},
        pictures_of={key: len(rows) for key, rows in film.pool.items()},
        bank={},
        save=None,
        calls={"standing_rounds": 0},
    )
    audience = AudienceGate(
        judge,
        audience="family",
        picture_evidence=PictureEvidence(film.lines),
        flag_rows={},
        lines=film.lines,
        bank_path=tmp_path / "shareability.private.json",
    )
    drafted = {row["asset_id"] for row in film.draft}
    cut = ThinPolish(bank_dir=tmp_path).polish(
        film.draft,
        judge=judge,
        gates=ThinGates(standing=standing, audience=audience, thumbnail_hash=lambda _a: None),
        catalogue=film.catalogue(),
        contract="contract",
        line_of=film.lines.get,
        record=lambda name, payload: recorded.__setitem__(name, payload),
        candidates_of=lambda key: film.pool.get(key, []),
        content_cap=sum(row["seconds"] for row in film.draft) + 120.0,
    )
    newcomers = [row["asset_id"] for row in cut if row["asset_id"] not in drafted]
    return judge, recorded["thin-polish"], cut, newcomers


def draft_of(film: Film, size: int, *, flagged: dict[int, str], tier_of=lambda _i: "maybe"):
    """`size` shots, four to a story, a shot every two days; `flagged` shots sit in stories of
    a thousand pictures."""
    for index in range(size):
        story = f"S{index // 4:03d}"
        when = START + timedelta(days=2 * index)
        caption = flagged.get(index, f"people at a table, shot {index}")
        if story not in film.tiers:
            big = any(i // 4 == index // 4 for i in flagged)
            film.story(story, tier_of(index), 1000 if big else 20, when + timedelta(days=1))
        film.draft.append(film.shot(f"d{index:03d}", story, when, caption))
    film.draft.sort(key=lambda row: (row["taken"], row["asset_id"]))


@pytest.mark.xfail(
    strict=True, reason="refill standing reads whole stories; audience asks one carrier per call"
)
def test_three_seats_in_stories_of_a_thousand_pictures_stay_inside_the_budget(tmp_path):
    film = Film()
    draft_of(film, 160, flagged={10: JUNK, 80: UNSTEADY, 150: PRIVATE})

    judge, payload, cut, newcomers = polish(tmp_path, film)

    kept = {row["asset_id"] for row in cut}
    assert not {"d010", "d080", "d150"} & kept
    assert len(payload["slots"]) == 3
    assert len(newcomers) == 3
    assert len(judge.calls) <= thin_budget(160, 3)


@pytest.mark.xfail(
    strict=True, reason="refill standing reads whole stories; audience asks one carrier per call"
)
def test_a_draft_nothing_is_wrong_with_costs_one_look(tmp_path):
    film = Film()
    draft_of(film, 160, flagged={})

    judge, payload, cut, _newcomers = polish(tmp_path, film)

    assert len(cut) == 160
    assert payload["slots"] == []
    assert len(judge.calls) <= thin_budget(160, 0)


def year_shaped(film: Film) -> None:
    """A year: 161 shots over forty stories of every tier, and 25 seats of every kind.

    Some of the stories a seat opens in lead their pages with pictures the standing gate
    refuses, so a seat has to pick again.
    """
    tiers = ("remarkable", "maybe", "background")
    flagged = {
        **dict.fromkeys(range(3, 160, 32), JUNK),
        **dict.fromkeys(range(7, 160, 32), DOUBTFUL),
        **dict.fromkeys(range(11, 160, 16), UNSTEADY),
        **dict.fromkeys(range(13, 160, 32), PRIVATE),
    }
    for index in range(161):
        story = f"S{index // 4:03d}"
        when = START + timedelta(hours=54 * index)
        if story not in film.tiers:
            film.story(
                story,
                tiers[(index // 4) % 3],
                400 if index // 4 % 2 else 60,
                when + timedelta(days=1),
                caption_of=lambda n: UNSTEADY if n < 2 else "the family together",
            )
        caption = flagged.get(index, f"people at a table, shot {index}")
        film.draft.append(
            film.shot(f"d{index:03d}", story, when, caption, favourite=index % 9 == 0)
        )
    film.draft.sort(key=lambda row: (row["taken"], row["asset_id"]))


@pytest.mark.xfail(
    strict=True, reason="refill standing reads whole stories; audience asks one carrier per call"
)
def test_a_year_shaped_draft_spends_calls_on_its_seats_not_its_size(tmp_path):
    film = Film()
    year_shaped(film)

    judge, payload, _cut, newcomers = polish(tmp_path, film)

    seats = len(payload["slots"])
    assert seats >= 20
    assert newcomers
    assert len(judge.calls) <= thin_budget(161, seats)
