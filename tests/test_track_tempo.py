"""Pick the bundled track whose beat divides the photo cadence.

#312's first half asks a generator for a beat-aligned tempo. A bundled track
cannot be asked — its tempo is already fixed — so the choice has to run the
other way: measure what ships, then pick from the ones that land on the cadence.

Detection is numpy + ffmpeg on purpose. librosa is present in this venv only as
a transitive dependency of an extra, so importing it would fail `make dep-check`
and break a plain install.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.audio.track_tempo import detect_bpm, track_for_cadence


def _click_track(path: Path, bpm: int, seconds: int = 12) -> Path:
    """A metronome at a known tempo — the only fixture with a knowable answer."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1200:duration={seconds}:sample_rate=22050",
            "-af",
            f"atrim=0:{seconds},asetrate=22050,"
            f"volume='if(lt(mod(t*{bpm}/60,1),0.06),1,0)':eval=frame",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.parametrize("bpm", [90, 120])
def test_a_metronome_reads_back_at_its_own_tempo(tmp_path: Path, bpm: int) -> None:
    track = _click_track(tmp_path / f"click{bpm}.wav", bpm)

    detected = detect_bpm(track)

    assert detected is not None
    assert abs(detected - bpm) <= 3, f"read {detected}, expected ~{bpm}"


def test_the_track_whose_beat_divides_the_cadence_wins(tmp_path: Path) -> None:
    """A 4s photo is 8 beats at 120bpm and 6.67 at 100 — 120 is the fit."""
    good = _click_track(tmp_path / "a.wav", 120)
    poor = _click_track(tmp_path / "b.wav", 100)

    chosen = track_for_cadence([poor, good], cadence_seconds=4.0)

    assert chosen == good


def _tracks_at(tmp_path: Path, bpms: dict[str, float], monkeypatch) -> list[Path]:
    """Tracks with stated tempos — the gate is under test, not the measurement.

    Real click tracks cannot express "0.3 beats off" precisely enough to sit on
    either side of the tolerance on purpose, because detection quantizes to
    whole autocorrelation lags.
    """
    paths = {}
    for name, bpm in bpms.items():
        path = tmp_path / name
        path.write_bytes(b"")
        paths[path] = bpm

    # WHY: replaces the FFmpeg decode and autocorrelation, so each track's tempo
    # is a stated fact rather than something the fixture has to synthesize.
    monkeypatch.setattr(
        "immich_memories.audio.track_tempo.detect_bpm", lambda path: paths.get(path)
    )
    return list(paths)


def test_every_track_that_lands_on_the_cadence_is_a_candidate(tmp_path: Path, monkeypatch) -> None:
    """Returning the single best fit makes every repeat of a memory sound the same."""
    tracks = _tracks_at(tmp_path, {"a.opus": 120.0, "b.opus": 90.0, "c.opus": 150.0}, monkeypatch)

    picks = {track_for_cadence(tracks, cadence_seconds=4.0) for _ in range(40)}

    assert picks == set(tracks), "all three land on whole beats at 4s"


def test_a_track_that_misses_the_beat_is_not_a_candidate(tmp_path: Path, monkeypatch) -> None:
    tracks = _tracks_at(tmp_path, {"fits.opus": 120.0, "misses.opus": 97.0}, monkeypatch)

    picks = {track_for_cadence(tracks, cadence_seconds=4.0) for _ in range(40)}

    assert picks == {tracks[0]}


def test_nothing_on_the_beat_defers_to_the_callers_own_choice(tmp_path: Path, monkeypatch) -> None:
    """None means "no opinion", which is what makes the caller fall back to random."""
    tracks = _tracks_at(tmp_path, {"a.opus": 97.0, "b.opus": 113.0}, monkeypatch)

    assert track_for_cadence(tracks, cadence_seconds=4.0) is None


def test_no_candidates_is_not_an_error() -> None:
    assert track_for_cadence([], cadence_seconds=4.0) is None


def test_an_unreadable_track_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A bad file must cost that track its turn, not the run."""
    broken = tmp_path / "broken.opus"
    broken.write_bytes(b"not audio")
    good = _click_track(tmp_path / "good.wav", 120)

    assert track_for_cadence([broken, good], cadence_seconds=4.0) == good
