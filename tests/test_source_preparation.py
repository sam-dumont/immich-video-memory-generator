"""Source preparation overlaps slow inputs without reordering the final film."""

from contextlib import contextmanager
from threading import Event, get_ident

from immich_memories.processing.source_preparation import prepare_sources


def test_fast_source_finishes_while_slow_source_is_still_waiting():
    release = Event()
    opened, closed = [], []

    # WHY: The client is the external resource; each worker must own and close its own.
    @contextmanager
    def client():
        owner = get_ident()
        opened.append(owner)
        try:
            yield owner
        finally:
            closed.append(get_ident())

    def prepare(owner, item):
        assert owner == get_ident()
        if item == "slow":
            assert release.wait(timeout=5)
        return item.upper()

    ready = prepare_sources(["slow", "fast"], client=client, prepare=prepare, workers=2)
    try:
        assert next(ready) == (1, "FAST")
    finally:
        release.set()
    assert list(ready) == [(0, "SLOW")]
    assert sorted(opened) == sorted(closed)
    assert len(set(opened)) == 2


def test_shared_source_is_downloaded_once_across_preparation_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from immich_memories.processing.download_coordinator import DownloadCoordinator, DownloadTarget

    calls = []
    source = tmp_path / "source.mov"
    source.write_bytes(b"fixture")

    # WHY: The download writes are replaced; both workers use the real coordinator.
    def download(_client, asset):
        calls.append(asset.id)
        return source

    coordinator = DownloadCoordinator(lambda: None, None, 2, download_operation=download)
    target = DownloadTarget("shared")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: coordinator.sources_for(None, [target]), range(2)))
    assert [result["shared"].path for result in results] == [source, source]
    assert calls == ["shared"]


def test_client_cleanup_failure_is_reported_after_the_last_source():
    import pytest

    @contextmanager
    def client():
        yield None
        raise OSError("client cleanup failed")

    ready = prepare_sources(["only"], client=client, prepare=lambda _, item: item, workers=1)
    assert next(ready) == (0, "only")
    with pytest.raises(OSError, match="client cleanup failed"):
        next(ready)


def test_closing_a_backpressured_iterator_stops_admission_and_closes_clients():
    from threading import Lock

    saturated = Event()
    lock = Lock()
    prepared, opened, closed = [], [], []

    @contextmanager
    def client():
        owner = get_ident()
        opened.append(owner)
        try:
            yield owner
        finally:
            closed.append(get_ident())

    def prepare(owner, item):
        assert owner == get_ident()
        with lock:
            prepared.append(item)
            # One delivered result, two queued results, two producers blocked on publish.
            if len(prepared) == 5:
                saturated.set()
        return item

    ready = prepare_sources(list(range(100)), client=client, prepare=prepare, workers=2)
    try:
        next(ready)
        assert saturated.wait(timeout=5)
    finally:
        ready.close()
    assert len(prepared) == 5
    assert sorted(opened) == sorted(closed)
    assert len(opened) == 2


def test_preparation_failure_closes_all_worker_resources():
    import pytest

    closed = []

    @contextmanager
    def client():
        try:
            yield None
        finally:
            closed.append(get_ident())

    def prepare(_, item):
        raise RuntimeError("cannot prepare source")

    with pytest.raises(RuntimeError, match="cannot prepare source"):
        list(prepare_sources([1, 2], client=client, prepare=prepare, workers=2))
    assert len(closed) == 2
