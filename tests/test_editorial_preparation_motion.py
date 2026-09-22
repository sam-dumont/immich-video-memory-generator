"""One motion line per video, asked once of the caption seat, banked like a caption."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from PIL import Image

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_motion_facts import RESIDUAL_PRODUCER
from immich_memories.analysis.editorial_preparation_motion import (
    BankedMotionLines,
    banked_residuals,
    missing_motion,
    motion_sources,
    prepare_motion_lines,
    seat_asker,
)
from immich_memories.analysis.llm_metrics import collecting
from immich_memories.analysis.llm_usage_record import USAGE_FILE, write_llm_usage
from immich_memories.api.models import Asset, AssetType
from immich_memories.processing.playback_keyframes import SampledKeyframes
from immich_memories.store.cut_measurements import (
    open_cut_measurements,
    remember_motion_residual,
)
from immich_memories.store.editorial_preparation import initialize, private_database_path
from tests.test_editorial_preparation_captions import _CaptionServer


def picture(asset_id, *, kind=AssetType.IMAGE, live=None, name="IMG.JPG"):
    at = datetime(2024, 3, 2, tzinfo=UTC)
    return Asset(
        id=asset_id,
        type=kind,
        file_created_at=at,
        file_modified_at=at,
        updated_at=at,
        original_file_name=name,
        live_photo_video_id=live,
    )


def video(asset_id):
    return picture(asset_id, kind=AssetType.VIDEO, name="VID.MOV")


def frame(shade):
    buffer = io.BytesIO()
    Image.new("RGB", (48, 32), (shade, shade, shade)).save(buffer, "JPEG")
    return buffer.getvalue()


def sampled(*_args):
    return SampledKeyframes((frame(10), frame(90), frame(200)), (1.0, 4.0, 7.0), 8.0, 300, 4)


def answer(text):
    return json.dumps({"description": text})


class Seat:
    # WHY: the caption server is an external model; this replays its raw answers in order
    def __init__(self, *answers):
        self.answers = list(answers)
        self.strips = []
        self.lock = threading.Lock()

    def __call__(self, strip):
        with self.lock:
            self.strips.append(strip)
            reply = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def store(tmp_path):
    path = private_database_path(tmp_path / "annotations.sqlite")
    with sqlite3.connect(path) as connection:
        initialize(connection)
    return path


def produce(store, sources, *, sample=sampled, seat):
    with sqlite3.connect(store) as connection:
        return prepare_motion_lines(
            connection=connection,
            sources=sources,
            sample=sample,
            ask=seat,
            concurrency=1,
            check_cancelled=lambda: None,
            progress=lambda *_: None,
        )


def test_motion_retries_report_usage_before_validation_and_warm_reuse_is_free(store, tmp_path):
    reply = {
        "model": "served-motion-revision",
        "choices": [{"finish_reason": "stop", "message": {"content": answer("A person waves.")}}],
        "usage": {
            "prompt_tokens": 600,
            "completion_tokens": 80,
            "prompt_tokens_details": {"cached_tokens": 200},
            "completion_tokens_details": {"reasoning_tokens": 50},
        },
    }
    truncated = {**reply, "choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    server = _CaptionServer(None, replies=[truncated, reply])
    sources = motion_sources((video("one"),), residual_of=lambda _: None)
    ask = seat_asker(server.base_url, api_key="", timeout=5)
    try:
        with collecting() as usage:
            assert produce(store, sources, seat=ask).described == 1
        write_llm_usage(tmp_path, usage)
        with sqlite3.connect(store) as connection:
            remaining = missing_motion(connection, sources)
        with collecting() as warm:
            produce(store, remaining, seat=ask)
    finally:
        server.close()

    report = json.loads((tmp_path / USAGE_FILE).read_text())
    assert report["calls"] == 2
    assert report["by_stage"]["motion"]["calls"] == 2
    assert report["by_model"]["served-motion-revision"]["calls"] == 2
    assert report["prompt_tokens"] == 1200
    assert report["cached_prompt_tokens"] == 400
    assert report["completion_tokens"] == 160
    assert report["reasoning_tokens"] == 100
    assert report["usage_complete"] is True
    assert warm.calls == 0


@pytest.mark.parametrize(
    "provider_usage,unknown", [(None, 1), ({"prompt_tokens": 0, "completion_tokens": 0}, 0)]
)
def test_motion_usage_distinguishes_missing_counts_from_zero(tmp_path, provider_usage, unknown):
    reply = {
        "choices": [{"finish_reason": "stop", "message": {"content": answer("A person waves.")}}],
        "usage": provider_usage,
    }
    server = _CaptionServer(None, replies=[reply])
    try:
        with collecting() as usage:
            seat_asker(server.base_url, api_key="", timeout=5)(frame(100))
        write_llm_usage(tmp_path, usage)
    finally:
        server.close()

    report = json.loads((tmp_path / USAGE_FILE).read_text())
    assert report["calls"] == 1
    assert report["unmetered_calls"] == unknown
    assert report["usage_complete"] is (unknown == 0)


def test_refused_motion_request_still_records_an_unmetered_attempt(tmp_path):
    server = _CaptionServer("required-token")
    try:
        with collecting() as usage, pytest.raises(PermissionError):
            seat_asker(server.base_url, api_key="", timeout=5)(frame(100))
        write_llm_usage(tmp_path, usage)
    finally:
        server.close()

    report = json.loads((tmp_path / USAGE_FILE).read_text())
    assert report["calls"] == report["unmetered_calls"] == 1
    assert report["by_stage"]["motion"]["calls"] == 1


def still_missing(store, sources):
    with sqlite3.connect(store) as connection:
        return [s.asset_id for s in missing_motion(connection, sources)]


def test_true_videos_and_live_photos_that_play_are_the_only_sources():
    assets = [
        picture("clip", kind=AssetType.VIDEO, name="VID.MOV"),
        picture("photo"),
        picture("moving-live", live="moving-companion"),
        picture("quiet-live", live="quiet-companion"),
        picture("unmeasured-live", live="unmeasured-companion"),
    ]
    residuals = {"moving-live": 2.0, "quiet-live": 0.4}

    sources = motion_sources(assets, residual_of=lambda a: residuals.get(a.id))

    assert [(s.asset_id, s.playback_id) for s in sources] == [
        ("clip", "clip"),
        ("moving-live", "moving-companion"),
    ]


def test_a_cold_video_is_described_once_and_a_warm_one_asks_nothing(store):
    sources = motion_sources([video("clip")], residual_of=lambda _a: None)
    seat = Seat(answer("A child throws a ball, then runs after it,"))

    outcome = produce(store, sources, seat=seat)

    assert (outcome.described, outcome.bytes_read, outcome.requests) == (1, 300, 4)
    assert len(seat.strips) == 1
    with Image.open(io.BytesIO(seat.strips[0])) as strip:
        assert strip.width == 3 * strip.height  # three frames side by side, in time order
    assert still_missing(store, sources) == []
    reader = BankedMotionLines(store_path=store, assets={"clip": video("clip")}, described=True)
    line = reader.observe({"asset_id": "clip", "kind": "video", "raw_seconds": 8.0})
    assert line.startswith("A child throws a ball, then runs after it (3 frames")


def test_a_changed_source_is_described_again(store):
    old = motion_sources([video("clip")], residual_of=lambda _a: None)
    produce(store, old, seat=Seat(answer("A child runs.")))
    renamed = picture("clip", kind=AssetType.VIDEO, name="RENAMED.MOV")

    assert still_missing(store, motion_sources([renamed], residual_of=lambda _a: None)) == ["clip"]


def refused(status):
    request = httpx.Request("GET", "https://immich.test/api/assets/clip/video/playback")
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


def test_a_playback_immich_will_not_serve_is_settled_not_a_gap(store):
    sources = motion_sources([video("clip")], residual_of=lambda _a: None)

    def missing(_playback_id):
        raise refused(404)

    outcome = produce(store, sources, sample=missing, seat=Seat(answer("unused")))

    assert outcome.failures == {} and outcome.unavailable == 1
    assert still_missing(store, sources) == []


@pytest.mark.parametrize("error", [OSError("connection reset"), refused(503)])
def test_a_transport_failure_is_unfinished_work(store, error):
    sources = motion_sources([video("clip")], residual_of=lambda _a: None)

    def broken(_playback_id):
        raise error

    outcome = produce(store, sources, sample=broken, seat=Seat(answer("unused")))

    assert list(outcome.failures) == ["clip"]
    assert still_missing(store, sources) == ["clip"]


def test_two_invalid_answers_are_settled_and_one_is_repaired(store):
    first = motion_sources([video("one")], residual_of=lambda _a: None)
    second = motion_sources([video("two")], residual_of=lambda _a: None)

    produce(store, first, seat=Seat("not json", json.dumps({"description": ""})))
    repaired = produce(store, second, seat=Seat("not json", answer("Two people wave.")))

    assert repaired.described == 1
    assert still_missing(store, first + second) == []
    lines = BankedMotionLines(
        store_path=store, assets={"one": video("one"), "two": video("two")}, described=True
    )
    assert lines.observe({"asset_id": "one", "kind": "video", "raw_seconds": 12.0}).startswith(
        "not described"
    )
    assert lines.observe({"asset_id": "two", "kind": "video"}).startswith("Two people wave")


def test_a_miss_or_a_tier_without_captions_reads_the_plain_facts(store):
    sources = motion_sources([video("clip")], residual_of=lambda _a: None)
    produce(store, sources, seat=Seat(answer("A dog jumps into a lake.")))
    live = {"asset_id": "live", "kind": "live-motion", "raw_seconds": 5.5, "residual": 2.25}
    assets = {"clip": video("clip"), "live": picture("live")}

    without_captions = BankedMotionLines(store_path=store, assets=assets, described=False)

    assert without_captions.observe({"asset_id": "clip", "kind": "video", "raw_seconds": 31.8}) == (
        "not described; 32 s of video"
    )
    assert without_captions.observe(live) == "not described; 6 s of live photo, motion 2.2"
    assert without_captions.metrics() == {
        "producer": without_captions.producer,
        "requested": 2,
        "banked": 0,
        "plain_facts": 2,
    }


def test_a_refused_seat_credential_stops_the_stage_rather_than_one_video(store):
    sources = motion_sources([video("clip")], residual_of=lambda _a: None)

    with pytest.raises(PermissionError):
        produce(store, sources, seat=Seat(PermissionError("HTTP 401")))


def test_a_live_photo_plays_by_the_residual_its_last_cut_measured(tmp_path):
    moving, quiet = picture("moving", live="c1"), picture("quiet", live="c2")
    bank = tmp_path / "annotations.sqlite"
    with closing(open_cut_measurements(bank)) as connection:
        remember_motion_residual(
            connection,
            asset_id=moving.id,
            producer=RESIDUAL_PRODUCER,
            source_digest=source_metadata_digest(moving),
            measured={"residual": 1.9},
        )

    residual_of = banked_residuals(bank)

    assert (residual_of(moving), residual_of(quiet)) == (1.9, None)
    assert banked_residuals(tmp_path / "absent.sqlite")(moving) is None


def banking_motion(calls, *, fail=False):
    """The motion stage's seam: records what it was asked and banks a line unless told to fail."""

    def motion(**kwargs):
        from immich_memories.analysis.editorial_preparation_motion import (
            MOTION_PRODUCER,
            MotionPreparation,
        )
        from immich_memories.store.motion_lines import (
            DESCRIBED,
            MotionLine,
            initialize_motion_lines,
            remember_motion_line,
        )

        sources = kwargs["sources"]
        calls.append(("motion", tuple(s.asset_id for s in sources)))
        if fail:
            return MotionPreparation(failures={s.asset_id: "OSError: reset" for s in sources})
        initialize_motion_lines(kwargs["connection"])
        for source in sources:
            remember_motion_line(
                kwargs["connection"],
                asset_id=source.asset_id,
                producer=MOTION_PRODUCER,
                source_digest=source.digest,
                line=MotionLine(DESCRIBED, "A child runs", 3),
                bytes_read=250_000,
            )
        return MotionPreparation(described=len(sources), bytes_read=250_000, requests=4)

    return motion


