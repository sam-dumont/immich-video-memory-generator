"""Read capture facts into stories, using the existing story weight floors."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from operator import itemgetter
from statistics import median
from typing import Any

import numpy as np

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

    def _stories(self, episodes, hints):
        by_key = {e.key: e for e in episodes}
        stories = []
        for index, keys in enumerate(consecutive_runs(by_key, lambda key: hints[key]["day"]), 1):
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
        if self.source.intent.product == "album":
            return 2
        return self._visual_standing(heads, known_people=bool(asset.people))

    @staticmethod
    def _visual_standing(heads: dict[str, str], *, known_people: bool) -> int:
        if heads.get("people") == "none":
            if heads.get("activity") == "other" and heads.get("venue") == "home":
                return 0
            if heads.get("location") == "indoor":
                return 1
        if known_people or heads.get("people", "undetermined") not in {"none", "undetermined"}:
            return 2
        if heads.get("activity", "other") != "other" or heads.get("venue") in {
            "nature",
            "urban",
            "event-venue",
            "sports",
            "water",
        }:
            return 2
        return 1
