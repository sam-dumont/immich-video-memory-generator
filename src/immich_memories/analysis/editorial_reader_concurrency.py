"""Bound independent reader jobs while committing their audits in source order."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory

from immich_memories.operations.cancellation import check_cancelled


def reader_map(judge, work, items):
    """Only production judges opt into parallel reads; other ports stay sequential."""
    run = getattr(judge, "map_independent", None)
    return run(work, items) if run is not None else [work(judge, item) for item in items]


def _run(work, judge, item):
    check_cancelled()
    return work(judge, item)


def _merge_audit(parent, child):
    offset = len(parent.calls)
    destination = parent.out / "calls"
    destination.mkdir(mode=0o700, exist_ok=True)
    for path in sorted((child.out / "calls").glob("*")):
        number, suffix = path.name.split("-", 1)
        path.replace(destination / f"{offset + int(number):02d}-{suffix}")
    parent.calls.extend(child.calls)


def run_reader_jobs(judge, work, items):
    """Give each concurrent job its own recorder, then merge after workers have stopped."""
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge

    limit = judge.config.llm.reader_concurrency
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
        futures = []
        try:
            for child, item in zip(children, items, strict=True):
                futures.append(pool.submit(copy_context().run, _run, work, child, item))
            return [future.result() for future in futures]
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            for child in children:
                _merge_audit(judge, child)