def prepared_video(asset_id):
    from tests.test_editorial_preparation import asset

    return asset(asset_id).model_copy(update={"type": AssetType.VIDEO})


def prepare(tmp_path, motion, **kwargs):
    from dataclasses import replace

    from tests.test_editorial_preparation import asset, preview, run, successful_ports

    calls = kwargs.pop("calls", [])
    return run(
        tmp_path,
        assets=[asset("aa1"), prepared_video("vv1")],
        ports=replace(successful_ports(calls), motion=motion),
        fetch_preview=lambda _: preview(),
        read_playback=lambda *_: pytest.fail("the seam reads playback, not the pass"),
        **kwargs,
    )


def test_a_full_pass_describes_each_video_once_and_a_warm_pass_asks_nothing(tmp_path):
    calls = []

    cold = prepare(tmp_path, banking_motion(calls), calls=calls)
    motion_calls = [call for call in calls if call[0] == "motion"]
    calls.clear()
    warm = prepare(tmp_path, banking_motion(calls), calls=calls)

    assert motion_calls == [("motion", ("vv1",))]
    assert cold.complete and cold.pictures_by_stage["motion"] == 1
    assert cold.transfer_by_stage["motion"] == {"bytes": 250_000, "requests": 4, "seat_calls": 0}
    assert warm.complete and calls == []


