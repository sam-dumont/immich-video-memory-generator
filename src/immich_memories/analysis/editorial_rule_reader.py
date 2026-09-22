"""Read capture facts into stories, using the existing story weight floors."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from operator import itemgetter
from statistics import median
from typing import Any

import numpy as np

from immich_memories.analysis.editorial_home_radius import home_of, near_home_of
from immich_memories.analysis.editorial_preparation_picture_facts import picture_facts_on
from immich_memories.analysis.editorial_rule_episodes import RULES_VERSION
from immich_memories.analysis.editorial_story_reading import (
    PeriodStory,
    StoryEpisode,
    _same_episode_day,
)
from immich_memories.analysis.editorial_story_replies import WEIGHT_ROLE, relations_on
from immich_memories.analysis.editorial_story_weighing import (
    _FAMILY_WORD,
    _floor_weights,
    consecutive_runs,
)

# The place labels of the shipped head bundle the standing rule reads. A picture is in a
# private or utility interior, or in a public place; every other venue label says nothing.
PRIVATE_VENUES = frozenset({"bedroom", "medical", "private_facility"})
PUBLIC_VENUES = frozenset({"water"})
OUTDOOR_LOCATION = "outdoor"


def _calendar_week(day: str) -> tuple[int, int] | tuple[()]:
    """The ISO (year, week) of a day episode; empty when the episode has no dated unit."""
    try:
        return date.fromisoformat(day[:10]).isocalendar()[:2]
    except ValueError:
        return ()


class NoModelJudge:
    """Guard the inference boundary; rules never answer serialized prompts."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ask(self, *args, **kwargs) -> str:
        raise RuntimeError("rules reader reached a model-only decision")

    def record_failure(self, stage: str, record: Mapping[str, Any]) -> None:
        """Rules answer nothing, so nothing can fail to parse; keep any claim visible anyway."""
        self.calls.append({"stage": stage, "failure": dict(record)})


