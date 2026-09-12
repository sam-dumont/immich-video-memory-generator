"""One reading of a literal JSON number, shared by the editorial contracts."""

from __future__ import annotations

from math import isfinite


def exact_number(value: object) -> float | None:
    """Return a finite int/float as a float; a bool, a subclass or NaN reads as absent."""
    if type(value) is int or type(value) is float:
        return float(value) if isfinite(value) else None
    return None
