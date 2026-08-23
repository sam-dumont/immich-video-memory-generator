"""Telling a model that could not answer from a bug in the code that asked.

Both LLM judgement calls in selection catch broadly on purpose. The pass is
fail-open by design: an unreachable model must not be able to gut a memory or
turn every day of a library into an ordinary one.

The cost of that catch is that it swallows our own mistakes with the same
silence. A keyword argument that did not match a callee's signature raised
TypeError inside the ask, the catch read it as "the model could not answer",
and the caller carried on with a verdict nobody produced. Across a scan the
visible result is zero special days found, or nothing ever dropped from a cut,
with no error anywhere — a wrong answer that looks exactly like a right one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Named rather than inferred, and inverted on purpose: anything unrecognised
# keeps failing open, so a transport error nobody anticipated stays quiet
# instead of becoming a crash. Enumerating the transport side instead would
# risk missing an httpx subclass and breaking the resilience these catches
# exist to provide.
_OUR_OWN_BUGS = (TypeError, AttributeError, NameError, KeyError, IndexError)


def stop_if_this_is_our_bug(exc: Exception, doing: str) -> None:
    """Let a bug in this code stop the run; let an unreachable model pass.

    Raised as well as logged. A bug here is the same bug on every clip and
    every day, so swallowing it does not degrade the result — it produces a
    complete, confident, empty one. Failing at the first call costs seconds
    and says why.

    The ERROR line carries the traceback so the diagnosis survives a catch
    added upstream later, which is exactly how this hid the first time.
    """
    if isinstance(exc, _OUR_OWN_BUGS):
        logger.error("The %s hit a bug in our own code", doing, exc_info=exc)
        raise exc
