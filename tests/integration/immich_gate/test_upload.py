"""Real-Immich gate: a finished film goes back into Immich, into its album, on v2 and v3."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tests.integration.immich_fixtures import requires_immich

pytestmark = [requires_immich]

_FILM_NAME = "gate_memory_0f1e2d3c.mp4"
_CAPTURED = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)


def _render(folder: Path, colour: str) -> Path:
    """A one-second film; the colour makes each render a different file under the same name."""
    folder.mkdir(parents=True)
    film = folder / _FILM_NAME
    subprocess.run(  # noqa: S603 -- fixed argv
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:s=320x240:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(film),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return film


def test_a_rerendered_film_is_filed_in_its_album_and_replaces_the_earlier_one(
    gate_client, gate_version, tmp_path
):
    album = f"Gate films {uuid.uuid4().hex[:8]}"
    first = gate_client.upload_memory(_render(tmp_path / "a", "red"), album, captured_at=_CAPTURED)
    second = gate_client.upload_memory(
        _render(tmp_path / "b", "blue"), album, captured_at=_CAPTURED
    )

    assert first["album_id"] is not None
    assert second["album_id"] == first["album_id"]
    assert gate_client.resolve_album(album).id == first["album_id"]
    assert gate_client.get_asset(second["asset_id"]).file_created_at == _CAPTURED

    filed = sorted(
        asset["id"]
        for asset in gate_client.list_album_assets(second["album_id"])
        if asset.get("originalFileName") == _FILM_NAME
    )
    if gate_version == "v2":
        # A v2 upload carries this app's device identity, so the earlier render
        # of the same recipe goes to Immich's trash.
        assert filed == [second["asset_id"]]
        assert gate_client.get_asset(first["asset_id"]).is_trashed
    else:
        # A v3 upload carries no device identity, so nothing proves the earlier
        # file is ours and it is kept (supersede_previous_renders).
        assert filed == sorted([first["asset_id"], second["asset_id"]])
        assert not gate_client.get_asset(first["asset_id"]).is_trashed
