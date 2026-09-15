"""Bound independent reader jobs while committing their audits in source order.

A job that has already reached the provider is not cancelled when a sibling fails.
There is no way to stop a thread mid-request, the call is billed the moment it is
sent, and its answer is banked by judgment key on arrival -- so letting it finish
costs the same as killing it and leaves the re-run one free replay better off.
Only jobs that have not started are dropped.
"""

import logging
import sys
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory

from immich_memories.analysis.llm_providers import reader_concurrency
from immich_memories.operations.cancellation import PipelineCancelled, check_cancelled

logger = logging.getLogger(__name__)


def reader_map(judge, work, items):
    """Only production judges opt into parallel reads; other ports stay sequential."""
    run = getattr(judge, "map_independent", None)
    return run(work, items) if run is not None else [work(judge, item) for item in items]


def _run(work, judge, item):
    check_cancelled()
    return work(judge, item)


def iter_reader_jobs(reader, work, items, *, limit):
    """Yield item/answer pairs in source order; save paid answers before cancellation."""
    if limit == 1 or len(items) < 2:
        for item in items:
            yield item, _run(work, reader, item)
        return
    pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="episode-reader")
    futures: list[Future] = []
    try:
        for item in items:
            futures.append(pool.submit(copy_context().run, _run, work, reader, item))
        yield from _ordered_reader_answers(items, futures)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        _name_every_failure(futures)


def _ordered_reader_answers(items, futures):
    """Drain successful siblings before propagating a stop; cancel only pending work."""
    cancelled = None
    for item, future in zip(items, futures, strict=True):
        try:
            answer = future.result()
        except PipelineCancelled as exc:
            cancelled = exc
            for pending in futures:
                pending.cancel()
        except CancelledError:
            continue
        else:
            yield item, answer
    if cancelled is not None:
        raise cancelled


def _merge_audit(parent, child):
    offset = len(parent.calls)
    destination = parent.out / "calls"
    destination.mkdir(mode=0o700, exist_ok=True)
    for path in sorted((child.out / "calls").glob("*")):
        number, suffix = path.name.split("-", 1)
        path.replace(destination / f"{offset + int(number):02d}-{suffix}")
    parent.calls.extend(child.calls)


def _name_every_failure(futures: list[Future]) -> None:
    """Retrieve every job's exception, not only the one that propagates.

    `Future` drops an exception nobody asked for, and unlike asyncio it says nothing
    when it does. Two jobs failing for two reasons reported one, and the operator had
    no way to know a second cause existed. The first still propagates with its own
    type, so callers that catch on type are unaffected; the rest ride with it as notes
    and are logged.
    """
    raised = sys.exc_info()[1]
    causes = [
        error
        for future in futures
        if future.done() and not future.cancelled()
        for error in (future.exception(),)
        if error is not None and error is not raised
    ]
    for cause in causes:
        logger.warning("another concurrent reader job failed: %r", cause)
    if raised is not None:
        for cause in causes:
            raised.add_note(f"a concurrent reader job also failed: {cause!r}")


def run_reader_jobs(judge, work, items):
    """Give each concurrent job its own recorder, then merge after workers have stopped."""
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge

    limit = reader_concurrency(judge.config.llm)
    if limit == 1 or len(items) < 2:
        return [work(judge, item) for item in items]
    judge.out.mkdir(parents=True, mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix=".reader-", dir=judge.out) as scratch:
        children = []
        for number in range(len(items)):
            out = Path(scratch) / str(number)
            out.mkdir(mode=0o700)
            children.append(StructureTextJudge(judge.config, out, cache_path=judge.cache_path))
        pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="reader")
        futures: list[Future] = []
        try:
            for child, item in zip(children, items, strict=True):
                futures.append(pool.submit(copy_context().run, _run, work, child, item))
            return [future.result() for future in futures]
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            _name_every_failure(futures)
            for child in children:
                _merge_audit(judge, child)
