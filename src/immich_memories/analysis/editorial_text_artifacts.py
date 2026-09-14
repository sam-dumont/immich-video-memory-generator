"""Private evidence for the pre-planner period request, before semantic validation."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from immich_memories.analysis.llm_providers import resolved_llm_config
from immich_memories.analysis.llm_text_identity import text_model_identity
from immich_memories.config_models_llm import LLMConfig
from immich_memories.security import write_secret_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextPromptArtifacts:
    directory: Callable[[], Path]
    stage: str

    def start(
        self,
        prompt: str,
        config: LLMConfig,
        *,
        max_tokens: int,
        timeout_seconds: int,
        thinking: bool,
        transport: str = "realtime",
    ) -> tuple[Path, dict] | None:
        """Write before querying; resolving the directory here follows the active attempt."""
        try:
            directory = self.directory() / "pre-planner-calls"
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            stem = directory / f"{self.stage}-{uuid4().hex}"
            record = {
                "schema": "pre-planner-text-call-v1",
                "stage": self.stage,
                "started_at": datetime.now(UTC).isoformat(),
                "status": "started",
                "parser_validated": False,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "request": {
                    "model_identity": text_model_identity(
                        resolved_llm_config(config), thinking=thinking
                    ),
                    "max_tokens": max_tokens,
                    "timeout_seconds": timeout_seconds,
                    "thinking": thinking,
                    "temperature": 0.0,
                    "require_complete": True,
                    "application_cache": False,
                    # Which wire carried this answer. A run that read half its
                    # episodes from a batch and half live has both words here,
                    # and the summary's counts have to agree with them.
                    "transport": transport,
                },
            }
            write_secret_file(Path(f"{stem}.request.private.txt"), prompt)
            write_secret_file(Path(f"{stem}.outcome.private.json"), json.dumps(record, indent=2))
            return stem, record
        except Exception as exc:  # Diagnostics cannot change the answer or retry policy.
            logger.warning("Could not record private text request (%s)", type(exc).__name__)
            return None

    @staticmethod
    def finish(
        call: tuple[Path, dict] | None,
        *,
        raw: str | None = None,
        error: BaseException | None = None,
        billed: dict | None = None,
    ) -> None:
        """Close one call's record. `billed` is the provider's own account of the reply.

        Without it a starved read is indistinguishable from a refused one: both
        land as `response_chars: 0`, and only the token split says which budget
        actually ran out.
        """
        if call is None:
            return
        stem, record = call
        try:
            if billed:
                record["reply"] = billed
            if error is not None:
                raw = getattr(error, "raw", None)
                record.update(
                    status="raised", error_type=type(error).__name__, error=str(error)[:300]
                )
            else:
                record["status"] = "complete_transport"
            if isinstance(raw, str):
                write_secret_file(Path(f"{stem}.response.private.txt"), raw)
                record.update(
                    response_chars=len(raw),
                    response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                )
            record["finished_at"] = datetime.now(UTC).isoformat()
            write_secret_file(Path(f"{stem}.outcome.private.json"), json.dumps(record, indent=2))
        except Exception as exc:
            logger.warning("Could not record private text response (%s)", type(exc).__name__)
