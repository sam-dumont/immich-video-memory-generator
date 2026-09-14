"""What a provider's error status means: refused, come back later, or a bad credential.

A hosted model that will not take images answers 400 in 300 milliseconds. Measured
2026-09-14: 39 calls answered, the fortieth carried an 800 px tile, and the run ended
on the provider's "rejected as malformed" with 22 minutes of reading discarded and no
record of which call had been refused. A failure belongs to its call.

The four meanings must not be collapsed, because they want opposite handling:

- A rate limit is transient and must be waited out. A matrix cell that quietly produced
  fewer picture facts because one provider was shedding load would read as a model
  difference.
- So is a provider that is simply down. Measured the same day: a 25-minute February run
  ended at planner call 188 on one 503, throwing away 187 good answers and EUR 0.05 of
  spend, and the same host answered three 503s in a row on another model an hour later.
  A 500 is murkier than a 503 -- it can be the request rather than the weather -- so it
  is asked again once and no more.
- A rejected credential is configuration and must be loud: nobody wants a whole run's
  worth of missing facts because a key expired.
- Only a refused payload is this call's own business, and only it is lost quietly.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

# `llm_wire.ensure_success` appends the provider's own code and message after this marker.
_PROVIDER_SAID = " - provider said "

REFUSED = "provider_refused"
RATE_LIMITED = "rate_limited"
CREDENTIAL_REJECTED = "credential_rejected"
UNAVAILABLE = "provider_unavailable"

_CREDENTIAL_STATUS = frozenset({401, 403})
# A gateway in front of a busy model: the weather, not the request.
_UNAVAILABLE_STATUS = frozenset({502, 503, 504})

# A provider can name a wait far beyond any sensible run; honour it, but bounded.
MAX_RETRY_AFTER_SECONDS = 120.0
# How much of the wait is spread, as a fraction added on top of it. A bounded
# wait stays bounded: at most 1.5 x MAX_RETRY_AFTER_SECONDS.
RETRY_JITTER = 0.5
# A throttled or downed provider is asked again rather than costing a stage its answer.
RETRY_ATTEMPTS = 4
# A bare 500 does not say whose fault it was. One more try, then it is the answer.
SERVER_ERROR_ATTEMPTS = 2


def retry_after_seconds(response: httpx.Response) -> float | None:
    """The wait the provider asked for, in seconds, or None when it named none.

    Both spellings of Retry-After are in the wild: a delay in seconds, and an
    HTTP date. A header we cannot read is the same as no header.
    """
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    with suppress(ValueError):
        return min(max(float(value), 0.0), MAX_RETRY_AFTER_SECONDS)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return min(max((when - datetime.now(UTC)).total_seconds(), 0.0), MAX_RETRY_AFTER_SECONDS)


@dataclass(frozen=True)
class ProviderFailure:
    """One error status, told apart by what it means for the next attempt."""

    status_code: int
    message: str
    images_attached: bool
    kind: str
    retry_after: float | None = None
    # How many times this call is worth making in total, this one included; 1 means
    # asking again cannot change the answer.
    attempts: int = 1

    @property
    def credential(self) -> bool:
        """Whether every later call will fail the same way until a human acts."""
        return self.kind == CREDENTIAL_REJECTED

    def as_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "reason": self.kind,
            "status_code": self.status_code,
            "provider_message": self.message,
            "images_attached": self.images_attached,
        }
        if self.retry_after is not None:
            record["retry_after_seconds"] = self.retry_after
        return record


class ProviderCredentialRejected(RuntimeError):
    """The provider refused the credential, not the payload: every call will fail."""

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(
            f"the reader's provider rejected its credential (HTTP {failure.status_code}): "
            f"{failure.message}. Every call fails the same way until the API key or the "
            "model access is fixed, so the run stops here rather than reading without it."
        )
        self.failure = failure


class ThrottleGate:
    """One provider pause shared by every call in flight.

    Overlapping readers turn one 429 into N. Each used to sleep its own span and
    they all came back at once, at N times the request rate the provider had just
    refused, which is the shape that keeps a throttle closed. A throttle answered
    anywhere holds the whole group until the window the provider named has passed.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until = 0.0

    def hold(self, seconds: float) -> None:
        """Shut the gate for at least this long, never shortening a longer hold."""
        with self._lock:
            self._until = max(self._until, self._clock() + seconds)

    def pause(self) -> float:
        """Seconds this caller owes the throttle before it may ask."""
        with self._lock:
            return max(0.0, self._until - self._clock())


