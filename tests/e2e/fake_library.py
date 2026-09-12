"""The synthetic family library behind the screenshots and the demo.

Six CC0 stock photographs live next door in ``fixtures/library`` -- see its
CREDITS.md -- and this file says what each one shows, when it was taken, and
which of three stories it belongs to. The caption and the picture are declared
in the same place on purpose: the story view puts them side by side, and a
reader who does not believe the pair does not believe the product.

Nothing here is anyone's real library. The pictures are public-domain stock,
and none of them shows a recognisable face.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

LIBRARY_DIR = Path(__file__).parent / "fixtures" / "library"


@dataclass(frozen=True, slots=True)
class Picture:
    """One synthetic capture: the file it comes from, and the line written about it."""

    asset_id: str
    filename: str
    kind: str
    taken_at: str
    caption: str
    is_favorite: bool = False

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def source(self) -> Path:
        return LIBRARY_DIR / f"{self.asset_id}.jpg"


@dataclass(frozen=True, slots=True)
class Story:
    """One weighed story, and the pictures the fixture editor hung on it."""

    key: str
    title: str
    weight: str
    role: str
    purpose: str
    picture_ids: tuple[str, ...]


THESIS = (
    "A month that opens at a table in the garden, spends one long day in the "
    "woods in the middle of it, and ends with two nights camped above a lake."
)

# Capture order. The three stories below take them two at a time, so the day
# order and the story order agree -- as they do in a real month.
LIBRARY: tuple[Picture, ...] = (
    Picture(
        asset_id="garden-table",
        filename="IMG_2418.mp4",
        kind="video",
        taken_at="2024-06-08T12:15:00.000Z",
        caption="the table and chairs still out on the lawn",
        is_favorite=True,
    ),
    Picture(
        asset_id="garden-cake",
        filename="IMG_2437.jpg",
        kind="still",
        taken_at="2024-06-09T16:30:00.000Z",
        caption="the cake with the candles still in it",
    ),
    Picture(
        asset_id="woods-hamper",
        filename="IMG_2690.jpg",
        kind="still",
        taken_at="2024-06-14T11:05:00.000Z",
        caption="the hamper open on the checked cloth",
        is_favorite=True,
    ),
    Picture(
        asset_id="woods-path",
        filename="IMG_2712.mp4",
        kind="video",
        taken_at="2024-06-15T15:40:00.000Z",
        caption="the long green path back to the car",
    ),
    Picture(
        asset_id="lake-tents",
        filename="IMG_2901.mp4",
        kind="video",
        taken_at="2024-06-21T18:45:00.000Z",
        caption="the tents pitched on the slope above the lake",
    ),
    Picture(
        asset_id="lake-sunset",
        filename="IMG_2933.jpg",
        kind="still",
        taken_at="2024-06-22T21:10:00.000Z",
        caption="the last of the sun going down over the water",
    ),
)

STORIES: tuple[Story, ...] = (
    Story(
        key="S0001",
        title="Lunch in the garden",
        weight="dominant",
        role="central",
        purpose="Opens the month where most of it actually happened",
        picture_ids=("garden-table", "garden-cake"),
    ),
    Story(
        key="S0002",
        title="The day in the woods",
        weight="major",
        role="central",
        purpose="Carries the middle of the month, the one day spent away from the house",
        picture_ids=("woods-hamper", "woods-path"),
    ),
    Story(
        key="S0003",
        title="Two nights by the lake",
        weight="glimpse",
        role="texture",
        purpose="Closes the month on the weekend everyone slept outside",
        picture_ids=("lake-tents", "lake-sunset"),
    ),
)

BY_ID: dict[str, Picture] = {picture.asset_id: picture for picture in LIBRARY}

STORY_OF: dict[str, Story] = {
    asset_id: story for story in STORIES for asset_id in story.picture_ids
}
