"""Real local speech detection and encoding preserve the planned runtime."""

import subprocess
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from immich_memories.analysis.editorial_runtime_ports import production_speech_resolver
from immich_memories.config_loader import Config
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.assembly_engine import AssemblyEngine
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.probe_cache import ProbeCache
from tests.conftest import make_asset
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


@pytest.fixture
def spoken_video(tmp_path):
    fixture = Path(__file__).parents[2] / "fixtures/speech/synthetic_speech_16k.npy"
    audio = np.load(fixture).astype(np.float32) / 32768.0
    path = tmp_path / "speech.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=30:d=3",
            "-f",
            "f32le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "1",
            "-c:a",
            "aac",
            str(path),
        ],
        input=audio.tobytes(),
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path


def test_production_speech_cuts_use_real_detector_and_reuse_facts(
    spoken_video, tmp_path, monkeypatch
):
    calls = []

    # WHY: only Immich transport is replaced; decoding, VAD, cache and cut selection are real.
    class Client:
        def __init__(self, **_kwargs):
            pass

        def download_playback(self, asset_id, path):
            calls.append(asset_id)
            path.write_bytes(spoken_video.read_bytes())

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("immich_memories.api.sync_client.SyncImmichClient", Client)
    source = SimpleNamespace(
        config=Config(),
        assets={"video": make_asset("video", duration=3)},
        companion_assets={},
        bank_dir=tmp_path / "banks",
        # The speech facts are banked per asset since #1070, so the port reads the store.
        store_path=tmp_path / "annotations.sqlite",
    )
    carrier = {"asset_id": "video", "kind": "video", "seconds": 1.0, "raw_seconds": 3.0}
    with ExitStack() as resources:
        resolve = production_speech_resolver(source, resources=resources)
        first = resolve([carrier])
    assert calls == ["video", "closed"]
    assert 1.1 < first[0]["seconds"] < 1.7  # The utterance finishes before the known pause.
    with ExitStack() as resources:
        resolve = production_speech_resolver(source, resources=resources)
        assert resolve([carrier]) == first
    assert calls == ["video", "closed"]


def test_same_smart_cut_renders_to_the_same_measured_length(spoken_video, tmp_path):
    settings = AssemblySettings(
        encoding_plan=standalone_assembly_encoding_plan(28),
        transition=TransitionType.SMART,
        transition_duration=0.3,
        target_resolution=(160, 90),
        auto_resolution=False,
        normalize_clip_audio=False,
    )
    prober = FFmpegProber(settings)
    engine = AssemblyEngine(settings, prober, ClipEncoder(settings, prober, lambda _: None))
    clips = [AssemblyClip(path=spoken_video, duration=3, asset_id=str(i)) for i in range(5)]
    expected = 15 - engine.get_transition_types(clips).count("fade") * 0.3
    durations = []
    for index in range(2):
        path = engine.assemble_scalable(clips, tmp_path / f"render-{index}.mp4")
        durations.append(ProbeCache().get(path).duration_seconds)
    assert durations[0] == pytest.approx(expected, abs=0.15)
    assert durations[0] == durations[1]
