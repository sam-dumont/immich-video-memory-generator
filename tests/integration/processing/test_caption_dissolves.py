"""A dissolve may blend pictures, but must not superimpose their captions."""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.streaming_assembler import assemble_streaming

pytestmark = pytest.mark.integration


def _frames(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 360, 640, 3)


def test_dissolve_has_no_caption_collision(tmp_path):
    library = Path(__file__).parents[2] / "e2e/fixtures/library"
    clips = []
    for index, name in enumerate(("lake-sunset.jpg", "woods-path.jpg")):
        source = tmp_path / f"source-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-loop",
                "1",
                "-i",
                str(library / name),
                "-vf",
                "scale=640:360",
                "-t",
                "2",
                "-r",
                "10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        clips.append(
            AssemblyClip(
                source,
                2.0,
                date=f"2024-06-{21 + index}",
                location_name=("Annecy", "Forest")[index],
            )
        )
    rendered = {}
    for captions in (False, True):
        output = tmp_path / f"captions-{captions}.mp4"
        assemble_streaming(
            clips,
            ["fade"],
            output,
            640,
            360,
            10,
            fade_duration=0.5,
            encoding_plan=standalone_assembly_encoding_plan(crf=0),
            date_overlay=captions,
            place_overlay=captions,
            scale_mode="black",
        )
        rendered[captions] = _frames(output)
        if captions and (review_dir := os.environ.get("TITLE_REVIEW_DIR")):
            destination = Path(review_dir)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, destination / "dissolve.mp4")
            from PIL import Image

            Image.fromarray(rendered[captions][17]).save(destination / "dissolve.png")

    plain, captioned = rendered[False], rendered[True]
    assert len(plain) == len(captioned) == 35
    assert not np.array_equal(plain[5], captioned[5]), "first caption must be visible"
    assert not np.array_equal(plain[25], captioned[25]), "second caption must be visible"
    np.testing.assert_array_equal(plain[15:20], captioned[15:20])
