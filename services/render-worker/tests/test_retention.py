"""Job expiry preserves active work and releases old terminal records."""

import time
from uuid import uuid4


def test_only_terminal_jobs_expire_and_old_request_ids_can_be_reused(monkeypatch):
    from immich_memories_render_worker.store import MemoryJobRepository

    now = [100.0]
    # WHY: advance the external clock without holding CI for a retention interval.
    monkeypatch.setattr(time, "time", lambda: now[0])
    store = MemoryJobRepository(2, 60)
    ready_id, running_id = uuid4(), uuid4()
    store.admit(ready_id, "ready-cut", "one")
    store.admit(running_id, "running-cut", "two")
    store.update(ready_id, state="ready")
    store.update(running_id, state="running")
    now[0] += 61
    assert set(store.expire()) == {ready_id}
    assert store.get(running_id).state == "running"
    status, fresh = store.admit(ready_id, "new-cut", "three")
    assert fresh
    assert status.memory_key == "new-cut"
