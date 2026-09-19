"""What a run spent on the model, written where a report can add it up.

The end-of-run block is for a person at a terminal, so it rounds: `115.6k
prompt`. Multiply that by a price per million and the answer is wrong by
hundreds of tokens per run, in the one direction nobody checks. The setup
matrix publishes a cost column, so the digits have to survive somewhere, and
this is where.

It sits in the attempt directory beside the plan and the trace, because that is
the directory the matrix already reads a finished cell out of, and because the
run that wrote them is the run that spent the money.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.analysis.llm_metrics import LLMCounters

logger = logging.getLogger(__name__)

USAGE_FILE = "llm-usage.json"
SCHEMA_VERSION = 1

__all__ = ["USAGE_FILE", "write_llm_usage"]


def _usage_record(counters: LLMCounters) -> dict:
    """Every counter the run holds, unrounded, with the per-model split beside it."""
    with counters._lock:
        return {
            "schema_version": SCHEMA_VERSION,
            "calls": counters.calls,
            "cache_hits": counters.cache_hits,
            "prompt_tokens": counters.prompt_tokens,
            "cached_prompt_tokens": counters.cached_prompt_tokens,
            "completion_tokens": counters.completion_tokens,
            "reasoning_tokens": counters.reasoning_tokens,
            "truncated": counters.truncated,
            "wall_seconds": round(counters.wall_seconds, 3),
            "batch_calls": counters.batch_calls,
            "batch_prompt_tokens": counters.batch_prompt_tokens,
            "batch_completion_tokens": counters.batch_completion_tokens,
            "by_model": {name: asdict(spend) for name, spend in sorted(counters.by_model.items())},
        }


def write_llm_usage(attempt_dir: Path, counters: LLMCounters | None) -> None:
    """Record the run's model spend, unless it never asked a model anything.

    A run with no LLM configured -- the recommended NAS setup -- leaves no file
    at all, so a reader can tell "did not use a model" from "used one for free".
    """
    if counters is None or not (counters.calls or counters.cache_hits):
        return
    try:
        write_secret_file(
            attempt_dir / USAGE_FILE, json.dumps(_usage_record(counters), indent=2) + "\n"
        )
    except OSError:  # WHY: a full disk must not turn a finished film into a failed run
        logger.warning("Could not write the run's LLM usage record to %s", attempt_dir)
