"""A film for the thin layer to polish, and a judge that answers and counts its every call."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.editorial_standing_facts import carries_nothing
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate
from immich_memories.analysis.editorial_thin_catalogue import BankedCatalogue, ThinStory
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from immich_memories.config_models_llm import LLMConfig

JUNK = "an empty worktop"
DOUBTFUL = "a plain corridor"
# The frame head reads this shot as an accidental frame, so the standing facts refuse it.
UNSTEADY = "a blurred wall"
PRIVATE = "a child in the bath"
START = datetime(2024, 1, 1, 9, 0)


def thin_budget(draft: int, seats: int) -> int:
    # fit and audience, each in its two orders, per twelve shots; four per seat
    return 4 * math.ceil(draft / 12) + 4 * seats


class CountingJudge:
    """Answers every question the thin layer asks, and counts each request it is sent."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        self.failures: list[str] = []

    def record_failure(self, stage, _record) -> None:
        self.failures.append(stage)

    def ask(self, stage, prompt, max_tokens=260, **_options):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
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


def frame_heads(line: str) -> dict[str, str]:
    """What the frame head reads for a fixture shot."""
    return {"frame_kind": "accidental_or_blurred_frame"} if UNSTEADY in line else {}


def _labels(prompt: str, marker: str) -> list[str]:
    return re.findall(rf"^(P\d+): .*{marker}", prompt, re.MULTILINE)


def _audience(prompt: str) -> str:
    groups = re.findall(r"^(G\d+): (.*)$", prompt, re.MULTILINE)
    if groups:
        return json.dumps({label: _finding(text) for label, text in groups})
    return json.dumps(_finding(prompt))


def _finding(text: str) -> dict[str, str]:
    return {"finding": "bathing" if PRIVATE in text else "none", "why": "what the caption says"}


class Film:
    """A draft of shots, each in a story, with the pictures each story could offer a seat."""

    def __init__(self) -> None:
        self.lines: dict[str, str] = {}
        self.units: dict[str, dict] = {}
        self.pool: dict[str, list[dict]] = {}
        self.tiers: dict[str, str] = {}
        self.draft: list[dict] = []
        self.records: dict[str, str] = {}

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
        return BankedCatalogue(
            thesis="A year a family grew.", stories=stories, hints={}, records=self.records
        )


def polish(tmp_path, film: Film, *, audience_batch: int = 12, short=None, room: float = 120.0):
    judge = CountingJudge()
    recorded: dict = {}
    standing = StandingGate(
        lambda asset: (
            0 if carries_nothing(frame_heads(film.lines[asset]), film.lines[asset]) else 2
        ),
        line_of=film.lines.get,
        life=lambda _asset: True,
        unit_by_asset={asset: ("fam", row) for asset, row in film.units.items()},
        pictures_of={key: len(rows) for key, rows in film.pool.items()},
    )
    audience = AudienceGate(
        judge,
        audience="family",
        annotations={},
        flag_rows={},
        lines=film.lines,
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience-verdicts.private.json", answerer="full|model-a"),
    )
    drafted = {row["asset_id"] for row in film.draft}
    cut = ThinPolish(bank_dir=tmp_path, short=short).polish(
        film.draft,
        judge=judge,
        gates=ThinGates(
            standing=standing,
            audience=audience,
            thumbnail_hash=lambda _a: None,
            audience_batch=audience_batch,
        ),
        catalogue=film.catalogue(),
        contract="contract",
        line_of=film.lines.get,
        record=lambda name, payload: recorded.__setitem__(name, payload),
        candidates_of=lambda key: film.pool.get(key, []),
        content_cap=sum(row["seconds"] for row in film.draft) + room,
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
