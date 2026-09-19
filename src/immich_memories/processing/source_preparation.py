"""Bounded source preparation with worker-owned resources and completion-order delivery."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from queue import Full, Queue
from threading import Event, Lock, Thread
from typing import Generic, TypeVar

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")
_Client = TypeVar("_Client")


def prepare_sources(
    items: Sequence[_Item],
    *,
    client: Callable[[], AbstractContextManager[_Client]],
    prepare: Callable[[_Client, _Item], _Result],
    workers: int,
) -> Iterator[tuple[int, _Result]]:
    """Yield completed sources with their original positions, never a download barrier.

    Each worker owns one client for its lifetime. At most ``workers`` completed
    results wait while at most ``workers`` sources are being prepared. Closing the
    iterator cancels admission, drains workers and closes their clients in-thread.
    """
    if workers < 1:
        raise ValueError("source preparation needs at least one worker")
    if not items:
        return
    count = min(workers, len(items))
    pool: _SourceWorkers[_Item, _Client, _Result] = _SourceWorkers(items, client, prepare, count)
    threads = [Thread(target=pool.work, name=f"source-prepare-{i}") for i in range(count)]
    for thread in threads:
        thread.start()
    try:
        remaining = count
        while remaining:
            result = pool.ready.get()
            if result is None:
                remaining -= 1
                continue
            if isinstance(result, BaseException):
                raise result
            yield result
    finally:
        pool.stopped.set()
        for thread in threads:
            thread.join()


class _SourceWorkers(Generic[_Item, _Client, _Result]):
    """Own admission, backpressure and per-thread resource cleanup for one iterator."""

    def __init__(self, items, client, prepare, count):
        self.client = client
        self.prepare = prepare
        self.ready: Queue[tuple[int, _Result] | BaseException | None] = Queue(maxsize=count)
        self.pending = iter(enumerate(items))
        self.admission = Lock()
        self.stopped = Event()

    def publish(self, result):
        while not self.stopped.is_set():
            try:
                self.ready.put(result, timeout=0.1)
                return
            except Full:
                continue

    def work(self):
        try:
            with self.client() as resource:
                self.prepare_remaining(resource)
        except BaseException as exc:
            self.publish(exc)
        finally:
            self.publish(None)

    def prepare_remaining(self, resource):
        while not self.stopped.is_set():
            with self.admission:
                item = next(self.pending, None)
            if item is None:
                return
            index, source = item
            self.publish((index, self.prepare(resource, source)))
