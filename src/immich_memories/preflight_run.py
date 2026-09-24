"""The host checks a cut has to pass before it reads a single picture.

Each of these used to surface late: a missing model at the heads, after the
previews and faces were fetched, and an output directory the container cannot
write at the very end, after the whole preparation and render. They are cheap
and local, so a run asks them first and refuses to start on any error.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from immich_memories.config import Config
from immich_memories.preflight import CheckResult, CheckStatus


def check_encoder(config: Config) -> CheckResult:
    """Report the digest-pinned DINOv2 export the eight context heads run on."""
    if not config.editorial.preparation.demands_models:
        return CheckResult(
            name="Encoder", status=CheckStatus.SKIPPED, message="Not required by metadata_only"
        )
    from immich_memories.analysis.editorial_preparation_heads import missing_encoder_message
    from immich_memories.triage.encoder import DINOV2_SMALL_ONNX_SHA256

    path = config.triage.encoder_path
    if not path.is_file():
        return CheckResult(
            name="Encoder",
            status=CheckStatus.ERROR,
            message="Pinned DINOv2 export missing",
            details=missing_encoder_message(path),
        )
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != DINOV2_SMALL_ONNX_SHA256:
        return CheckResult(
            name="Encoder",
            status=CheckStatus.ERROR,
            message="Not the pinned DINOv2 export",
            details=f"{path}: {digest[:12]} is not {DINOV2_SMALL_ONNX_SHA256[:12]}",
        )
    return CheckResult(
        name="Encoder",
        status=CheckStatus.OK,
        message="Pinned DINOv2 export verified",
        details=str(path),
    )


def check_detector_export(config: Config) -> CheckResult:
    """Report the digest-pinned sensitive-content export the flag detector runs on.

    It is checked here because the alternative is finding out during the cut:
    the detector worker is a separate process reached hours into preparation.
    """
    if not config.editorial.preparation.demands_models:
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.SKIPPED,
            message="Not required by metadata_only",
        )
    from immich_memories.analysis.editorial_preparation_detectors import (
        MARQO_ONNX_ID,
        MARQO_ONNX_SHA256,
        missing_marqo_message,
    )

    path = config.editorial.preparation.marqo_onnx_path
    if not path.is_file():
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.ERROR,
            message=f"Pinned {MARQO_ONNX_ID} export missing",
            details=missing_marqo_message(path),
        )
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != MARQO_ONNX_SHA256:
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.ERROR,
            message=f"Not the pinned {MARQO_ONNX_ID} export",
            details=f"{path}: {digest[:12]} is not {MARQO_ONNX_SHA256[:12]}",
        )
    return CheckResult(
        name="Sensitive-content detector",
        status=CheckStatus.OK,
        message="Pinned sensitive-content export verified",
        details=str(path),
    )


def _unwritable(directory: Path, error: OSError) -> CheckResult:
    # The Docker case: a bind-mounted ./output that the daemon created as root,
    # written by a container that runs as uid 1000.
    user = f"uid {os.getuid()}" if hasattr(os, "getuid") else "this user"
    return CheckResult(
        name="Output directory",
        status=CheckStatus.ERROR,
        message="Output directory is not writable",
        details=(
            f"{directory}: {error.strerror or error}. Make it writable by {user} "
            "(on Docker, create ./output before `docker compose up`, or run "
            "`sudo chown 1000:1000 output`)"
        ),
    )


def check_output_directory(directory: Path) -> CheckResult:
    """Create the output directory when it is missing, and prove a file can be written in it.

    The probe writes and removes a real temporary file: permission bits and
    ``os.access`` both lie on network shares and root-squashed bind mounts.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-"):
            pass
    except OSError as error:
        return _unwritable(directory, error)
    return CheckResult(
        name="Output directory",
        status=CheckStatus.OK,
        message="Output directory is writable",
        details=str(directory),
    )


def run_blockers(config: Config, *, output_directory: Path | None) -> list[CheckResult]:
    """Return the checks that would stop this run, before any Immich call or preparation.

    ``output_directory`` is None for a run that writes no film (``--no-render``).
    """
    # A producer the inference service answers for needs no local file here; its
    # outage is reported by the preparation, which knows about the fallback.
    served = set(config.inference.producers) if config.inference.enabled else set()
    checks = []
    if "heads" not in served:
        checks.append(check_encoder(config))
    if "nsfw_marqo" not in served:
        checks.append(check_detector_export(config))
    if output_directory is not None:
        checks.append(check_output_directory(output_directory))
    return [check for check in checks if check.status is CheckStatus.ERROR]
