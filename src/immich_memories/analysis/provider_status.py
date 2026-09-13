"""Name the provider that stopped answering, the moment it stops.

The retry in `llm_query` is right and stays: one dropped connection once
killed a 26-minute run. What was missing is that nobody was told. Until this
module, a reader that went away meant six silent seconds and then a dead run,
with the endpoint named nowhere on either surface.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from immich_memories.analysis.llm_query import TRANSPORT_RETRIES, LLMTransportAttempt
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cut_progress import StageUpdate, announce_stage


def endpoint_of(config: LLMConfig) -> str:
    """The host and port a person would check, without the path or the scheme."""
    parts = urlsplit(config.base_url)
    return parts.netloc or config.base_url


def watch_provider(role: str, config: LLMConfig) -> Callable[[LLMTransportAttempt], None]:
    """A transport observer that announces each dropped connection as a stage."""
    endpoint = endpoint_of(config)

    def observe(attempt: LLMTransportAttempt) -> None:
        if attempt.outcome != "connection_error":
            return
        if attempt.attempt >= TRANSPORT_RETRIES:
            # The run dies after this one; say what to do, on the row the person is watching.
            announce_stage(
                StageUpdate(
                    f"Gave up on the {role} at {endpoint} after {TRANSPORT_RETRIES} dropped "
                    "connections: fix the server and cut again"
                )
            )
            return
        announce_stage(
            StageUpdate(
                f"Waiting for the {role} at {endpoint}: connection dropped, "
                f"retry {attempt.attempt} of {TRANSPORT_RETRIES}"
            )
        )

    return observe
