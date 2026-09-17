"""A captured film of separate days, some of them away from home, for the story planner tests.

Every day is one episode and every moment its own capture group. Places are fictional: home
sits at one made-up coordinate, the trips several hundred kilometres away from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_case import Case, _adapt_production_cards
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_moment_wall import (
    MomentCardEvidence,
    ProductionMomentWallRenderer,
    RepresentativeEvidence,
)
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_structure_contract import (
    EpisodeReadingCard,
    StructurePlanningInput,
)
from immich_memories.analysis.moment_cards import MomentCard
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.config_loader import Config
from immich_memories.config_models_automation import TripsConfig
from immich_memories.timeperiod import DateRange
from tests.test_editorial_story_first_planner import StoryJudge

HOME = (45.0, 5.0, "Hometown", "Homeland")
SEASIDE = (43.0, 9.0, "Seaside", "Farland")
HILLS = (47.0, 1.0, "Hilltown", "Farland")


@dataclass(frozen=True)
class Day:
    """One photographed day: where, what the reader will call it, and how many moments."""

    day: date
    activity: str
    where: tuple[float, float, str, str] | None = HOME
    moments: int = 1


def home_days(start: date, count: int, *, step: int = 1, activity: str = "Home day") -> list[Day]:
    return [Day(start + timedelta(days=step * n), f"{activity} {n + 1}") for n in range(count)]


def trip_days(start: date, count: int, *, where=SEASIDE, moments: int = 2) -> list[Day]:
    return [
        Day(start + timedelta(days=n), f"Day {n + 1} by the sea", where, moments)
        for n in range(count)
    ]


def _asset(asset_id: str, taken: datetime, where) -> Asset:
    exif = (
        ExifInfo(latitude=where[0], longitude=where[1], city=where[2], country=where[3])
        if where
        else None
    )
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=taken,
        fileModifiedAt=taken,
        updatedAt=taken,
        originalFileName=f"{asset_id}.jpg",
        exifInfo=exif,
    )


def _candidate(asset: Asset) -> EditorialCandidate:
    return EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=False,
        source=asset,
        proposed_segment=None,
        shippable_duration=0,
        grounded_annotations=(),
    )


def film_source(
    tmp_path,
    days: list[Day],
    *,
    seconds: float,
    span: tuple[date, date],
    product: str = "monthly_highlights",
    home_base: bool = True,
    pictures: int = 1,
) -> StructurePlanningInput:
    """A film over `span` holding `days`; each moment has `pictures` pictures."""
    groups, episodes, cards, candidates, annotations = [], [], [], [], {}
    for day_index, spec in enumerate(days):
        day_candidates = []
        day_groups = []
        for moment in range(spec.moments):
            local = []
            for picture in range(pictures):
                taken = datetime.combine(spec.day, datetime.min.time(), UTC) + timedelta(
                    hours=9 + 2 * moment, minutes=7 * picture
                )
                asset = _asset(f"d{day_index:03d}-m{moment}-p{picture}", taken, spec.where)
                description = f"A clothed person during {spec.activity.lower()}, moment {moment} view {picture}."
                place = f" | at {spec.where[2]}, {spec.where[3]}" if spec.where else ""
                annotations[asset.id] = AssetAnnotationLine(
                    asset.id,
                    f"{taken.isoformat()} | {description}{place} | activity=playing",
                    description=description,
                    heads=(("nsfw_marqo", "no"),),
                )
                local.append(_candidate(asset))
            group = EditorialGroup(f"day{day_index:03d}-moment{moment}", tuple(local))
            day_groups.append(group)
            day_candidates.extend(local)
        episode = EditorialGroup(f"day{day_index:03d}", tuple(day_candidates))
        episodes.append(episode)
        for group in day_groups:
            groups.append(group)
            cards.append(
                MomentCard(
                    moment_id=group.group_id,
                    episode_id=episode.group_id,
                    full_asset_ids=group.candidate_ids,
                    selectable_asset_ids=group.candidate_ids,
                    representative_asset_ids=group.candidate_ids[:1],
                    text=spec.activity,
                    evidence=MomentCardEvidence(
                        episode_meaning=spec.activity,
                        representatives=(RepresentativeEvidence(spec.activity, "Shows the day."),),
                        annotations=(("activity", "playing"),),
                    ),
                )
            )
        candidates.extend(day_candidates)
    prepared = SimpleNamespace(
        moment_groups=tuple(groups),
        episode_groups=tuple(episodes),
        candidates=tuple(candidates),
    )
    adapted, _ = _adapt_production_cards(prepared, tuple(cards))
    wall = ProductionMomentWallRenderer(prepared, tuple(cards), adapt_editorial_people({})).render(
        adapted
    )
    case = Case(
        "film",
        "A film of separate days",
        product,
        (
            DateRange(
                datetime.combine(span[0], datetime.min.time(), UTC),
                datetime.combine(span[1], datetime.max.time(), UTC),
            ),
        ),
        seconds,
        "Show the period through its days.",
    )
    config = Config()
    if home_base:
        config = config.model_copy(
            update={"trips": TripsConfig(homebase_latitude=HOME[0], homebase_longitude=HOME[1])}
        )
    return StructurePlanningInput(
        case=case,
        intent=build_editorial_intent(case.product, case.ranges, brief=case.brief),
        config=config,
        wall_bytes=wall.text.encode(),
        moment_asset_ids={
            alias: group.candidate_ids for alias, group in zip(wall.aliases, groups, strict=True)
        },
        assets={c.asset_id: c.source for c in candidates},
        annotations={key: row.text for key, row in annotations.items()},
        audience_annotations=annotations,
        gps={
            c.asset_id: (c.source.exif_info.latitude, c.source.exif_info.longitude)
            for c in candidates
            if c.source.exif_info
        },
        pixel_facts={},
        shareability_flags={},
        motion_residuals={},
        lineage={},
        bank_dir=tmp_path / "banks",
        artifact_dir=tmp_path / "plan",
        episode_readings={
            alias: EpisodeReadingCard(
                episode_id=card.episode_id,
                evidence_key=f"evidence-{card.episode_id}",
                what_happened=card.evidence.episode_meaning,
                representative_asset_ids=card.representative_asset_ids,
                cache_hit=False,
            )
            for alias, card in zip(wall.aliases, cards, strict=True)
        },
    )


def _json_after(prompt: str, marker: str):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())[0]


class FilmJudge(StoryJudge):
    """Every day is its own episode and story, titled as the reader saw it.

    `weigh(row)` names each story's weight from its weighing row.
    """

    def __init__(self, *args, weigh=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.weigh = weigh or (lambda row: "major" if "| trip:" in row else "minor")

    def answer(self, stage, prompt):
        if stage.startswith("story-episodes"):
            rows = json.loads(prompt.rstrip().rsplit("\n", 1)[1])
            keys = [f"S{index + 1:04d}" for index in range(len(rows))]
            return json.dumps(
                {
                    "fragments": [
                        {"reading": row["reading"], "episode": key}
                        for row, key in zip(rows, keys, strict=True)
                    ],
                    "new_episodes": [
                        {
                            "id": key,
                            "title": row["what_happened"],
                            "account": row["what_happened"],
                            "role": "supporting",
                        }
                        for row, key in zip(rows, keys, strict=True)
                    ],
                }
            )
        if stage.startswith("story-understanding"):
            cards = _json_after(prompt, "remarkable, maybe or background)\n")
            return json.dumps(
                {
                    "thesis": "A period of separate days.",
                    "about": [],
                    "stories": [
                        {"title": c["title"], "episodes": [c["episode"]], "purpose": c["title"]}
                        for c in cards
                    ],
                    "uncertainties": [],
                }
            )
        if stage.startswith("story-weighing"):
            rows = re.findall(r"^K\d{2} \|.*$", prompt, re.MULTILINE)
            return json.dumps(
                {
                    "about": [],
                    "weights": {row.split(" |", 1)[0]: self.weigh(row) for row in rows},
                    "join": [],
                    "retitle": {},
                }
            )
        return super().answer(stage, prompt)


def with_favourites(source: StructurePlanningInput, starred: set[str]) -> StructurePlanningInput:
    return replace(
        source,
        assets={
            key: asset.model_copy(update={"is_favorite": key in starred})
            for key, asset in source.assets.items()
        },
    )
