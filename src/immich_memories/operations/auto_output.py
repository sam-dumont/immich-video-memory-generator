"""Private child transcripts, separate from the attempt's short error summary."""

import logging
from pathlib import Path
from uuid import UUID

from immich_memories.security import sanitize_error_message, write_secret_file

# What `generate` prints, and exits 0 on, when a period was read and holds nothing worth a
# film. Automation reads a child's transcript for it.
NOTHING_WORTH_A_FILM = "Nothing worth a film"


def output_log_path(cache_dir: Path, attempt_id: str) -> Path:
    """Locate an automation transcript by its durable attempt identity."""
    return cache_dir / "automation-output" / f"{UUID(attempt_id)}.private.log"


def retain_output(
    cache_dir: Path, attempt_id: str, stdout: str, stderr: str, secrets: tuple[str, ...]
) -> None:
    """Keep both complete streams at mode 0600, with configured credentials removed."""
    text = sanitize_error_message(f"stdout:\n{stdout}\n\nstderr:\n{stderr}\n")
    for secret in secrets:
        text = text.replace(secret, "***") if secret else text
    try:
        write_secret_file(output_log_path(cache_dir, attempt_id), text)
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not retain child output for attempt %s", attempt_id
        )
