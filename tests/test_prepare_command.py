"""`prepare` runs the expensive half of a cut, says what it cost, and stops."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner, Result

from immich_memories.analysis.editorial_preparation import PreparationResult
from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.cli import main
from immich_memories.config_loader import Config

TAKEN = datetime(2024, 6, 14, 10, 30, tzinfo=UTC)


def _photo(asset_id: str) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=TAKEN,
        fileModifiedAt=TAKEN,
        updatedAt=TAKEN,
        isFavorite=False,
        originalFileName=f"{asset_id}.HEIC",
        exifInfo=ExifInfo(
            make="Apple", model="iPhone 15 Pro", exifImageWidth=4032, exifImageHeight=3024
        ),
    )


def _config() -> Config:
    config = Config()
    config.immich.url = "http://immich.test:2283"
    config.immich.api_key = "immich-key"
    return config


def _client(photos: list[Asset]) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_videos_for_date_range.return_value = []
    client.get_photos_for_date_range.return_value = photos
    client.get_live_photos_for_date_range.return_value = []
    return client


def _prepared(**kwargs) -> PreparationResult:
    """Stand in for the producers, reporting the stages they report."""
    total = len(kwargs["assets"])
    for stage in ("previews", "pixels", "public_heads", "detectors"):
        kwargs["progress"](stage, total, total)
    return PreparationResult(
        requested=total, missing_by_producer={}, failures={}, produced={"pixel": total}
    )


def _invoke(
    args: list[str],
    config: Config,
    *,
    photos: list[Asset] | None = None,
    prepared=_prepared,
) -> Result:
    with (
        # WHY: init_config_dir would create ~/.immich-memories on the real home.
        patch("immich_memories.cli.init_config_dir"),
        # WHY: get_config would read the developer's own config file.
        patch("immich_memories.cli.get_config", return_value=config),
        # WHY: SyncImmichClient is the Immich HTTP server this path would call.
        patch(
            "immich_memories.api.sync_client.SyncImmichClient",
            return_value=_client(photos or []),
        ),
        # WHY: the producers are ONNX, torch and a caption server, none present in a unit run.
        patch(
            "immich_memories.analysis.editorial_preparation.prepare_editorial_annotations",
            side_effect=prepared,
        ),
    ):
        return CliRunner().invoke(main, args, catch_exceptions=False)


def test_prepare_reports_a_rate_per_producer_and_renders_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = _config()

    result = _invoke(
        ["prepare", "--year", "2024", "--month", "6", "--library-size", "10000"],
        config,
        photos=[_photo("a1"), _photo("a2")],
    )

    assert result.exit_code == 0, result.output
    assert "2 pictures" in result.output
    for producer in ("previews", "pixels", "public_heads", "detectors"):
        assert producer in result.output
    assert "s/picture" in result.output
    assert "At this rate 10,000 pictures would take" in result.output
    assert not list(Path(config.output.output_path).glob("*.mp4"))


def test_prepare_names_the_sources_immich_will_not_serve_without_calling_the_pass_incomplete(
    tmp_path, monkeypatch
) -> None:
    """A 404'd preview is nothing a rerun fixes, so the pass succeeds and still says it."""

    def refused(**kwargs) -> PreparationResult:
        result = _prepared(**kwargs)
        return replace(
            result, unservable_sources={"a1": "preview unavailable at Immich (HTTP 404)"}
        )

    monkeypatch.setenv("HOME", str(tmp_path))

    result = _invoke(
        ["prepare", "--year", "2024"],
        _config(),
        photos=[_photo("a1"), _photo("a2")],
        prepared=refused,
    )

    assert result.exit_code == 0, result.output
    assert "1 sources will leave any cut" in result.output
    assert "preview unavailable at Immich (HTTP 404)" in result.output


def test_prepare_says_so_when_the_scope_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    result = _invoke(["prepare", "--year", "2024"], _config(), photos=[])

    assert result.exit_code == 0, result.output
    assert "nothing to prepare" in result.output
