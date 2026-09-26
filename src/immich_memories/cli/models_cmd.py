"""Fetch the pinned model artifacts an install needs before its first cut."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import click

from immich_memories.analysis.editorial_preparation_detectors import DETECTOR_SNAPSHOTS
from immich_memories.laya_checkpoints import LAYA_ONNX_NAME
from immich_memories.pinned_models import (
    ENCODER,
    LAYA_AUDIENCE,
    LAYA_AUDIENCE_ONNX,
    LAYA_MAX_BYTES,
    MARQO_ONNX,
    fetch_pinned_model,
)


def register_models_commands(cli_group: click.Group) -> None:
    """Register the `models` group that prepares an install's pinned artifacts."""

    @cli_group.group()
    def models() -> None:
        """Fetch the pinned model artifacts selection needs."""

    @models.command()
    @click.option("--force", is_flag=True, help="Re-download even when the file is already right")
    @click.option(
        "--detectors/--no-detectors",
        default=True,
        help="Also fetch the pinned detector export and warm the pinned detector snapshot",
    )
    @click.option(
        "--laya",
        is_flag=True,
        help="Fetch the Laya audience checkpoint even on the nas tier (gpu and full fetch it anyway)",
    )
    @click.pass_context
    def fetch(ctx: click.Context, force: bool, detectors: bool, laya: bool) -> None:
        """Download every pinned model artifact a first cut needs, in one command."""
        config = ctx.obj["config"]
        preparation = config.editorial.preparation
        _fetch_pinned(
            label="encoder",
            url=config.triage.encoder_url,
            destination=config.triage.encoder_path,
            sha256=ENCODER.sha256,
            force=force,
        )
        if laya or config.editorial.laya_audience:
            pin = (
                LAYA_AUDIENCE_ONNX
                if LAYA_ONNX_NAME
                in (
                    config.editorial.laya_checkpoint_url.rsplit("/", 1)[-1],
                    config.editorial.laya_checkpoint_path.name,
                )
                else LAYA_AUDIENCE
            )
            _fetch_pinned(
                label="laya audience",
                url=config.editorial.laya_checkpoint_url,
                destination=config.editorial.laya_checkpoint_path,
                sha256=pin.sha256,
                force=force,
                max_bytes=LAYA_MAX_BYTES,
            )
        if not detectors:
            return
        _fetch_pinned(
            label="detector nsfw_marqo",
            url=preparation.marqo_onnx_url,
            destination=preparation.marqo_onnx_path,
            sha256=MARQO_ONNX.sha256,
            force=force,
        )
        try:
            for repo in warm_detectors(preparation.detector_cache_dir):
                click.echo(f"detector: cached {repo}")
        except (ImportError, OSError, ValueError) as exc:
            click.echo(f"detectors: {exc}")
            raise SystemExit(1) from exc


def _fetch_pinned(
    *,
    label: str,
    url: str,
    destination: Path,
    sha256: str,
    force: bool,
    max_bytes: int | None = None,
) -> None:
    limit = {} if max_bytes is None else {"max_bytes": max_bytes}
    try:
        outcome = fetch_pinned_model(
            url=url, destination=destination, sha256=sha256, force=force, **limit
        )
    except (OSError, ValueError, urllib.error.URLError) as exc:
        click.echo(f"{label}: {exc}")
        raise SystemExit(1) from exc
    verb = "already present at" if outcome == "present" else "downloaded to"
    click.echo(f"{label}: {verb} {destination}")


def warm_detectors(cache_dir: str) -> list[str]:
    """Pull every pinned Hugging Face detector file into the cache the worker reads offline.

    Returns one ``repo@revision`` label per warmed snapshot. The worker runs with
    ``HF_HUB_OFFLINE=1`` unless `allow_model_downloads` is on, so this is what
    makes that default honest on a cold install.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ImportError(
            "the detectors need the editorial extra: pip install 'immich-memories[editorial]'"
        ) from exc

    resolved = str(Path(cache_dir).expanduser()) if cache_dir.strip() else None
    warmed = []
    for repo, revision, filenames in DETECTOR_SNAPSHOTS:
        for filename in filenames:
            hf_hub_download(repo, filename, revision=revision, cache_dir=resolved)
        warmed.append(f"{repo}@{revision[:8]}")
    return warmed
