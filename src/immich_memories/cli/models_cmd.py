"""Fetch the pinned model artifacts an install needs before its first cut."""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import click

from immich_memories.triage.encoder import DINOV2_SMALL_ONNX_SHA256

# The pinned export is 88 MB; the cap only exists so a wrong URL cannot fill a disk.
MAX_ENCODER_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0
_CHUNK_BYTES = 1024 * 1024


def register_models_commands(cli_group: click.Group) -> None:
    """Register the `models` group that prepares an install's pinned artifacts."""

    @cli_group.group()
    def models() -> None:
        """Fetch the pinned model artifacts selection needs."""

    @models.command()
    @click.option("--force", is_flag=True, help="Re-download even when the file is already right")
    @click.pass_context
    def fetch(ctx: click.Context, force: bool) -> None:
        """Download the pinned DINOv2 encoder export to the configured path."""
        config = ctx.obj["config"]
        destination = config.triage.encoder_path
        try:
            outcome = fetch_encoder(
                url=config.triage.encoder_url,
                destination=destination,
                sha256=DINOV2_SMALL_ONNX_SHA256,
                force=force,
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            click.echo(f"encoder: {exc}", err=False)
            raise SystemExit(1) from exc
        verb = "already present at" if outcome == "present" else "downloaded to"
        click.echo(f"encoder: {verb} {destination}")


def fetch_encoder(
    *,
    url: str,
    destination: Path,
    sha256: str,
    force: bool = False,
    max_bytes: int = MAX_ENCODER_BYTES,
) -> str:
    """Put the digest-pinned encoder at ``destination``; return what it took.

    ``"present"`` when the file already carries the pinned digest, ``"downloaded"``
    when it was fetched. The bytes are hashed in a temporary file and only renamed
    into place once they match, so a bad download never leaves a loadable path.
    """
    if not force and destination.is_file() and _digest_of(destination) == sha256:
        return "present"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.partial")
    try:
        digest = _stream_to(url, temporary, max_bytes=max_bytes)
        if digest != sha256:
            raise ValueError(f"{url}: digest {digest[:12]} is not the pinned {sha256[:12]}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "downloaded"


def _digest_of(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _stream_to(url: str, destination: Path, *, max_bytes: int) -> str:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"{url}: model downloads must be HTTP(S)")
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(  # noqa: S310 — the scheme is checked above
        url, headers={"User-Agent": "immich-memories"}
    )
    with (
        urllib.request.urlopen(  # noqa: S310 — the scheme is checked above
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response,
        destination.open("wb") as handle,
    ):
        while chunk := response.read(_CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                raise ValueError(f"{url}: refused past {max_bytes} bytes")
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()