# Process-wide, because a rate limit belongs to the API key and every reader here
# is spending the same one.
THROTTLE = ThrottleGate()


def retry_wait(failure: ProviderFailure | None, attempt: int) -> float | None:
    """Seconds to wait before asking again, or None when asking again cannot help.

    The provider's own Retry-After wins over our backoff: it knows when its window
    reopens and we are only guessing. The jitter is added above that wait and never
    below it, so honouring the header stays honouring it; what it buys is that four
    throttled calls do not come back in the same millisecond.
    """
    if failure is None or attempt >= failure.attempts:
        return None
    base = failure.retry_after if failure.retry_after is not None else 2.0 * attempt
    return base + random.uniform(0.0, base * RETRY_JITTER)  # noqa: S311 - spread, not a secret


def group_wait(failure: ProviderFailure, wait: float) -> float:
    """This caller's wait, made the group's and widened to any longer hold already on it.

    A rate limit belongs to the key, not to the call that happened to hit it, so the
    calls that have not been refused yet wait it out too -- see `THROTTLE.pause()`,
    which they consult before their first attempt. Anything else is this call's own
    weather and nobody else needs to sit it out.
    """
    if failure.kind != RATE_LIMITED:
        return wait
    THROTTLE.hold(wait)
    return THROTTLE.pause()


def announce_retry(
    logger: logging.Logger, failure: ProviderFailure, wait: float, attempt: int
) -> None:
    """A run that goes quiet for a minute must say it is waiting, not look hung."""
    logger.warning(
        "The reader's provider answered %s (HTTP %s); waiting %.0fs before attempt %s of %s: %s",
        failure.kind,
        failure.status_code,
        wait,
        attempt + 1,
        failure.attempts,
        failure.message,
    )


def _kind_and_attempts(status_code: int, named_a_wait: bool) -> tuple[str, int]:
    """A named Retry-After is the provider saying "come back", whatever status carries
    it; some hosts spell a spent quota 403."""
    if status_code == 429 or named_a_wait:
        return RATE_LIMITED, RETRY_ATTEMPTS
    if status_code in _UNAVAILABLE_STATUS:
        return UNAVAILABLE, RETRY_ATTEMPTS
    if status_code == 500:
        return UNAVAILABLE, SERVER_ERROR_ATTEMPTS
    if status_code in _CREDENTIAL_STATUS:
        return CREDENTIAL_REJECTED, 1
    return (UNAVAILABLE, 1) if status_code >= 500 else (REFUSED, 1)


def provider_failure(error: BaseException, *, images_attached: bool) -> ProviderFailure | None:
    """What one 4xx or 5xx means, or None when the failure was not the provider answering.

    `images_attached` is the one fact the status line never has and the operator always
    wants: a model that takes text and refuses pictures looks identical otherwise.
    """
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code < 400:
        return None
    response = error.response
    wait = retry_after_seconds(response)
    kind, attempts = _kind_and_attempts(response.status_code, wait is not None)
    said = str(error).split(_PROVIDER_SAID, 1)
    return ProviderFailure(
        status_code=response.status_code,
        message=(said[1] if len(said) == 2 else str(error).splitlines()[0]).strip(),
        images_attached=images_attached,
        kind=kind,
        retry_after=wait,
        attempts=attempts,
    )
