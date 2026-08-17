"""Real whisper.cpp against real synthesised speech.

Local only. The repo has no recorded-speech fixture by design -- the committed
FireRedVAD fixture is formant synthesis, which has no words in it for an ASR model
to find -- so this generates French speech with macOS `say` and skips elsewhere.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from immich_memories.config_models import TranscriptionConfig
from immich_memories.speech.transcription import select_transcriber
from immich_memories.speech.vad import extract_audio_16k

pytestmark = pytest.mark.integration

FRENCH_LINE = "Les enfants jouent sur la plage au bord de la mer."


def _french_voice() -> str | None:
    """First installed fr_FR voice, or None. Voice names vary between machines."""
    if sys.platform != "darwin" or shutil.which("say") is None:
        return None
    listing = subprocess.run(  # noqa: S603
        ["say", "-v", "?"], capture_output=True, text=True, check=False
    )
    for line in listing.stdout.splitlines():
        if "fr_FR" in line:
            return line.split()[0]
    return None


@pytest.fixture
def french_speech(tmp_path: Path) -> Path:
    voice = _french_voice()
    assert voice is not None
    audio = tmp_path / "speech.aiff"
    subprocess.run(  # noqa: S603
        ["say", "-v", voice, "-o", str(audio), FRENCH_LINE], check=True
    )
    return audio


@pytest.mark.skipif(_french_voice() is None, reason="macOS `say` with a French voice required")
def test_french_speech_is_transcribed_in_french(french_speech: Path):
    pytest.importorskip("pywhispercpp")

    # `base` deliberately, not the shipped `medium` default: this test should not
    # pull a 1.5 GB model onto a developer machine. TTS is clean, close-mic'd and
    # single-speaker, so a small model handles it -- which is exactly why this test
    # proves the wiring works and says nothing about real family audio.
    transcriber = select_transcriber(
        TranscriptionConfig(enabled=True, languages=["fr"], model="base", min_confidence=0.0)
    )
    assert transcriber is not None

    audio = extract_audio_16k(french_speech)
    assert audio is not None

    result = transcriber.transcribe(audio)

    assert result is not None
    assert result.language == "fr"
    # `tiny` will not be word-perfect, so assert it found French content rather
    # than an exact string.
    assert any(word in result.text.lower() for word in ("enfant", "plage", "mer"))


@pytest.mark.skipif(_french_voice() is None, reason="macOS `say` with a French voice required")
def test_silence_produces_no_transcript(tmp_path: Path):
    """The post-ASR gate must reject whisper's output on non-speech audio.

    whisper hallucinates on roughly all pure non-speech input, so this is the
    behaviour that keeps a silent clip from acquiring a confident sentence.
    """
    pytest.importorskip("pywhispercpp")

    silence = tmp_path / "silence.wav"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "5", str(silence)],
        check=True,
        capture_output=True,
    )

    # min_confidence is spelled out because the default is now 0.0: the confidence
    # signal is inverted on real audio, so the VAD gate, the marker stripper and the
    # repetition guard are what must reject silence.
    transcriber = select_transcriber(
        TranscriptionConfig(enabled=True, languages=["fr"], model="base", min_confidence=0.0)
    )
    assert transcriber is not None

    audio = extract_audio_16k(silence)
    assert audio is not None

    assert transcriber.transcribe(audio) is None
