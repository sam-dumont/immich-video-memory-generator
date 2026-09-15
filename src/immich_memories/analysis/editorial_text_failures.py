"""Typed, replayable exhaustion of a bounded text-completion attempt."""

from __future__ import annotations

from immich_memories.analysis.provider_failure import provider_failure

FAILURE_SCHEMA = "bounded-text-completion-v1"
STAGE_CALL_SCHEMA = "stage-call-failure-v1"


def elapsed_label(seconds: float) -> str:
    """How long a call ran, rounded the way an operator reads a run log."""
    whole = int(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m{whole % 60}s"


class StageCallFailure(RuntimeError):
    """A stage's model call ended on something other than an answer.

    A dropped connection used to reach the CLI as `Error: ` and nothing else: httpx
    raises a read error carrying an empty message, and nothing between the socket and
    the terminal had a name for what had happened. Measured 2026-09-14: a local server
    restarted mid-request, the run ended nine minutes later on that empty line, and
    calls/ held the request with no response and no failure beside it.
    """

    def __init__(self, stage: str, *, call: int, cause: BaseException, seconds: float) -> None:
        self.stage = stage
        self.call = call
        self.seconds = seconds
        self.error_type = f"{type(cause).__module__}.{type(cause).__name__}"
        # Text stages attach no pictures, so a refusal here is never about an image.
        self.refusal = provider_failure(cause, images_attached=False)
        detail = self.refusal.message if self.refusal else str(cause).strip()
        super().__init__(
            f"{stage}: the model call failed after {elapsed_label(seconds)} at call {call}, "
            f"with {call - 1} already answered ({self.error_type})"
            + (f": {detail}" if detail else "")
        )

    def as_record(self):
        return {
            "schema_version": STAGE_CALL_SCHEMA,
            "stage": self.stage,
            "call": self.call,
            "answered_calls": self.call - 1,
            "error_type": self.error_type,
            "wall_seconds": round(self.seconds, 3),
            "error": str(self),
            **(self.refusal.as_record() if self.refusal else {}),
        }


class TextCompletionFailure(ValueError):
    """No usable answer after the existing two attempts; callers may explicitly fall back."""

    def __init__(self, attempts: list[dict], *, cache_hit: bool = False):
        super().__init__("text completion failed after bounded recovery: " + attempts[-1]["error"])
        self.attempts = attempts
        self.cache_hit = cache_hit

    def as_record(self):
        return {"schema_version": FAILURE_SCHEMA, "attempts": self.attempts}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or record.get("schema_version") != FAILURE_SCHEMA:
            return None
        attempts = record.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 2:
            return None
        for row in attempts:
            if not _is_replayable_attempt(row):
                return None
        return cls(attempts, cache_hit=True)


# What the provider said it billed for the reply. Absent from a record written
# before the reader budgeted for reasoning, and from any transport that reports
# no usage, so it is read where it is there and never required.
_BILLING_FIELDS = {"finish_reason", "completion_tokens", "reasoning_tokens"}
_REQUIRED_FIELDS = {"outcome", "raw", "max_tokens", "error"}


def _is_replayable_attempt(row) -> bool:
    if not isinstance(row, dict) or not _REQUIRED_FIELDS <= set(row) <= (
        _REQUIRED_FIELDS | _BILLING_FIELDS
    ):
        return False
    if row["outcome"] not in ("incomplete", "invalid_json"):
        return False
    if not isinstance(row["raw"], str) or not isinstance(row["max_tokens"], int):
        return False
    if not isinstance(row["error"], str) or not row["error"]:
        return False
    return row["max_tokens"] > 0
