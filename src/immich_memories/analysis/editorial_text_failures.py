"""Typed, replayable exhaustion of a bounded text-completion attempt."""

FAILURE_SCHEMA = "bounded-text-completion-v1"


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


def _is_replayable_attempt(row) -> bool:
    if not isinstance(row, dict) or set(row) != {"outcome", "raw", "max_tokens", "error"}:
        return False
    if row["outcome"] not in ("incomplete", "invalid_json"):
        return False
    if not isinstance(row["raw"], str) or not isinstance(row["max_tokens"], int):
        return False
    if not isinstance(row["error"], str) or not row["error"]:
        return False
    return row["max_tokens"] > 0
