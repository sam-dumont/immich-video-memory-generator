"""Real filmstrip decoding and banked descriptions behind source admission."""

import hashlib
import json
import subprocess

import pytest

from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.editorial_story_motion import StoryMotionFacts
from immich_memories.analysis.llm_query import LLMTransportAttempt
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.models import AssetType
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset


@pytest.fixture
def playback(tmp_path):
    path = tmp_path / "synthetic.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=10",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


def setup_reader(tmp_path, monkeypatch, payload):
    calls, fetches = [], []

    async def answer(prompt, _config, **kwargs):
        calls.append((prompt, kwargs["images"]))
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-motion-description-v1",
                "description": "A test pattern changes across three frames.",
                "motion_contribution": "meaningful",
                "motion_reason": "The pattern changes.",
            }
        )

    # Only external transport is replaced; sampling, sheet rendering, parsing and cache are real.
    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", answer)
    asset = make_asset("ordinary-video", duration="00:00:03")
    asset.type = AssetType.VIDEO
    unit = {
        "kind": "video",
        "asset_id": asset.id,
        "members": [asset.id],
        "video_ids": [asset.id],
        "trim_points": [],
        "seconds": 2.0,
    }
    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "bank.sqlite", trace=trace
    )

    def factory():
        return StoryMotionFacts(
            assets={asset.id: asset},
            allowed_ids={asset.id},
            fetch_playback=lambda key: fetches.append(key) or payload,
            requester=gateway,
            trace=trace,
            cache_dir=tmp_path / "motion-cache",
            output_dir=tmp_path / "attempt",
        )

    return asset, unit, factory, gateway, calls, fetches


def test_exact_sample_provenance_and_warm_reuse(tmp_path, monkeypatch, playback):
    _asset, unit, factory, gateway, calls, fetches = setup_reader(tmp_path, monkeypatch, playback)
    try:
        reader = factory()
        line = reader.observe(unit)
        assert "3 chronological samples within 0.00–2.00s" in line
        assert "not a full-video or privacy assessment" in line
        assert reader.observe(unit) == factory().observe(unit) == line
        assert len(calls) == len(fetches) == 1
        assert "chronological filmstrip" in calls[0][0]
        (record,) = reader.records.values()
        assert record["playback_sha256"] == hashlib.sha256(playback).hexdigest()
        assert record["sampled_interval"] == [0.0, 2.0]
        assert record["frame_count"] == 3 and len(record["filmstrip_sha256"]) == 64
        assert record["description"]["provenance"]
        assert "uncovered_person" not in record  # No audience authority is minted here.
    finally:
        gateway.close()


@pytest.mark.parametrize("change", ["outside", "photo", "live", "members", "trim", "nan"])
def test_unbound_or_nonordinary_material_is_never_fetched(tmp_path, monkeypatch, change):
    asset, unit, factory, gateway, calls, fetches = setup_reader(tmp_path, monkeypatch, b"unused")
    try:
        reader = factory()
        if change == "outside":
            reader.allowed_ids = frozenset()
        elif change == "photo":
            asset.type = AssetType.IMAGE
        elif change == "live":
            unit["kind"] = "live-motion"
        elif change == "members":
            unit["members"].append("unbound-companion")
        elif change == "trim":
            unit["trim_points"] = [[1.0, 2.0]]
        else:
            unit["seconds"] = float("nan")
        assert reader.observe(unit) == ""
        assert not calls and not fetches
    finally:
        gateway.close()


def test_failed_decode_never_substitutes_a_cover_or_banks_a_fact(tmp_path, monkeypatch):
    _asset, unit, factory, gateway, calls, fetches = setup_reader(tmp_path, monkeypatch, b"invalid")
    try:
        reader = factory()
        assert reader.observe(unit) == reader.observe(unit) == ""
        assert not calls and len(fetches) == 1
        assert reader.metrics()["observed"] == 0
        assert next(iter(reader.records.values()))["status"] == "unavailable"
    finally:
        gateway.close()


def test_new_interval_is_new_evidence_and_corrupt_cache_is_not_trusted(
    tmp_path, monkeypatch, playback
):
    asset, unit, factory, gateway, calls, fetches = setup_reader(tmp_path, monkeypatch, playback)
    try:
        first = factory()
        assert first.observe(unit)
        assert first.observe({**unit, "seconds": 1.0})
        assert len(calls) == 2 and len(fetches) == 1
        asset.is_favorite = True  # Complete metadata identity changes even with the same source ID.
        second = factory()
        assert second.observe(unit)
        assert len(fetches) == 2
        for path in (tmp_path / "motion-cache").rglob("playback.private.mp4"):
            path.write_bytes(b"corrupt")
        assert factory().observe(unit) == ""
        assert len(fetches) == 2
    finally:
        gateway.close()