class RuleStructureReader:
    def __init__(self, source) -> None:
        self.source = source

    def worthiness(self, wall, near_home):
        assets = self.source.assets
        days = Counter(a.file_created_at.date() for a in assets.values())
        masses = list(days.values())
        threshold = max(4 * median(masses), float(np.percentile(masses, 75)))
        cities = Counter(self._city(a) for a in assets.values() if self._city(a))
        usual = {city for city, _ in cities.most_common(12)}
        required = self._required_families(wall)
        votes = {
            family: self._vote(
                [assets[a] for a in wall.event_assets[family]],
                days=days,
                threshold=threshold,
                usual=usual,
                away=near_home(family) is False,
                required=family in required,
            )
            for family in wall.fam_ids
        }
        return {f: v[0] for f, v in votes.items()}, {f: v[1] for f, v in votes.items()}

    def _required_families(self, wall) -> set[str]:
        families: dict[str, set[str]] = {}
        for family in wall.fam_ids:
            for asset_id in wall.event_assets[family]:
                day = self.source.assets[asset_id].file_created_at.date()
                part = self.source.intent.partition_for(day)
                if part is not None and part.required:
                    families.setdefault(part.key, set()).add(family)
        return {next(iter(values)) for values in families.values() if len(values) == 1}

    def _vote(self, members, *, days, threshold, usual, away, required):
        if self.source.intent.product == "album":
            return 0, "Owner chose this album as the memory's source"
        if any(days[a.file_created_at.date()] >= threshold for a in members):
            return 0, "Capture count at least four times the median photographed day"
        city = self._dominant_city(members)
        if away or city and city not in usual:
            return 0, "Outside home radius or the twelve usual cities"
        return self._indicator(members, required=required)

    def _indicator(self, members, *, required):
        if any(a.is_favorite for a in members):
            return 1, "Owner favourite present"
        relations = (
            rel for a in members for rel in relations_on(self.source.annotations.get(a.id, ""))
        )
        if any(_FAMILY_WORD.search(rel) for rel in relations):
            return 1, "Close family recorded in people metadata"
        if any(a.is_video for a in members):
            return 1, "Recorded video present"
        if required:
            return 1, "Only happening in a required partition"
        return 2, "No occasion indicator in available facts"

    def _dominant_city(self, members) -> str:
        cities = Counter(self._city(a) for a in members if self._city(a))
        return cities.most_common(1)[0][0] if cities else ""

    @staticmethod
    def _city(asset) -> str:
        return (asset.exif_info.city or "") if asset.exif_info else ""

    def _title(self, members) -> str:
        cities = Counter(self._city(a) for a in members if self._city(a))
        city = cities.most_common(1)[0][0] if cities else ""
        activities = Counter(
            label
            for a in members
            if (record := self.source.audience_annotations.get(a.id))
            for head, label in record.heads
            if head == "activity" and label != "other"
        )
        activity = activities.most_common(1)[0][0] if activities else ""
        return (
            f"{activity} at {city}"
            if activity and city
            else city or activity or f"{members[0].file_created_at:%Y-%m-%d}"
        )

    def _day_chunks(self):
        groups = []
        for moment, ids in self.source.moment_asset_ids.items():
            members = sorted((self.source.assets[a] for a in ids), key=lambda a: a.file_created_at)
            groups.append((members[0].file_created_at.isoformat(), moment, members))
        groups.sort(key=itemgetter(0, 1))
        chunks: list[list] = []
        for row in groups:
            if chunks:
                first, last = chunks[-1][0], chunks[-1][-1]
                gap = (
                    datetime.fromisoformat(row[0]) - datetime.fromisoformat(last[0])
                ).total_seconds()
                changed = self._city(row[2][0]) != self._city(last[2][0])
                joins = _same_episode_day(row[0], first[0], last[0]) and not (
                    gap > 5400 and changed
                )
            else:
                joins = False
            if joins:
                chunks[-1].append(row)
            else:
                chunks.append([row])
        return chunks

    def _day_episodes(self, evidence):
        episodes = []
        for index, chunk in enumerate(self._day_chunks(), 1):
            members = [a for row in chunk for a in row[2]]
            moments = [row[1] for row in chunk]
            covered = set(moments)
            observations = [
                o
                for row in evidence
                if covered.intersection(row["moments"])
                for o in row.get("observations", ())
            ]
            account = " ".join("; ".join(observations).split()[:60])
            episodes.append(
                StoryEpisode(
                    f"R{index:03}",
                    self._title(members),
                    account,
                    "",
                    "supporting",
                    "",
                    moments=moments,
                )
            )
        return episodes

    def _away_from_home(self, episode) -> bool | None:
        points = [
            self.source.gps.get(asset_id)
            for moment in episode.moments
            for asset_id in self.source.moment_asset_ids.get(moment, ())
        ]
        near = near_home_of(home_of(self.source.config.trips), points)
        return None if near is None else not near

    def _runs(self, episodes, hints) -> list[list[str]]:
        """Consecutive photographed days, cut into stories a film can spend a grant on.

        A stretch away from home stays whole however long it lasts, because a trip is one
        story, and a day at home ends it: two trips either side of a week at home are two
        stories, not one. A run at home is cut on the calendar week; without that, a
        densely photographed year merges into a single 129-day story whose grant is spent
        on its first week and whose remaining months never come into view. A day whose
        pictures say nothing about where they were does not end a trip.
        """
        away_of = {e.key: self._away_from_home(e) for e in episodes}
        runs: list[list[str]] = []
        for keys in consecutive_runs({e.key: e for e in episodes}, lambda key: hints[key]["day"]):
            chunks: dict[tuple, list[str]] = {}
            dated = next((w for k in keys if (w := _calendar_week(hints[k]["day"]))), ())
            away, was_away, trips = False, False, 0
            for key in keys:
                known = away_of[key]
                if known is not None:
                    away = known
                trips += away and not was_away
                was_away = away
                week = _calendar_week(hints[key]["day"]) or dated
                chunks.setdefault((True, trips) if away else (False, week), []).append(key)
            runs.extend(chunks.values())
        return runs

    def _stories(self, episodes, hints):
        by_key = {e.key: e for e in episodes}
        stories = []
        for index, keys in enumerate(self._runs(episodes, hints), 1):
            relations: Counter[str] = Counter()
            for key in keys:
                relations.update(hints[key].get("relations", {}))
            gate = min(
                (hints[k].get("gate", "background") for k in keys),
                key=("remarkable", "maybe", "background").index,
            )
            stories.append(
                {
                    "key": f"S{index:03}",
                    "episodes": keys,
                    "title": " / ".join(dict.fromkeys(by_key[k].title for k in keys)),
                    "purpose": "Capture dates, places and owner favourites",
                    "weight": "",
                    "gate": gate,
                    "people_counts": dict(relations),
                    "seen": {
                        field: sum(hints[k].get(field, 0) for k in keys)
                        for field in ("moments", "pictures", "favourites")
                    },
                }
            )
        return stories

    def read_story(self, _judge, *, evidence, enrich, record, **kwargs) -> PeriodStory:
        episodes = self._day_episodes(evidence)
        hints = enrich(episodes)
        stories = self._stories(episodes, hints)
        by_key = {e.key: e for e in episodes}
        floors = _floor_weights(stories, journey=False)
        for story in stories:
            for key in story["episodes"]:
                by_key[key].role = WEIGHT_ROLE[story["weight"]]
        result = PeriodStory(
            "",
            episodes,
            [],
            [],
            [],
            {"producer": RULES_VERSION, "hints": hints, "floors": floors},
            stories,
        )
        record(result.as_record())
        return result

    def standing(self, asset_id: str) -> int:
        asset = self.source.assets[asset_id]
        if asset.is_favorite:
            return 2
        record = self.source.audience_annotations.get(asset_id)
        heads = dict(record.heads) if record else {}
        line = self.source.annotations.get(asset_id, "")
        if (
            heads.get("doc_docling", "photograph") != "photograph"
            or heads.get("nsfw_marqo") == "yes"
        ):
            return 0
        if any(marker in line for marker in ("SOFT (blurry)", "DARK", "BLOWN OUT")):
            return 0
        if nothing_to_show(line):
            return 0
        if self.source.intent.product == "album":
            return 2
        return self._visual_standing(heads, known_people=bool(asset.people))

    @staticmethod
    def _visual_standing(heads: dict[str, str], *, known_people: bool) -> int:
        """Nobody, nothing happening and a private or utility interior does not stand on its
        own; people, an activity, or an outdoor or public place does.

        Every label here is one the shipped head bundle can produce. The rule this replaces
        asked for `venue == "home"` and four place labels no head has ever emitted, so two of
        its branches were dead and it could not answer 0 from the heads at all.
        """
        if heads.get("people") == "none":
            if heads.get("activity", "other") == "other" and heads.get("venue") in PRIVATE_VENUES:
                return 0
            if heads.get("location") == "indoor":
                return 1
        if known_people or heads.get("people", "undetermined") not in {"none", "undetermined"}:
            return 2
        if (
            heads.get("activity", "other") != "other"
            or heads.get("location") == OUTDOOR_LOCATION
            or heads.get("venue") in PUBLIC_VENUES
        ):
            return 2
        return 1


# Below this the reader is saying the picture carries nothing, and it has to agree with
# itself: a low number alone refuses real moments, and the label alone refuses a ceiling
# somebody meant to photograph.
_NOT_WORTH = 0.10
_NOTHING_KINDS = frozenset(
    {
        "empty_room_ceiling_or_floor",
        "accidental_or_blurred_frame",
        "lone_everyday_object",
        "body_part_closeup",
    }
)


def nothing_to_show(line: str) -> bool:
    """The optional picture reader says this frame carries nothing. Silent without its row."""
    facts = picture_facts_on(line)
    worth = facts.get("worth")
    return isinstance(worth, float) and worth < _NOT_WORTH and facts.get("what") in _NOTHING_KINDS
