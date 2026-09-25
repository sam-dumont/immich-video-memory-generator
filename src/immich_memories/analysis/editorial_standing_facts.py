"""Does a picture carry nothing worth showing? Read from facts already on the picture, no model asked.

The rule in plain words (the owner's): objects and useless stuff out, people and animals in, a leg or
a shoe on its own out. So standing refuses only a picture whose subject is not alive: an object or a
product, a screen or a device, a document or a sign, an empty room, a plate of food, a dark or
accidental frame, a body part with no one to go with it. A caption that names a person of any age
or an animal is never refused.

Two points tables, one per tier. With no caption (the no-model install) the frame-kind, document,
screen and people heads and the pixel warnings on the line decide. With the ingest caption, the
caption's words join them: whether it names anyone alive, and which of eight kinds of thing it
names. The kinds and their words come from the public CC BY corpus (the caption tokens most tied to a
refusal), never from anyone's library.

The points are logistic regressions fitted on that public corpus (photographer-disjoint splits,
glm-5.3-flash's two-order standing answers as labels), rounded to half points. On a private
evaluation library graded by the same reader, both tables agree with it more often than the local
30B did and catch more of its refusals (standing-facts-v1; with captions: agreement 0.966 vs 0.935,
recall 0.67 vs 0.36).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.editorial_carrier_eligibility import screen_flagged

_HEADS_REFUSE_AT = 3.0
_HEADS_KIND = {
    "empty_room_ceiling_or_floor": 4.0,
    # No public row carried this label, so it takes the empty room's points rather than a fit.
    "accidental_or_blurred_frame": 4.0,
    "lone_everyday_object": 3.5,
    "body_part_closeup": 2.5,
    "meaningful_record": 2.5,
    "screen_or_document": 2.0,
}
_HEADS_PEOPLE = {"two": -0.5, "small-group": -1.0, "crowd": -1.5}
_HEADS_FLAGS = {
    "children": -1.0,
    "document": 1.0,
    "screen": 1.0,
    "BLOWN OUT": 1.5,
    "SOFT (blurry)": 0.5,
}


@dataclass(frozen=True)
class _CaptionTable:
    kind: Mapping[str, float]
    people: Mapping[str, float]
    flags: Mapping[str, float]
    nobody_alive: float
    goods: float
    goods_as_object: bool = False
    faceless_person: float = 0.0
    refuse_at: float = 4.5


_CAPTION_FLAG_POINTS = {
    "children": -0.5,
    "document": 1.0,
    "screen": 1.0,
    "BLOWN OUT": 2.0,
    "SOFT (blurry)": 1.0,
}
# Fitted with no face facts: a library whose faces Immich never read.
_CAPTION_TABLE = _CaptionTable(
    kind={
        "empty_room_ceiling_or_floor": 2.5,
        "accidental_or_blurred_frame": 2.5,
        "lone_everyday_object": 2.5,
        "body_part_closeup": 2.5,
        "meaningful_record": 1.5,
        "screen_or_document": 1.5,
        "place_or_scenery": -0.5,
    },
    people={"two": -0.5, "small-group": -1.0, "crowd": -1.0},
    flags=_CAPTION_FLAG_POINTS,
    nobody_alive=2.0,
    goods=1.0,
)
# Fitted with them. The people head seeing somebody Immich found no face for (legs, feet, a back,
# a misreading) now carries a point, so the frame kinds and the empty caption carry less; goods
# on display count as the object they are.
_CAPTION_FACES_TABLE = _CaptionTable(
    kind={
        "empty_room_ceiling_or_floor": 2.0,
        "accidental_or_blurred_frame": 2.0,
        "lone_everyday_object": 2.0,
        "body_part_closeup": 2.0,
        "meaningful_record": 1.0,
        "screen_or_document": 1.0,
        "place_or_scenery": -1.0,
    },
    people={"two": -0.5, "small-group": -1.0, "crowd": -1.5},
    flags=_CAPTION_FLAG_POINTS | {"BLOWN OUT": 1.5},
    nobody_alive=1.5,
    goods=0.0,
    goods_as_object=True,
    faceless_person=1.0,
)
_WEIGHED_MOMENT_WARNINGS = ("SOFT (blurry)", "DARK")


def _words(*words: str) -> re.Pattern[str]:
    return re.compile(r"\b(" + "|".join(" ".join(words).split()) + r")\b", re.IGNORECASE)


# The public corpus's refusal-tied caption tokens, grouped by what they name, with their points.
_CAPTION_KINDS = (
    (
        _words(
            "placed pair collection surface object objects items pile piece various displayed",
            "displaying including featuring filled resting sits hanging box bottle bottles glass",
            "metal plastic wooden toy toys stuffed balloons bicycle trunk bow tied",
        ),
        1.0,
    ),
    (_words("screen screens displaying game wire cable device phone laptop computer monitor"), 1.0),
    (_words("feet foot hand hands legs leg shoe shoes"), 0.5),
    (_words("plate plates wine food cake dining fresh bowl dish"), 0.5),
    (
        _words(
            "table desk wall floor door window room hallway chairs parking sidewalk concrete",
        ),
        0.5,
    ),
    (_words("plant plants leaves petals flower flowers growing branches"), 0.5),
    (
        _words(
            "text paper papers book books sign signs reads reading drawing picture photo",
            "character designs design patterns",
        ),
        0.5,
    ),
)

# Goods on display: a store shelf, a product display, a showroom. From the public corpus, where a
# caption naming a shelf is refused half the time and a market or a shop full of people is not.
_GOODS = re.compile(
    r"\b(shelf|shelves|products?|merchandise|showroom|display cases?|for sale|price tags?|"
    r"packaged|packages|packaging|aisle)\b",
    re.IGNORECASE,
)
_NOBODY_SEEN = frozenset({"none", "undetermined"})

# Anyone alive: people of any age and animals. General English, not read off any library.
_PEOPLE_WORDS = (
    "man men woman women person people boy boys girl girls child children baby babies toddler",
    "toddlers infant infants kid kids son daughter mother father dad mom couple friends family",
    "crowd group bride groom",
)
_ANIMAL_WORDS = (
    "dog dogs puppy puppies cat cats kitten kittens horse horses pony",
    "ponies cow cows sheep goat goats pig pigs bird birds duck ducks rabbit rabbits deer elephant",
    "elephants giraffe giraffes lion lions monkey monkeys animal animals pet pets donkey camel zebra",
)
_ALIVE = _words(*_PEOPLE_WORDS, *_ANIMAL_WORDS)
_ANIMAL = _words(*_ANIMAL_WORDS)
# A likeness of a living thing, or something made for one, is an object: "a stuffed bear", "a
# statue of a man", "baby clothes", "a dog bowl".
_LIKENESS = re.compile(
    r"\b(stuffed|toy|plush|teddy|figurine|statue|sculpture|cartoon|drawing|painting|picture|image|"
    r"photo|poster|model|inflatable|ceramic|wooden|plastic|stone|metal)\s+(of\s+(a|an|the)\s+)?\w+",
    re.IGNORECASE,
)
_MADE_FOR = re.compile(
    r"\b\w+('s)?\s+(clothes|clothing|bottle|bottles|shoe|shoes|sock|socks|toy|toys|monitor|food|bed|"
    r"crib|seat|carrier|stroller|pram|book|blanket|onesie|bib|formula|bowl|collar|leash|cage)\b",
    re.IGNORECASE,
)


def _living_text(caption: str) -> str:
    return _MADE_FOR.sub(lambda match: match.group(2), _LIKENESS.sub(" ", caption))


def names_someone_alive(caption: str) -> bool:
    """The caption names a person or an animal, not a likeness of one or a thing made for one."""
    return bool(_ALIVE.search(_living_text(caption)))


def _names_an_animal(caption: str) -> bool:
    return bool(_ANIMAL.search(_living_text(caption)))


# Parts of a body and what a foot or a hand wears: a shot of these alone shows no one.
_BODY_PART = _words(
    "feet foot toes toe leg legs knee knees hand hands finger fingers arm arms",
    "shoe shoes sneaker sneakers trainers boot boots sandal sandals sock socks slipper slippers",
)


def shows_only_a_body_part(
    heads: Mapping[str, str], caption: str | None, *, face: bool | None
) -> bool:
    """A body part with no face: legs, feet, shoes or hands alone (the owner's rule).

    It sits on top of the points tables and is not fitted to them. A face on the picture makes
    it a person. The frame head's body-part reading, or a caption naming a body part or what a
    foot wears and no animal, says what the shot shows. Where Immich never read this library's
    faces (`face` is None) there is no face to miss, so a caption must also name nobody alive.
    """
    if face is True:
        return False
    if heads.get("frame_kind") == "body_part_closeup":
        return True
    if not caption or not _BODY_PART.search(caption) or _names_an_animal(caption):
        return False
    # With faces read and none found, a person the caption names is the body part it shows.
    return face is False or not names_someone_alive(caption)


def face_evidence(assets: Mapping[str, Any]) -> Callable[[str], bool | None]:
    """Whether Immich found a face on a picture of this scope, as `carries_nothing` reads it.

    None for every picture when Immich recognised nobody anywhere in the scope: its faces were
    never read (recognition off, or a library of pets and places), so an empty list says nothing.
    """
    reads_faces = any(
        getattr(asset, "people", None) or getattr(asset, "faces", None) for asset in assets.values()
    )

    def face(asset_id: str) -> bool | None:
        if not reads_faces:
            return None
        asset = assets.get(asset_id)
        return bool(asset is not None and (asset.people or getattr(asset, "faces", None)))

    return face


def _head_points(
    heads: Mapping[str, str],
    line: str,
    kind: Mapping[str, float],
    people: Mapping[str, float],
    flags: Mapping[str, float],
) -> float:
    points = kind.get(heads.get("frame_kind", ""), 0.0) + people.get(heads.get("people", ""), 0.0)
    facts = {
        "children": heads.get("children") == "yes",
        "document": heads.get("doc_docling", "photograph") != "photograph",
        "screen": screen_flagged(heads),
        "BLOWN OUT": "BLOWN OUT" in line,
        "SOFT (blurry)": "SOFT (blurry)" in line,
    }
    return points + sum(value for fact, value in flags.items() if facts[fact])


def carries_nothing(
    heads: Mapping[str, str], line: str, caption: str | None = None, *, face: bool | None = None
) -> bool:
    """The picture's subject is not alive and shows nothing a film can use, on the facts alone.

    `face` is whether Immich found a face on it: True, False, or None when this library's
    faces were never read (then a person is taken at the heads' and the caption's word).
    """
    if (
        heads.get("frame_kind") == "people_moment"
        and not any(warning in line for warning in _WEIGHED_MOMENT_WARNINGS)
        and face is not False
    ):
        # A clear people moment stands before anything is counted (#1205), once a face says a
        # person is in it: the frame head calls legs, feet or a back a people moment too. A
        # soft or dark one is counted like any other frame.
        return False
    if caption:
        return _caption_refuses(heads, line, caption, face)
    if heads.get("activity") == "animal-nature":
        return False
    return _head_points(heads, line, _HEADS_KIND, _HEADS_PEOPLE, _HEADS_FLAGS) >= _HEADS_REFUSE_AT


def _caption_refuses(heads: Mapping[str, str], line: str, caption: str, face: bool | None) -> bool:
    named = names_someone_alive(caption)
    # A person the caption names is kept once a face is found; an animal has no face to find.
    if named and (face is not False or _names_an_animal(caption)):
        return False
    table = _CAPTION_TABLE if face is None else _CAPTION_FACES_TABLE
    goods = not named and bool(_GOODS.search(caption))
    if goods and table.goods_as_object:
        heads = {**heads, "frame_kind": "lone_everyday_object"}
    points = _head_points(heads, line, table.kind, table.people, table.flags)
    points += sum(value for pattern, value in _CAPTION_KINDS if pattern.search(caption))
    points += (0.0 if named else table.nobody_alive) + (table.goods if goods else 0.0)
    if face is False and heads.get("people", "undetermined") not in _NOBODY_SEEN:
        points += table.faceless_person
    return points >= table.refuse_at
