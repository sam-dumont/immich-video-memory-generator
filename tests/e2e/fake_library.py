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
    # A filler picture borrows one of the six photographs; the six use their own file.
    source_id: str | None = None

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def source(self) -> Path:
        return LIBRARY_DIR / f"{self.source_id or self.asset_id}.jpg"


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


def _filler_month() -> tuple[Picture, ...]:
    """A second month wide enough to page: one clip and forty-one stills over May 2024.

    The media pool shows twenty pictures per page, so a month of forty-two proves the
    page never grows past that. None of these belongs to a story; the scripted
    editor only knows June.
    """
    sources = tuple(picture.asset_id for picture in LIBRARY if not picture.is_video)
    pictures = [
        Picture(
            asset_id="may-walk",
            filename="IMG_2001.mp4",
            kind="video",
            taken_at="2024-05-01T10:00:00.000Z",
            caption="a slow walk down the lane",
            source_id="woods-path",
        )
    ]
    for index in range(41):
        day, hour = divmod(index, 2)
        pictures.append(
            Picture(
                asset_id=f"may-{index + 1:02d}",
                filename=f"IMG_{2002 + index}.jpg",
                kind="still",
                taken_at=f"2024-05-{day + 2:02d}T{9 + hour * 6:02d}:00:00.000Z",
                caption=f"filler picture {index + 1}",
                source_id=sources[index % len(sources)],
            )
        )
    return tuple(pictures)


BIG_MONTH: tuple[Picture, ...] = _filler_month()

# Everything the fake Immich serves: the June story month and the wide May filler month.
ALL_PICTURES: tuple[Picture, ...] = LIBRARY + BIG_MONTH

BY_ID: dict[str, Picture] = {picture.asset_id: picture for picture in LIBRARY}

STORY_OF: dict[str, Story] = {
    asset_id: story for story in STORIES for asset_id in story.picture_ids
}
