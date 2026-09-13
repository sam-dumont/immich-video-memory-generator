"""The synthetic household library behind the screenshots and the demo.

One month, June 2024, told by public-domain stock photographs that live next
door in ``fixtures/library`` (see its CREDITS.md): ordinary days at home in
Brussels, a birthday in the garden, a Saturday in the woods, a week camped by a
lake in the Alps, and the return. This file says what each picture shows, when
it was taken, where, who was there, and which story the scripted editor hung it
on -- or why it left it out. Caption and picture are declared together on
purpose: the story view puts them side by side, and a reader who does not
believe the pair does not believe the product.

Nothing here is anyone's real library. The home is a public landmark, the
people are a made-up cast, and the pictures are CC0 stock chosen so that no
recognisable face appears in any of them (tests/test_scaling_utilities.py
leans on that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

LIBRARY_DIR = Path(__file__).parent / "fixtures" / "library"


@dataclass(frozen=True, slots=True)
class Place:
    latitude: float
    longitude: float
    city: str
    country: str


# The fixture home is a public landmark, never a real address (owner's rule).
HOME = Place(50.8417, 4.3624, "Brussels", "Belgium")
WOODS = Place(50.7806, 4.4200, "Tervuren", "Belgium")
ROAD = Place(47.3220, 5.0415, "Dijon", "France")
LAKE = Place(45.8992, 6.1294, "Annecy", "France")
TALLOIRES = Place(45.8410, 6.2120, "Talloires", "France")
SEMNOZ = Place(45.7920, 6.1010, "Semnoz", "France")
MENTHON = Place(45.8600, 6.2000, "Menthon-Saint-Bernard", "France")

# A made-up household. The names are the ones the fake Immich's people endpoint
# returns; the people page fixture keeps its own roster.
CAST: tuple[str, ...] = ("Robin", "Charlie", "Kit")
ADULTS = ("Robin", "Charlie")
EVERYONE = CAST


@dataclass(frozen=True, slots=True)
class Story:
    """One weighed story, and the pictures the fixture editor hung on it."""

    key: str
    title: str
    weight: str
    role: str
    purpose: str


STORIES: tuple[Story, ...] = (
    Story(
        key="S0001",
        title="A birthday in the garden",
        weight="dominant",
        role="central",
        purpose="The one afternoon the whole month was arranged around",
    ),
    Story(
        key="S0002",
        title="A week by the lake",
        weight="major",
        role="central",
        purpose="Seven days away, the only time the month leaves the city",
    ),
    Story(
        key="S0003",
        title="The Saturday in the woods",
        weight="minor",
        role="supporting",
        purpose="One day out between the party and the holiday",
    ),
    Story(
        key="S0004",
        title="Ordinary days",
        weight="glimpse",
        role="texture",
        purpose="What the rest of the month looked like at home",
    ),
)

STORY_BY_KEY: dict[str, Story] = {story.key: story for story in STORIES}

THESIS = (
    "A month that turns around one birthday afternoon in the garden, spends a "
    "Saturday in the woods, and leaves the city for a week camped above a lake "
    "before pancakes on the first morning back."
)


@dataclass(frozen=True, slots=True)
class Scene:
    """One moment the household photographed, and how the editor read it.

    ``key`` is the file prefix under ``fixtures/library``: ``<key>.jpg`` for the
    six originals, ``<key>-01.jpg`` … for the fetched sets. The first file of a
    scene is its main shot; the later files are the near-duplicates a phone
    always holds, and the editor leaves them out with a reason of their own.
    """

    key: str
    day: int
    time: str
    caption: str
    place: Place = HOME
    story: str | None = None
    drop: str | None = None
    people: tuple[str, ...] = ()
    video: bool = False
    favorite: bool = False
    seconds: float = 4.0


# Capture order. A scene with a story ships its main shot; every other file the
# scene holds is a near-duplicate the editor drops.
SCENES: tuple[Scene, ...] = (
    # Week one: home.
    Scene(
        "home-breakfast",
        1,
        "08:15",
        "the first coffee of the month at the kitchen table",
        story="S0004",
        people=ADULTS,
    ),
    Scene(
        "home-evening-sky",
        1,
        "21:30",
        "the sky over the roofs after dinner",
        drop="a sky, and nothing of the family under it",
    ),
    Scene(
        "home-park-pond",
        2,
        "10:00",
        "the ducks at the park pond",
        drop="the pond, with nobody of ours at it",
    ),
    Scene(
        "home-football-lawn",
        2,
        "16:00",
        "the ball left on the lawn",
        drop="the ball on the grass, the game already over",
        people=("Kit",),
    ),
    Scene(
        "home-rain-window",
        3,
        "07:40",
        "rain on the kitchen window, the month's first grey morning",
        story="S0004",
    ),
    Scene(
        "home-kitchen",
        3,
        "18:00",
        "the pan on the stove",
        drop="a pan on a stove, nothing to say about it",
    ),
    Scene(
        "home-desk-laptop",
        4,
        "12:30",
        "the laptop on the desk",
        drop="a laptop on a desk: work, not the household",
    ),
    Scene(
        "home-dog-walk",
        4,
        "17:45",
        "the dog pulling towards the park",
        story="S0004",
        people=("Charlie",),
        video=True,
        seconds=5.0,
    ),
    Scene(
        "home-cat-window",
        5,
        "08:00",
        "the cat asleep in the window",
        drop="the cat asleep, the same as every morning",
    ),
    Scene(
        "home-playground",
        5,
        "16:20",
        "the swing in the park",
        drop="the swing, empty by the time the phone came out",
        people=("Kit",),
        video=True,
    ),
    Scene("home-laundry", 6, "11:00", "washing on the line", drop="washing on the line, a chore"),
    Scene(
        "home-sofa-reading",
        6,
        "19:00",
        "a book and a cup on the sofa",
        drop="a book and a cup, and nobody reading",
    ),
    Scene(
        "home-market-veg",
        7,
        "09:00",
        "the vegetable stall at the market",
        drop="a market stall, could be anyone's Saturday",
    ),
    Scene(
        "home-street-tram",
        7,
        "14:00",
        "the tram on the way home",
        drop="a tram in the street, the way home from the market",
    ),
    # The birthday, Saturday the 8th.
    Scene(
        "home-garden-roses",
        8,
        "10:30",
        "the roses out on the morning of the party",
        drop="the roses, three frames of the same bush",
    ),
    Scene(
        "birthday-balloons",
        8,
        "11:00",
        "balloons tied to the fence before anyone arrived",
        story="S0001",
        people=("Robin",),
    ),
    Scene(
        "garden-table",
        8,
        "12:15",
        "the table and chairs still out on the lawn",
        story="S0001",
        people=EVERYONE,
        video=True,
        favorite=True,
    ),
    Scene(
        "birthday-lemonade",
        8,
        "13:00",
        "the lemonade jug in the sun",
        drop="the jug of lemonade, one of four shots of the drinks",
    ),
    Scene(
        "birthday-cupcakes",
        8,
        "15:30",
        "cupcakes on the tray",
        drop="cupcakes on the tray: the cake is the picture",
    ),
    Scene(
        "garden-cake",
        8,
        "16:30",
        "the cake with the candles still in it",
        story="S0001",
        people=("Kit",),
        favorite=True,
    ),
    Scene(
        "birthday-candles",
        8,
        "16:32",
        "the candles going out",
        story="S0001",
        people=("Kit", "Robin"),
        video=True,
        seconds=5.0,
    ),
    Scene(
        "birthday-gifts",
        8,
        "17:00",
        "the presents, wrapping still on",
        drop="the presents before they were opened",
    ),
    Scene(
        "birthday-confetti",
        8,
        "17:30",
        "confetti on the grass",
        drop="confetti on the grass, the party already over",
    ),
    Scene("birthday-pinata", 8, "18:00", "the piñata mid-swing", drop="blurred mid-swing"),
    Scene(
        "home-coffee-cup",
        9,
        "09:15",
        "the morning after, one cup",
        drop="one cup of coffee, the morning after",
    ),
    # Ordinary days between the party and the woods.
    Scene(
        "home-bread-baking",
        10,
        "18:30",
        "the loaf out of the oven",
        drop="a loaf on the rack, a Monday",
    ),
    Scene(
        "home-tomatoes-garden",
        11,
        "19:00",
        "the tomato plants tied up",
        drop="tomato plants, the same as last week",
    ),
    Scene(
        "home-bath-toys",
        12,
        "19:30",
        "bath toys on the edge of the tub",
        drop="the bath toys, no one in the bath",
        people=("Kit",),
    ),
    Scene(
        "home-balcony-flowers",
        13,
        "08:30",
        "the pots on the balcony",
        drop="the balcony pots, watered",
    ),
    Scene(
        "home-shoes-door",
        13,
        "17:00",
        "shoes by the door",
        drop="shoes by the door, taken by mistake",
    ),
    # The Saturday in the woods, the 15th.
    Scene(
        "woods-tall-trees",
        15,
        "10:40",
        "the tall trees at the start of the walk",
        place=WOODS,
        drop="the trees at the car park, the path says it better",
    ),
    Scene(
        "woods-hamper",
        15,
        "11:05",
        "the hamper open on the checked cloth",
        place=WOODS,
        story="S0003",
        people=EVERYONE,
        favorite=True,
    ),
    Scene(
        "woods-picnic",
        15,
        "11:30",
        "the blanket from the other side",
        place=WOODS,
        drop="the same blanket from the other side of the clearing",
    ),
    Scene(
        "woods-stream",
        15,
        "12:45",
        "the stream where the boots came off",
        place=WOODS,
        story="S0003",
        people=("Kit",),
    ),
    Scene(
        "woods-moss",
        15,
        "13:30",
        "moss on a fallen trunk",
        place=WOODS,
        drop="moss on a log, a close-up of the ground",
    ),
    Scene(
        "woods-fern",
        15,
        "14:15",
        "ferns along the path",
        place=WOODS,
        drop="ferns, another close-up of the ground",
    ),
    Scene(
        "woods-path",
        15,
        "15:40",
        "the long green path back to the car",
        place=WOODS,
        story="S0003",
        people=ADULTS,
        video=True,
        seconds=6.0,
    ),
    Scene(
        "woods-path-light",
        15,
        "15:50",
        "the path again, in the light",
        place=WOODS,
        drop="the same path ten minutes later",
    ),
    # More ordinary days.
    Scene(
        "home-bike-park",
        16,
        "10:30",
        "the Sunday bike ride to the park",
        drop="the bike ride, the frame is mostly road",
        people=("Robin", "Kit"),
        video=True,
    ),
    Scene(
        "home-drawing",
        18,
        "17:00",
        "crayons and a drawing on the table",
        drop="crayons on the table, the drawing out of frame",
        people=("Kit",),
    ),
    Scene(
        "home-evening-lamp",
        19,
        "21:00",
        "the lamp on in the living room",
        drop="the living room at night, nothing happening",
    ),
    # The week by the lake, the 21st to the 28th.
    Scene(
        "home-suitcase",
        21,
        "08:30",
        "the suitcase open on the bed, the morning we left",
        story="S0002",
        people=("Charlie",),
    ),
    Scene(
        "trip-motorway",
        21,
        "11:45",
        "the motorway through the windscreen",
        place=ROAD,
        drop="the motorway through the windscreen, the same as every motorway",
    ),
    Scene(
        "trip-map-car",
        21,
        "12:30",
        "the map on the passenger seat",
        place=ROAD,
        drop="the map on the seat",
    ),
    Scene(
        "trip-lake-arrival",
        21,
        "17:20",
        "the first sight of the lake from the road down",
        place=LAKE,
        story="S0002",
        people=EVERYONE,
        video=True,
        seconds=5.0,
    ),
    Scene(
        "lake-tents",
        21,
        "18:45",
        "the tents pitched on the slope above the lake",
        place=LAKE,
        story="S0002",
        people=ADULTS,
        video=True,
        seconds=5.0,
    ),
    Scene(
        "trip-campfire",
        21,
        "21:00",
        "the fire on the first night",
        place=LAKE,
        drop="the fire, too dark to see who was round it",
        video=True,
    ),
    Scene(
        "trip-morning-mist",
        22,
        "07:30",
        "mist on the water before anyone else was up",
        place=LAKE,
        story="S0002",
    ),
    Scene(
        "trip-lake-jetty",
        22,
        "10:00",
        "the jetty, empty",
        place=LAKE,
        drop="the jetty with nobody on it",
    ),
    Scene(
        "trip-swim",
        22,
        "11:30",
        "the first swim",
        place=LAKE,
        story="S0002",
        people=("Kit", "Charlie"),
        video=True,
        seconds=5.0,
    ),
    Scene(
        "trip-ice-cream",
        22,
        "14:00",
        "two cones, half eaten",
        place=LAKE,
        drop="two ice creams, half eaten",
        people=("Kit",),
    ),
    Scene(
        "lake-sunset",
        22,
        "21:10",
        "the last of the sun going down over the water",
        place=LAKE,
        story="S0002",
        favorite=True,
    ),
    Scene(
        "trip-cows-meadow",
        23,
        "11:30",
        "cows on the meadow halfway up",
        place=SEMNOZ,
        drop="cows on the way up, the top says it better",
        people=EVERYONE,
    ),
    Scene(
        "trip-summit-view",
        23,
        "12:30",
        "the lake from the top",
        place=SEMNOZ,
        story="S0002",
        people=EVERYONE,
    ),
    Scene(
        "trip-town-canal",
        24,
        "10:00",
        "the canal in the old town",
        place=LAKE,
        drop="the canal, a postcard",
    ),
    Scene(
        "trip-cheese-market",
        24,
        "11:00",
        "the cheese stall at the market",
        place=LAKE,
        drop="the cheese stall, could be anyone's market",
    ),
    Scene(
        "trip-rain-day",
        25,
        "09:30",
        "rain, the day nobody left the tent",
        place=LAKE,
        drop="rain on the tent, one grey day",
    ),
    Scene(
        "trip-tent-morning",
        25,
        "14:00",
        "the tents drying out",
        place=LAKE,
        drop="the tents again, drying",
    ),
    Scene(
        "trip-cable-car",
        26,
        "10:00",
        "the cable car up the mountain",
        place=SEMNOZ,
        drop="the cable car, mostly window",
        people=("Kit", "Robin"),
        video=True,
    ),
    Scene(
        "trip-waterfall",
        26,
        "13:00",
        "the waterfall at the end of the walk",
        place=SEMNOZ,
        drop="the waterfall, no one in front of it",
        video=True,
        seconds=5.0,
    ),
    Scene(
        "trip-kayak",
        27,
        "10:30",
        "paddling out to the middle of the lake",
        place=LAKE,
        drop="the paddle out, the swim already says the lake",
        people=("Robin", "Kit"),
        video=True,
        seconds=5.0,
    ),
    Scene(
        "trip-lake-dusk",
        27,
        "21:00",
        "the lake at dusk, the last night",
        place=LAKE,
        drop="the lake at dusk, the sunset from the first night is in",
    ),
    # The return.
    Scene(
        "home-pancakes",
        29,
        "09:30",
        "pancakes on the first morning back",
        story="S0004",
        people=EVERYONE,
    ),
    Scene(
        "home-sunday-park",
        30,
        "16:00",
        "the park bench on the last Sunday",
        drop="a bench in the park, the month already over",
    ),
)

SCENE_BY_KEY: dict[str, Scene] = {scene.key: scene for scene in SCENES}


@dataclass(frozen=True, slots=True)
class Picture:
    """One synthetic capture: the file it comes from, and the line written about it."""

    asset_id: str
    filename: str
    kind: str
    taken_at: str
    caption: str
    scene: str
    place: Place
    is_favorite: bool = False
    people: tuple[str, ...] = ()
    story_key: str | None = None
    drop_reason: str | None = None
    seconds: float = 4.0
    source_id: str | None = None
    sequence: int = 1
    tags: tuple[str, ...] = field(default=())

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def source(self) -> Path:
        return LIBRARY_DIR / f"{self.source_id or self.asset_id}.jpg"

    @property
    def shipped(self) -> bool:
        return self.story_key is not None


def _scene_files(scene: Scene) -> list[str]:
    """The files a scene owns, main shot first; an original has one, a fetched set up to three."""
    single = LIBRARY_DIR / f"{scene.key}.jpg"
    if single.exists():
        return [scene.key]
    return sorted(path.stem for path in LIBRARY_DIR.glob(f"{scene.key}-[0-9][0-9].jpg"))


def _stamp(scene: Scene, minutes_later: int) -> str:
    hour, minute = (int(part) for part in scene.time.split(":"))
    taken = datetime(2024, 6, scene.day, hour, minute, tzinfo=UTC) + timedelta(
        minutes=minutes_later
    )
    return taken.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _expand() -> tuple[Picture, ...]:
    pictures: list[Picture] = []
    counter = 2400
    for scene in SCENES:
        stems = _scene_files(scene)
        for index, stem in enumerate(stems):
            counter += 1
            main = index == 0
            video = scene.video and main
            if main:
                story, drop = scene.story, scene.drop
                caption = scene.caption
            else:
                story = None
                minutes = 2 * index
                caption = f"another frame of {scene.caption}"
                drop = f"another frame of the same moment, {minutes} minutes later"
            pictures.append(
                Picture(
                    asset_id=stem,
                    filename=f"IMG_{counter}.{'mp4' if video else 'jpg'}",
                    kind="video" if video else "still",
                    taken_at=_stamp(scene, 2 * index),
                    caption=caption,
                    scene=scene.key,
                    place=scene.place,
                    is_favorite=scene.favorite and main,
                    people=scene.people if main else (),
                    story_key=story,
                    drop_reason=drop,
                    seconds=scene.seconds if video else 4.0,
                    source_id=stem,
                    sequence=index + 1,
                )
            )
    if not pictures:
        # WHY loudly: this module IS its directory -- the scenes above are only
        # captions until a glob of LIBRARY_DIR says which files exist. A consumer
        # that stages the module without the pictures (the release smoke mounted
        # fake_library.py alone, #881) used to read an empty month and drop every
        # candidate, which surfaces far downstream as "selected no clips".
        raise RuntimeError(
            f"no pictures under {LIBRARY_DIR}: the library is its files, so this "
            "module cannot be used apart from the directory next to it"
        )
    return tuple(pictures)


# Everything the fake Immich serves, in capture order: one month, June 2024.
LIBRARY: tuple[Picture, ...] = _expand()
ALL_PICTURES: tuple[Picture, ...] = LIBRARY

BY_ID: dict[str, Picture] = {picture.asset_id: picture for picture in LIBRARY}

# The pictures the scripted editor ships, in capture order, and the story of each.
CARRIERS: tuple[Picture, ...] = tuple(picture for picture in LIBRARY if picture.shipped)
STORY_OF: dict[str, Story] = {
    picture.asset_id: STORY_BY_KEY[picture.story_key]
    for picture in CARRIERS
    if picture.story_key is not None
}

# Everything else, with the reason the editor gave for leaving it out.
DROPPED: dict[str, str] = {
    picture.asset_id: picture.drop_reason or "not part of any story the month tells"
    for picture in LIBRARY
    if not picture.shipped
}


def carriers_of(story_key: str) -> tuple[Picture, ...]:
    return tuple(picture for picture in CARRIERS if picture.story_key == story_key)


def summary_line() -> str:
    """The line the result page prints: stories that carry pictures, and how many."""
    stories = len({picture.story_key for picture in CARRIERS})
    return f"{stories} stories, {len(CARRIERS)} pictures"


def pool_line() -> str:
    videos = sum(1 for picture in LIBRARY if picture.is_video)
    return f"{len(LIBRARY)} in the pool ({videos} videos, {len(LIBRARY) - videos} photos)"
