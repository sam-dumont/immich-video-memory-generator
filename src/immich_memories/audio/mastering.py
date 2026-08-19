"""Master a music track so it sits well under video.

Generated and bundled tracks come out darker and at less consistent loudness than
music the ear expects. Measured against real Suno tracks, ours carried about 1% of
their energy above 6 kHz where those carried 2.3-2.7%, and integrated loudness
ranged over 3.8 LU between moods.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# A gentle linear-phase tilt. Deliberately NOT an exciter: aexciter synthesises
# harmonics that phase-cancel against the source, which measured as a +5 dB lift
# overall while carving a 4.5 dB hole at 8-10 kHz.
_TILT = (
    "firequalizer=gain_entry='"
    "entry(20,0);entry(1500,0);entry(3000,0.56);entry(6000,2.24);"
    "entry(9000,3.58);entry(12000,4.03);entry(16000,4.03);entry(20000,2.24)"
    "':delay=0.03:fft2=on"
)
# -17 LUFS with a narrow range: music under dialogue should stay put rather than
# swell. EBU R128 s2 places distribution loudness at -20 to -16 LUFS.
_LOUDNESS = "loudnorm=I=-17:TP=-1.5:LRA=8"
# WHY: alimiter normalises by default and silently ignores `limit`; measured
# +0.0003 dBFS (clipping) without level=disabled, -0.9996 with it.
_CEILING = "alimiter=limit=-1dB:level=disabled:attack=5:release=50"

_SAMPLE_RATE = 48000


def master_music_track(source: Path, destination: Path) -> Path:
    """Apply the tilt, loudness target and ceiling. Returns the source on failure.

    Mastering is a polish step, so a failure here should cost brightness, not the
    whole video.
    """
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-af",
        f"{_TILT},{_LOUDNESS},{_CEILING}",
        # WHY: loudnorm reports its analysis at 192 kHz and ffmpeg follows it
        # unless the output rate is pinned.
        "-ar",
        str(_SAMPLE_RATE),
        str(destination),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("Music mastering failed, using the track unmastered: %s", error)
        return source
    return destination
