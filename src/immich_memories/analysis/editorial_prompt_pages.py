"""Bound text requests without dropping offered rows."""

from __future__ import annotations

import json
from typing import Any

PAGE_UNITS = 16
PAGE_CHARS = 14000


def pages(rows, *, max_items=PAGE_UNITS, max_chars=PAGE_CHARS):
    """Page every row in source order. Limits change request size, never coverage."""
    page: list[Any] = []
    size = 0
    for row in rows:
        width = len(json.dumps(row, ensure_ascii=False))
        if page and (len(page) >= max_items or size + width > max_chars):
            yield page
            page, size = [], 0
        page.append(row)
        size += width
    if page:
        yield page
