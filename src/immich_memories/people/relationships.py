"""Human relationship vocabulary shared by the people file and its editor.

The graph stores directed facts so prompts can read them without guessing which
side of "parent" they are looking at. The UI writes both directions from one
answer; nobody should have to enter the reciprocal edge by hand.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationshipChoice:
    kind: str
    label: str


RELATIONSHIP_CHOICES = (
    RelationshipChoice("partner-of", "partner of"),
    RelationshipChoice("spouse-of", "married to"),
    RelationshipChoice("parent-of", "parent of"),
    RelationshipChoice("mother-of", "mother of"),
    RelationshipChoice("father-of", "father of"),
    RelationshipChoice("child-of", "child of"),
    RelationshipChoice("son-of", "son of"),
    RelationshipChoice("daughter-of", "daughter of"),
    RelationshipChoice("sibling-of", "sibling of"),
    RelationshipChoice("sister-of", "sister of"),
    RelationshipChoice("brother-of", "brother of"),
    RelationshipChoice("twin-of", "twin of"),
    RelationshipChoice("godparent-of", "godparent of"),
    RelationshipChoice("godmother-of", "godmother of"),
    RelationshipChoice("godfather-of", "godfather of"),
    RelationshipChoice("best-friend-of", "best friend of"),
    RelationshipChoice("friend-of", "friend of"),
    RelationshipChoice("uncle-of", "uncle of"),
    RelationshipChoice("aunt-of", "aunt of"),
    RelationshipChoice("aunt-or-uncle-of", "aunt or uncle of"),
    RelationshipChoice("nibling-of", "niece or nephew of"),
    RelationshipChoice("cousin-of", "cousin of"),
    RelationshipChoice("grandparent-of", "grandparent of"),
    RelationshipChoice("grandchild-of", "grandchild of"),
    RelationshipChoice("parent-in-law-of", "parent-in-law of"),
    RelationshipChoice("child-in-law-of", "child-in-law of"),
    RelationshipChoice("sibling-in-law-of", "sibling-in-law of"),
)

_RECIPROCAL = {
    "partner-of": "partner-of",
    "spouse-of": "spouse-of",
    "parent-of": "child-of",
    "mother-of": "child-of",
    "father-of": "child-of",
    "child-of": "parent-of",
    "son-of": "parent-of",
    "daughter-of": "parent-of",
    "sibling-of": "sibling-of",
    "sister-of": "sibling-of",
    "brother-of": "sibling-of",
    "twin-of": "twin-of",
    "godparent-of": "godchild-of",
    "godmother-of": "godchild-of",
    "godfather-of": "godchild-of",
    "godchild-of": "godparent-of",
    "best-friend-of": "best-friend-of",
    "friend-of": "friend-of",
    "uncle-of": "nibling-of",
    "aunt-of": "nibling-of",
    "nibling-of": "aunt-or-uncle-of",
    "aunt-or-uncle-of": "nibling-of",
    "cousin-of": "cousin-of",
    "grandparent-of": "grandchild-of",
    "grandchild-of": "grandparent-of",
    "parent-in-law-of": "child-in-law-of",
    "child-in-law-of": "parent-in-law-of",
    "sibling-in-law-of": "sibling-in-law-of",
}

_OWNER_ROLE = {
    "partner-of": "partner",
    "spouse-of": "spouse",
    "parent-of": "parent",
    "mother-of": "mother",
    "father-of": "father",
    "child-of": "child",
    "son-of": "son",
    "daughter-of": "daughter",
    "sibling-of": "sibling",
    "sister-of": "sister",
    "brother-of": "brother",
    "twin-of": "twin",
    "godparent-of": "godparent",
    "godmother-of": "godmother",
    "godfather-of": "godfather",
    "godchild-of": "godchild",
    "best-friend-of": "best friend",
    "friend-of": "friend",
    "uncle-of": "uncle",
    "aunt-of": "aunt",
    "aunt-or-uncle-of": "aunt or uncle",
    "nibling-of": "nibling",
    "cousin-of": "cousin",
    "grandparent-of": "grandparent",
    "grandchild-of": "grandchild",
    "parent-in-law-of": "parent-in-law",
    "child-in-law-of": "child-in-law",
    "sibling-in-law-of": "sibling-in-law",
}


def reciprocal_kind(kind: str) -> str:
    """The fact written on the other person after one relationship answer."""
    return _RECIPROCAL.get(kind, kind)


def owner_role(kind: str) -> str | None:
    """The owner-relative role implied when a relationship points at the owner."""
    return _OWNER_ROLE.get(kind)


def relationship_label(kind: str) -> str:
    """A compact human label for a stored directed relationship."""
    for choice in RELATIONSHIP_CHOICES:
        if choice.kind == kind:
            return choice.label
    return kind.replace("-", " ")
