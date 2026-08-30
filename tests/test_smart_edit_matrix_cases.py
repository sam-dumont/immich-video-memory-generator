"""The private smart-edit matrix preserves each product's acquisition scope."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_smart_edit_matrix as matrix

from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange


def test_case_manifest_preserves_an_album_reference(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "key": "curated-trip",
                        "label": "Curated trip",
                        "product": "album",
                        "ranges": [{"start": "2022-07-21", "end": "2022-08-03"}],
                        "target_seconds": 120,
                        "brief": "Edit only the curated album membership.",
                        "album_ref": "private-album-id",
                    }
                ]
            }
        )
    )

    (case,) = matrix._load_cases(manifest)

    assert case.album_ref == "private-album-id"


def test_album_case_fetches_curated_membership_instead_of_the_date_range(monkeypatch) -> None:
    older = SimpleNamespace(id="older", file_created_at=datetime(2022, 7, 21, tzinfo=UTC))
    newer = SimpleNamespace(id="newer", file_created_at=datetime(2022, 8, 3, tzinfo=UTC))
    resolved = SimpleNamespace(id="resolved-album")
    client = SimpleNamespace(resolve_album=lambda _reference: resolved)
    observed: dict[str, object] = {}

    def fetch_album_media(
        actual_client,
        album,
        *,
        config,
        use_live_photos,
        use_photos,
    ):
        observed.update(
            {
                "client": actual_client,
                "album": album,
                "config": config,
                "use_live_photos": use_live_photos,
                "use_photos": use_photos,
            }
        )
        return SimpleNamespace(videos=[newer], photos=[older])

    monkeypatch.setattr(matrix, "fetch_album_media", fetch_album_media)
    monkeypatch.setattr(
        matrix,
        "fetch_photos",
        lambda **_kwargs: pytest.fail("an album must not fall back to date-range acquisition"),
    )
    config = Config()
    case = matrix.Case(
        key="curated-trip",
        label="Curated trip",
        product="album",
        ranges=(
            DateRange(
                start=datetime(2022, 7, 21, tzinfo=UTC),
                end=datetime(2022, 8, 3, 23, 59, tzinfo=UTC),
            ),
        ),
        target_seconds=120,
        brief="Edit only the curated album membership.",
        album_ref="private-album-id",
    )

    assets = matrix._fetch_assets(client, config, case, {})

    assert [asset.id for asset in assets] == ["older", "newer"]
    assert observed == {
        "client": client,
        "album": resolved,
        "config": config,
        "use_live_photos": True,
        "use_photos": True,
    }


def test_final_refinement_motion_prefers_the_rendition_fetcher(monkeypatch) -> None:
    """The 480p Immich rendition is tried before ever touching the full original."""
    calls: list[str] = []

    def fake_rendition(_client, _asset, *, cache_dir):
        calls.append("rendition")
        return Path("/rendition/path.mp4")

    def fake_original(_client, _candidate, _batch):
        calls.append("original")
        return Path("/original/path.mp4")

    monkeypatch.setattr(matrix, "_fetch_motion_rendition", fake_rendition)
    monkeypatch.setattr(matrix, "_fetch_motion_original", fake_original)
    candidate = SimpleNamespace(asset_id="clip-1", source=SimpleNamespace(id="clip-1"))

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={},
        warned=set(),
    )

    assert path == Path("/rendition/path.mp4")
    assert calls == ["rendition"]


def test_final_refinement_motion_falls_back_to_original_on_rendition_failure(
    monkeypatch,
) -> None:
    """A failed rendition fetch falls open onto the slower full-original path."""
    calls: list[str] = []

    def fake_rendition(_client, _asset, *, cache_dir):
        calls.append("rendition")
        return None

    def fake_original(_client, _candidate, _batch):
        calls.append("original")
        return Path("/original/path.mp4")

    monkeypatch.setattr(matrix, "_fetch_motion_rendition", fake_rendition)
    monkeypatch.setattr(matrix, "_fetch_motion_original", fake_original)
    candidate = SimpleNamespace(asset_id="clip-2", source=SimpleNamespace(id="clip-2"))

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={},
        warned=set(),
    )

    assert path == Path("/original/path.mp4")
    assert calls == ["rendition", "original"]


def test_final_refinement_motion_falls_back_to_the_known_path_when_both_fetchers_fail(
    monkeypatch,
) -> None:
    """Neither fetcher working must never drop motion evidence the run already had."""
    monkeypatch.setattr(matrix, "_fetch_motion_rendition", lambda *_a, **_k: None)
    monkeypatch.setattr(matrix, "_fetch_motion_original", lambda *_a, **_k: None)
    candidate = SimpleNamespace(asset_id="clip-3", source=SimpleNamespace(id="clip-3"))
    known = Path("/already/known.mp4")

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={"clip-3": known},
        warned=set(),
    )

    assert path == known
