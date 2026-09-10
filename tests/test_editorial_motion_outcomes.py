"""Exact attempt replay is distinct from permanent transport-failure caching.

The matrix-kit replay and the retired moment-editor comparisons stay on the probe branch.
"""

import hashlib
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from immich_memories.analysis import editorial_motion_facts as motion
from immich_memories.analysis.editorial_case import Case
from immich_memories.analysis.editorial_motion_outcomes import (
    REFERENCE_NAME,
    SCHEMA,
    MotionOutcomeReplay,
)
from tests.test_editorial_demanded_motion import _assets, _carrier


def replay_from_output(output) -> MotionOutcomeReplay:
    """Read back the reference an attempt wrote.

    The product only writes this file (`as_record`); picking it up again is what
    a replay does by hand, so the reader lives here with its one caller.
    """
    from pathlib import Path

    record = json.loads((Path(output) / REFERENCE_NAME).read_text())
    assert set(record) == {"schema", "path", "sha256"}
    assert record["schema"] == SCHEMA
    replay = MotionOutcomeReplay(Path(record["path"]), record["sha256"])
    replay.read()
    return replay


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    assets = _assets(5)
    calls = []
    failed = {"video-2"}

    class Client:
        def __init__(self, **_config):
            pass

        def get_video_playback(self, video):
            calls.append(video)
            if video in failed:
                raise TimeoutError("temporary playback failure")
            return video.encode()

        def close(self):
            pass

    from immich_memories.api import sync_client

    monkeypatch.setattr(sync_client, "SyncImmichClient", Client)
    native = motion.DemandedMotionResolver
    monkeypatch.setattr(
        motion,
        "DemandedMotionResolver",
        lambda **kwargs: native(**kwargs, measure=lambda _p: {"residual": 2.0, "frames": 12}),
    )
    source = SimpleNamespace(
        case=Case(
            key="month",
            label="A month",
            product="month",
            ranges=(),
            target_seconds=60,
            brief="Make the memory",
        ),
        wall_bytes=b"fixed wall",
        moment_asset_ids={"F01": tuple(assets)},
        assets=assets,
        artifact_dir=tmp_path / "artifacts",
        bank_dir=tmp_path / "shared/derived",
        motion_outcome_replay=None,
        config=SimpleNamespace(
            immich=SimpleNamespace(url="unused", api_key="unused", api_version=None)
        ),
    )
    return source, calls, failed


def changed(source, **updates):
    return SimpleNamespace(**(vars(source) | updates))


def test_failure_is_exactly_replayed_before_later_global_success_and_fresh_attempt_retries(runtime):
    source, calls, failed = runtime
    carrier = _carrier("still-0", "still-1", "still-2")
    cold, cost = motion.production_motion_resolver(source)([carrier])
    reference = replay_from_output(source.artifact_dir)
    original_bytes = reference.path.read_bytes()
    assert calls == ["video-0", "video-1", "video-2"]
    assert cost["new_motion_downloads"] == 2 and cost["unavailable_sources"] == 1
    assert cold[0]["kind"] == "live-motion" and cold[0]["motion_evidence"]["available"] == 2
    with sqlite3.connect(source.bank_dir.parent / "demanded-motion.sqlite") as c:
        assert c.execute("SELECT COUNT(*) FROM demanded_motion_facts").fetchone()[0] == 2
    # A fresh normal attempt in the SAME product artifact directory retries the transient failure.
    failed.clear()
    fresh, fresh_cost = motion.production_motion_resolver(source)([carrier])
    assert calls == ["video-0", "video-1", "video-2", "video-2"]
    assert fresh_cost["new_motion_downloads"] == 1 and fresh[0]["motion_evidence"]["available"] == 3
    assert reference.path.read_bytes() == original_bytes  # Never overwrite historical evidence.
    assert replay_from_output(source.artifact_dir).path != reference.path

    # The old attempt remains exact even after this new success populated the shared bank.
    def forbidden(**_kwargs):
        raise AssertionError("explicit replay opened a network client")

    from immich_memories.api import sync_client

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sync_client, "SyncImmichClient", forbidden)
        warm_source = changed(
            source, artifact_dir=source.artifact_dir / "warm", motion_outcome_replay=reference
        )
        warm, warm_cost = motion.production_motion_resolver(warm_source)([carrier])
    assert warm == cold
    assert warm_cost["fetch_attempts"] == warm_cost["new_motion_downloads"] == 0
    assert warm_cost["outcome_replay_hits"] == 3 and warm_cost["unavailable_sources"] == 1
    assert "path" not in json.dumps(warm) and "sha256" not in json.dumps(warm)


def test_one_factory_preserves_multiple_completion_batches_and_empty_attempts(runtime):
    source, calls, _ = runtime
    resolve = motion.production_motion_resolver(source)
    one = [_carrier("still-0", "still-1", "still-2")]
    two = [_carrier("still-3", "still-4")]
    first, _ = resolve(one)
    second, _ = resolve(two)
    reference = replay_from_output(source.artifact_dir)
    assert len(reference.read()["units"]) == 2
    replay = motion.production_motion_resolver(changed(source, motion_outcome_replay=reference))
    count = len(calls)
    assert replay(one)[0] == first and replay(two)[0] == second
    assert len(calls) == count
    empty_source = changed(source, artifact_dir=source.artifact_dir / "empty")
    empty = motion.production_motion_resolver(empty_source)
    assert empty([])[0] == []
    ref = replay_from_output(empty_source.artifact_dir)
    assert ref.read()["units"] == {}
    assert (
        motion.production_motion_resolver(changed(empty_source, motion_outcome_replay=ref))([])[0]
        == []
    )


