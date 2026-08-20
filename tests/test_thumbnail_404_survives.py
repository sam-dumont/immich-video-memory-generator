"""A missing asset must cost one photo, not the whole run.

Found by the capability matrix: after 409 seconds of analysis a single
`GET /assets/<id>/thumbnail` returning 404 ended the generation. In a library
of tens of thousands of assets something is always mid-import or just deleted,
so this is routine rather than exceptional.

Both photo call sites already meant to be tolerant — the comment at the moment
suppression site says a bad thumbnail "must cost this photo its comparison, not
the run" — but they catch `(OSError, RuntimeError, ValueError)`, and Immich
raises `ImmichNotFoundError`, which inherits from `ImmichAPIError` and none of
those.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from immich_memories.api.immich import ImmichNotFoundError
from immich_memories.photos import moment_suppression, photo_pipeline

# Every place these modules reach Immich. Enumerated rather than pattern-matched,
# because one of them had no guard at all.
_NETWORK_CALLS = ("thumbnail_fn(", "download_fn(")


def _raise_404(*args: object, **kwargs: object) -> bytes:
    raise ImmichNotFoundError("Resource not found", status_code=404)


def test_the_hash_resolver_skips_a_missing_thumbnail() -> None:
    resolve = moment_suppression._thumbnail_hash_resolver(
        thumbnail_cache=None, thumbnail_fn=_raise_404
    )

    assert resolve("gone") is None


def test_rendering_skips_a_photo_whose_original_is_gone(tmp_path: Path) -> None:
    """`_render_single_photo` returns None for a skip; callers do `if clip:`."""
    asset = pytest.importorskip("immich_memories.api.models").Asset(
        id="gone",
        type="IMAGE",
        fileCreatedAt="2026-01-05T00:00:00Z",
        fileModifiedAt="2026-01-05T00:00:00Z",
        updatedAt="2026-01-05T00:00:00Z",
        width=4032,
        height=3024,
    )
    config = photo_pipeline.PhotoConfig()

    result = photo_pipeline._render_single_photo(
        asset, config, 1920, 1080, tmp_path, download_fn=_raise_404
    )

    assert result is None


def test_every_immich_call_is_guarded_against_a_missing_asset() -> None:
    """Each network call must sit under a guard naming ImmichAPIError.

    Scoped to the enclosing `try` of an actual Immich call: guards around JPEG
    decoding should stay narrow, and swallowing an API error there would hide a
    real fault rather than tolerate an expected one.
    """
    for module in (moment_suppression, photo_pipeline):
        lines = inspect.getsource(module).splitlines()
        calls = [
            i
            for i, ln in enumerate(lines)
            if any(c in ln for c in _NETWORK_CALLS) and "def " not in ln
        ]
        assert calls, f"{module.__name__} no longer calls Immich"

        for i in calls:
            guard = next((ln for ln in lines[i : i + 80] if ln.strip().startswith("except ")), None)
            assert guard and "ImmichAPIError" in guard, (
                f"{module.__name__} line {i + 1} ({lines[i].strip()}) is guarded by "
                f"{guard!r} — a 404 raises ImmichNotFoundError, which inherits from "
                "ImmichAPIError and from none of those"
            )
