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
from collections.abc import Mapping

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

_CAPTION_REFUSE_AT = 4.5
_CAPTION_KIND = {
    "empty_room_ceiling_or_floor": 2.5,
    "accidental_or_blurred_frame": 2.5,
    "lone_everyday_object": 2.5,
    "body_part_closeup": 2.5,
    "meaningful_record": 1.5,
    "screen_or_document": 1.5,
    "place_or_scenery": -0.5,
}
_CAPTION_PEOPLE = {"two": -0.5, "small-group": -1.0, "crowd": -1.0}
_CAPTION_FLAGS = {
    "children": -0.5,
    "document": 1.0,
    "screen": 1.0,
    "BLOWN OUT": 2.0,
    "SOFT (blurry)": 1.0,
}
_NOBODY_ALIVE = 2.0


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

# Anyone alive: people of any age and animals. General English, not read off any library.
_ALIVE = _words(
    "man men woman women person people boy boys girl girls child children baby babies toddler",
    "toddlers infant infants kid kids son daughter mother father dad mom couple friends family",
    "crowd group bride groom dog dogs puppy puppies cat cats kitten kittens horse horses pony",
    "ponies cow cows sheep goat goats pig pigs bird birds duck ducks rabbit rabbits deer elephant",
    "elephants giraffe giraffes lion lions monkey monkeys animal animals pet pets donkey camel zebra",
)
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


def names_someone_alive(caption: str) -> bool:
    """The caption names a person or an animal, not a likeness of one or a thing made for one."""
    text = _MADE_FOR.sub(lambda match: match.group(2), _LIKENESS.sub(" ", caption))
    return bool(_ALIVE.search(text))


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


def carries_nothing(heads: Mapping[str, str], line: str, caption: str | None = None) -> bool:
    """The picture's subject is not alive and shows nothing a film can use, on the facts alone."""
    if heads.get("frame_kind") == "people_moment" and "SOFT (blurry)" not in line:
        # A sharp people moment stands before anything is counted (#1205).
        return False
    if caption:
        if names_someone_alive(caption):
            return False
        points = _head_points(heads, line, _CAPTION_KIND, _CAPTION_PEOPLE, _CAPTION_FLAGS)
        points += _NOBODY_ALIVE + sum(
            value for pattern, value in _CAPTION_KINDS if pattern.search(caption)
        )
        return points >= _CAPTION_REFUSE_AT
    if heads.get("activity") == "animal-nature":
        return False
    return _head_points(heads, line, _HEADS_KIND, _HEADS_PEOPLE, _HEADS_FLAGS) >= _HEADS_REFUSE_AT