def test_an_unfinished_motion_line_blocks_the_cut_like_a_caption(tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import MOTION_PRODUCER

    result = prepare(tmp_path, banking_motion([], fail=True))

    assert result.missing_by_producer == {f"motion:{MOTION_PRODUCER}": ("vv1",)}
    assert result.failures == {"motion:vv1": "OSError: reset"}


@pytest.mark.parametrize("tier", ["no_captions", "metadata_only"])
def test_a_tier_without_a_caption_seat_never_asks_for_motion(tmp_path, tier):
    from tests.test_editorial_preparation import without_picture_facts

    def refuse(**_):
        pytest.fail("motion ran under a tier without a caption seat")

    result = prepare(
        tmp_path, refuse, preparation_config=without_picture_facts(tier=tier), head_versions={}
    )

    assert result.complete


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="needs ffmpeg")
def test_playback_reads_never_overlap_because_the_immich_client_drives_one_loop(store, tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import playback_sampler
    from tests.test_playback_keyframes import encode

    data = encode(tmp_path / "clip.mp4", gop=30)
    inside = threading.Lock()

    def read(_playback_id, start, length):
        # WHY: the Immich playback endpoint; the real sync client fails on overlapping calls
        if not inside.acquire(blocking=False):
            raise RuntimeError("This event loop is already running")
        try:
            threading.Event().wait(0.005)
            return data[start : start + length], len(data)
        finally:
            inside.release()

    sources = motion_sources([video(f"clip-{i}") for i in range(6)], residual_of=lambda _a: None)
    outcome = produce(
        store, sources, sample=playback_sampler(read), seat=Seat(answer("A pattern moves."))
    )

    assert outcome.failures == {} and outcome.described == 6


@pytest.mark.parametrize(
    "error,reason",
    [
        (PermissionError("caption endpoint answered HTTP 401"), "HTTP 401"),
        (RuntimeError("This event loop is already running"), "RuntimeError: This event loop"),
    ],
)
def test_a_motion_stage_that_breaks_is_named_and_leaves_the_videos_owed(tmp_path, error, reason):
    from immich_memories.analysis.editorial_preparation_motion import MOTION_PRODUCER

    def broken(**_):
        raise error

    result = prepare(tmp_path, broken)

    assert reason in result.failures["motion"]
    assert reason in " ".join(result.producer_failures)
    assert result.missing_by_producer[f"motion:{MOTION_PRODUCER}"] == ("vv1",)
