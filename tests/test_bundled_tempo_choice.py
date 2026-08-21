"""A bundled track is chosen for its tempo when there is a cadence to fit.

#312's generator half asks for a tempo whose beat divides the photo cadence.
Bundled tracks cannot be asked, so the fit has to be found by measuring.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from immich_memories.audio.bundled_music import bundled_track_for_mood


def _library(tmp_path: Path, bpms: dict[str, int]) -> Path:
    root = tmp_path / "library" / "happy"
    root.mkdir(parents=True)
    for name, bpm in bpms.items():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1200:duration=12:sample_rate=22050",
                "-af",
                f"volume='if(lt(mod(t*{bpm}/60,1),0.06),1,0)':eval=frame",
                str(root / name),
            ],
            check=True,
        )
    return tmp_path / "library"


def test_the_fitting_tempo_is_chosen_when_a_cadence_is_given(tmp_path: Path) -> None:
    """4s is 8 beats at 120 and 6.67 at 100."""
    library = _library(tmp_path, {"fits.wav": 120, "misses.wav": 100})

    chosen = bundled_track_for_mood("happy", library=library, cadence_seconds=4.0)

    assert chosen is not None and chosen.name == "fits.wav"


def test_without_a_cadence_the_choice_stays_random(tmp_path: Path) -> None:
    """No photos means no rhythm to sync to; variety matters more than tempo."""
    library = _library(tmp_path, {"a.wav": 120, "b.wav": 100})

    picks = {bundled_track_for_mood("happy", library=library) for _ in range(25)}

    assert len(picks) == 2, "a fixed pick would make every repeat sound the same"
