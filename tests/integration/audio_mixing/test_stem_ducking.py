"""Real samples prove that stems leave room for speech and existing music."""

from __future__ import annotations

import subprocess
import wave

import numpy as np
import pytest

from immich_memories.audio.mixer import DuckingConfig, MixConfig
from immich_memories.audio.mixer_helpers import mix_audio_with_4stem_ducking

pytestmark = pytest.mark.integration
RATE = 44100


def _write_audio(path, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((samples * 32767).astype("<i2").tobytes())


def _amplitude(samples, frequency, start, end):
    section = samples[int(start * RATE) : int(end * RATE)]
    time = np.arange(len(section)) / RATE
    return abs(np.mean(section * np.exp(-2j * np.pi * frequency * time))) * 2


def test_stems_duck_under_speech_and_all_respect_existing_music(tmp_path):
    time = np.arange(6 * RATE) / RATE
    voice = 0.3 * np.sin(2 * np.pi * 1500 * time) * ((time >= 1) & (time < 2))
    voice_path = tmp_path / "voice.wav"
    _write_audio(voice_path, voice)
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=32x32:r=10:d=6",
            "-i",
            str(voice_path),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    stems = []
    for frequency in (110, 220, 440, 880):
        path = tmp_path / f"stem-{frequency}.wav"
        _write_audio(path, 0.05 * np.sin(2 * np.pi * frequency * time))
        stems.append(path)
    output = mix_audio_with_4stem_ducking(
        video,
        *stems,
        tmp_path / "mixed.mp4",
        config=MixConfig(
            ducking=DuckingConfig(music_volume_db=-6, attack_ms=10, release_ms=100),
            normalize_audio=False,
            fade_in_seconds=0,
            fade_out_seconds=0,
            mute_windows=[(4, 5)],
        ),
    )
    audio = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-",
        ],
    )
    samples = np.frombuffer(audio, dtype=np.float32)
    assert len(samples) / RATE == pytest.approx(6, abs=0.05)
    assert _amplitude(samples, 1500, 1.3, 1.6) > 0.2
    assert _amplitude(samples, 440, 1.3, 1.6) < _amplitude(samples, 440, 3.3, 3.6) * 0.8
    for frequency in (110, 220, 440, 880):
        assert _amplitude(samples, frequency, 4.3, 4.6) < (
            _amplitude(samples, frequency, 3.3, 3.6) * 0.1
        )


@pytest.mark.parametrize("use_stems", [True, False])
def test_shared_music_phase_mixes_generated_music(tmp_path, monkeypatch, use_stems):
    from unittest.mock import MagicMock

    from immich_memories.audio.music_generator_models import GeneratedMusic, MusicStems
    from immich_memories.config import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import run_music_phase
    from tests.test_music_output_paths import _h264_output_plan

    time = np.arange(3 * RATE) / RATE
    silent = tmp_path / "full.wav"
    tone = tmp_path / "vocals.wav"
    _write_audio(silent, np.zeros_like(time))
    _write_audio(tone, 0.15 * np.sin(2 * np.pi * 440 * time))
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=32x32:r=10:d=3",
            "-i",
            str(silent),
            "-c:v",
            "libx264",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    generated = (
        GeneratedMusic(silent, MusicStems(tone, drums=silent, bass=silent, other=silent))
        if use_stems
        else GeneratedMusic(tone)
    )
    # WHY: stands in for expensive model inference; FFmpeg still mixes and validates real files.
    monkeypatch.setattr(
        "immich_memories.generate_music.auto_generate_music", lambda *_a, **_k: generated
    )
    config = Config()
    config.ace_step.enabled = True
    # WHY: replace run-history writes; the real music phase still validates its output.
    tracker = MagicMock()
    outcome = run_music_phase(
        GenerationParams(clips=[], output_path=video, config=config),
        [],
        video,
        tmp_path,
        tracker,
        encoding_plan=_h264_output_plan(),
        mute_windows=[(1, 2)],
    )
    assert outcome.applied
    assert outcome.warning is None
    audio = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-",
        ],
    )
    samples = np.frombuffer(audio, dtype=np.float32)
    assert _amplitude(samples, 440, 0.5, 0.8) > 0.001
    assert _amplitude(samples, 440, 1.5, 1.8) < _amplitude(samples, 440, 0.5, 0.8) * 0.1


async def test_stems_receive_the_mastered_music(tmp_path):
    from immich_memories.audio.generators.base import GenerationResult
    from immich_memories.audio.music_generator_models import MusicStems, VideoTimeline
    from immich_memories.audio.music_pipeline import MusicPipeline
    from tests.test_music_pipeline import FakeGenerator

    time = np.arange(3 * RATE) / RATE
    raw = tmp_path / "quiet.wav"
    _write_audio(raw, 0.001 * np.sin(2 * np.pi * 440 * time))

    # WHY: model inference is expensive; the real mastering filter must feed the separator.
    class Generator(FakeGenerator):
        async def generate(self, request, progress_callback=None):
            return GenerationResult(audio_path=raw)

    class Separator:
        name = "test separator"

        async def is_available(self):
            return True

        async def separate_stems(self, audio_path, output_dir, progress_callback=None):
            return MusicStems(vocals=audio_path)

    async with MusicPipeline([Generator()], Separator()) as pipeline:
        result = await pipeline.generate_music_for_video(VideoTimeline(), tmp_path, num_versions=1)
    track = result.versions[0]
    assert track.stems is not None
    assert track.stems.vocals == track.full_mix
    import soundfile as sf

    samples, _ = sf.read(track.stems.vocals)
    assert np.sqrt(np.mean(samples**2)) > 0.01