@pytest.mark.parametrize("mutation", ["video", "metadata", "wall", "case", "moment-members"])
def test_source_or_scope_change_rejects_replay_before_transport(runtime, mutation):
    source, calls, _ = runtime
    motion.production_motion_resolver(source)([_carrier("still-0", "still-1", "still-2")])
    reference = replay_from_output(source.artifact_dir)
    altered = changed(source, motion_outcome_replay=reference)
    if mutation in {"video", "metadata"}:
        altered.assets = dict(source.assets)
        altered.assets["still-2"] = source.assets["still-2"].model_copy(
            update=(
                {"live_photo_video_id": "new-video"}
                if mutation == "video"
                else {"is_favorite": True}
            )
        )
    elif mutation == "wall":
        altered.wall_bytes = b"changed wall"
    elif mutation == "case":
        altered.case = replace(source.case, target_seconds=120)
    else:
        altered.moment_asset_ids = {"F01": tuple(reversed(source.assets))}
    count = len(calls)
    with pytest.raises(ValueError, match="scope"):
        motion.production_motion_resolver(altered)
    assert len(calls) == count


def test_changed_retained_unit_membership_rejects_replay(runtime):
    source, calls, _ = runtime
    motion.production_motion_resolver(source)([_carrier("still-0", "still-1", "still-2")])
    ref = replay_from_output(source.artifact_dir)
    replay = motion.production_motion_resolver(changed(source, motion_outcome_replay=ref))
    count = len(calls)
    with pytest.raises(ValueError, match="retained unit membership"):
        replay([_carrier("still-0", "still-2")])
    assert len(calls) == count


@pytest.mark.parametrize("change", ["bytes", "schema", "partial", "measurement", "sample-limit"])
def test_tampered_or_incomplete_snapshot_and_changed_measurement_fail_closed(runtime, change):
    source, calls, _ = runtime
    carrier = _carrier("still-0", "still-1", "still-2")
    motion.production_motion_resolver(source)([carrier])
    ref = replay_from_output(source.artifact_dir)
    record = ref.read()
    count = len(calls)
    if change == "bytes":
        ref.path.write_bytes(ref.path.read_bytes() + b" ")
    elif change == "measurement":
        with sqlite3.connect(source.bank_dir.parent / "demanded-motion.sqlite") as c:
            c.execute("UPDATE demanded_motion_facts SET facts_json=?", ('{"residual":0.1}',))
    else:
        if change == "schema":
            record["unsupported"] = True
        elif change == "sample-limit":
            record["scope"]["sample_limit"] = 2
        else:
            outcomes = next(iter(record["units"].values()))["outcomes"]
            outcomes.pop(next(iter(outcomes)))
        payload = json.dumps(record).encode()
        path = source.artifact_dir / "explicit-invalid-copy.json"
        path.write_bytes(payload)
        ref = MotionOutcomeReplay(path, hashlib.sha256(payload).hexdigest())
    with pytest.raises(ValueError):
        motion.production_motion_resolver(changed(source, motion_outcome_replay=ref))([carrier])
    assert len(calls) == count


def test_success_cache_remains_shared_across_fresh_attempts(runtime):
    source, calls, failed = runtime
    failed.clear()
    cold, _ = motion.production_motion_resolver(source)([_carrier("still-0", "still-1")])
    warm, cost = motion.production_motion_resolver(source)([_carrier("still-0", "still-1")])
    assert warm == cold and len(calls) == 2
    assert cost["cache_hits"] == 2 and cost["fetch_attempts"] == 0


def test_known_wrapped_status_is_preserved_without_inventing_unknown_status(runtime, monkeypatch):
    from immich_memories.api import sync_client
    from immich_memories.api.immich import ImmichAPIError

    source, _calls, _failed = runtime

    class UnavailableClient:
        def __init__(self, **_kwargs):
            pass

        def get_video_playback(self, _video):
            raise ImmichAPIError("service unavailable", status_code=503)

        def close(self):
            pass

    monkeypatch.setattr(sync_client, "SyncImmichClient", UnavailableClient)
    result, cost = motion.production_motion_resolver(source)([_carrier("still-0")])
    journal = replay_from_output(source.artifact_dir).read()
    outcome = next(iter(next(iter(journal["units"].values()))["outcomes"].values()))
    assert outcome == {
        "status": "unavailable",
        "error_type": "ImmichAPIError",
        "phase": "fetch",
        "http_status": 503,
    }
    assert result[0]["kind"] == "live-still" and cost["unavailable_sources"] == 1


def test_empty_native_sample_set_keeps_the_still_and_is_exactly_replayable(runtime):
    source, calls, _failed = runtime
    source.assets = {
        "still-0": source.assets["still-0"].model_copy(update={"live_photo_video_id": None})
    }
    source.moment_asset_ids = {"F01": ("still-0",)}
    carrier = _carrier("still-0")
    cold, metrics = motion.production_motion_resolver(source)([carrier])
    reference = replay_from_output(source.artifact_dir)
    assert len(reference.read()["units"]) == 1
    warm, _ = motion.production_motion_resolver(changed(source, motion_outcome_replay=reference))(
        [carrier]
    )
    assert warm == cold and cold[0]["kind"] == "live-still"
    assert calls == [] and metrics["sampled_sources"] == 0
