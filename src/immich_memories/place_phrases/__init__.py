"""How each title language says where a trip went.

A trip title reads "TWO WEEKS IN THE NETHERLANDS" or "DEUX SEMAINES EN
CRÈTE": the preposition and the article belong to the place and the language,
and a wrong one ("À CRÈTE", "IN NETHERLANDS") is worse than none. So each
language that has rules owns a module here, named after its locale with `-`
as `_` and lowercased (`fr`, `pt_br`), exposing

    phrase(place: Place) -> str | None

which returns the whole phrase ("in the Netherlands", "dans les Pouilles,
Italie") or None when it cannot say it right. A locale listed in
`PREPOSITION_FREE` has deliberately no module. Either way, no phrase means the
trip title is written without a preposition at all, place first. Adding a
language to `i18n.SUPPORTED_LOCALES` without one or the other fails a test.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from immich_memories.place_phrases.place import Place

__all__ = ["PREPOSITION_FREE", "Place", "PlacePhrase", "phrase_module_name", "place_phrase"]

PlacePhrase = Callable[[Place], "str | None"]

# Title languages that write trip titles with no preposition, on purpose.
PREPOSITION_FREE: frozenset[str] = frozenset()


def phrase_module_name(locale: str) -> str:
    """The module a locale's rules live in: `pt-BR` -> `immich_memories.place_phrases.pt_br`."""
    return f"{__name__}.{locale.replace('-', '_').lower()}"


def place_phrase(locale: str, place: Place) -> str | None:
    """The phrase this language puts in a trip title for `place`, or None for none."""
    if locale in PREPOSITION_FREE:
        return None
    try:
        module = importlib.import_module(phrase_module_name(locale))
    except ModuleNotFoundError:
        return None
    phrase: PlacePhrase = module.phrase
    return phrase(place)
