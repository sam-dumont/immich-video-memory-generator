"""Real-Immich gate: a finished film goes back into Immich, into its album, on v2 and v3."""

from __future__ import annotations

import subprocess
import time
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
            # Unique bytes per run: Immich answers a checksum it already holds
            # (even in the trash) with that old asset instead of a new upload.
            "-metadata",
            f"comment={uuid.uuid4()}",
            str(film),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return film


def _read_by_immich(gate_client, asset_id: str) -> bool:
    """Whether Immich's metadata job has read the file (only it sets dateTimeOriginal)."""
    for _ in range(60):
        exif = gate_client.get_asset(asset_id).exif_info
        if exif is not None and exif.date_time_original is not None:
            return True
        time.sleep(0.5)
    return False


def test_a_rerendered_film_is_filed_in_its_album_and_replaces_the_earlier_one(
    gate_client, tmp_path
):
    album = f"Gate films {uuid.uuid4().hex[:8]}"
    first = gate_client.upload_memory(_render(tmp_path / "a", "red"), album, captured_at=_CAPTURED)
    # A real re-render comes long after Immich read the first upload. That read
    # used to wipe a tag applied while it ran (#1270), so the film lost its
    # provenance and a v3 re-render kept it as a duplicate.
    assert _read_by_immich(gate_client, first["asset_id"])
    assert first["asset_id"] in gate_client.generated_asset_ids()
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
    # v2 proves the earlier render is ours by its device identity; v3 dropped
    # deviceId, so there the immich-memories/generated tag proves it. Either way
    # the earlier render of the same recipe goes to Immich's trash.
    assert filed == [second["asset_id"]]
    assert gate_client.get_asset(first["asset_id"]).is_trashed
