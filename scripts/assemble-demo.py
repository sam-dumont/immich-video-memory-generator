#!/usr/bin/env python3
"""Assemble raw Playwright recordings into a polished demo video.

Usage: uv run python scripts/assemble-demo.py
Expects: docs-site/static/demo/raw/segment*.webm
Produces: docs-site/static/demo/demo.mp4

Pipeline:
  1. Speed-ramp each segment (3x fast, 1x highlights)
  2. Zoom into highlight segments (crop + scale)
  3. Generate intro/outro cards from blurred screenshots
  4. Crossfade transitions between all segments
  5. Mix ACE-Step background music
  6. Final encode (H.264, CRF 18, faststart)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "docs-site" / "static" / "demo"
RAW_DIR = DEMO_DIR / "raw"
WORK_DIR = DEMO_DIR / "work"
OUTPUT = DEMO_DIR / "demo.mp4"
MUSIC = DEMO_DIR / "demo-music.wav"
SCREENSHOT_DIR = REPO_ROOT / "docs-site" / "static" / "img" / "screenshots"

# (filename, speed_factor, zoom_region or None)
# zoom_region = (x_frac, y_frac, w_frac, h_frac) — crop fractions
SEGMENTS = [
    ("segment1-config", 3.0, None),
    ("segment2-navigate", 3.0, None),
    ("segment3-grid", 1.0, (0.1, 0.15, 0.8, 0.7)),
    ("segment4-options", 3.0, None),
    ("segment5-progress", 1.5, (0.15, 0.3, 0.7, 0.4)),
    ("segment6-preview", 1.0, None),
]

FADE_DUR = 0.5
INTRO_DUR = 2.5
OUTRO_DUR = 3.0
MUSIC_DB = -12


def log(msg: str) -> None:
    print(f"\033[0;34m[assemble]\033[0m {msg}")


def ok(msg: str) -> None:
    print(f"\033[0;32m[assemble]\033[0m {msg}")


def run_ffmpeg(args: list[str], desc: str = "") -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"FFmpeg error ({desc}): {result.stderr[-500:]}", file=sys.stderr)
        sys.exit(1)


def get_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(result.stdout.strip())


def stage1_speed_ramp() -> list[Path]:
    """Speed-ramp each segment, apply zoom where configured."""
    log("Stage 1: Speed-ramping segments...")
    outputs = []
    for name, speed, zoom in SEGMENTS:
        src = RAW_DIR / f"{name}.webm"
        if not src.exists():
            log(f"  SKIP: {src.name} not found")
            continue
        dst = WORK_DIR / f"{name}_fast.mp4"
        pts = 1.0 / speed
        vf = f"setpts={pts}*PTS"
        if zoom:
            x, y, w, h = zoom
            vf += f",crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=1440:900"
        run_ffmpeg(
            [
                "-i",
                str(src),
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "fast",
                str(dst),
            ],
            name,
        )
        ok(f"  {name}: {speed}x" + (" + zoom" if zoom else ""))
        outputs.append(dst)
    return outputs


def stage2_intro_outro() -> tuple[Path, Path]:
    """Generate intro/outro cards from blurred screenshots."""
    log("Stage 2: Generating intro/outro cards...")

    intro_bg = SCREENSHOT_DIR / "step1-overview.png"
    if not intro_bg.exists():
        intro_bg = SCREENSHOT_DIR / "hero-step1.png"

    intro = WORK_DIR / "intro.mp4"
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(intro_bg),
            "-t",
            str(INTRO_DUR),
            "-vf",
            "scale=1440:900,gblur=sigma=25,eq=brightness=-0.3,"
            "drawtext=text='Immich Memories':fontsize=64:fontcolor=white:"
            "x=(w-tw)/2:y=(h-th)/2-30,"
            "drawtext=text='Your photos, cinematic recap videos':"
            "fontsize=24:fontcolor=0xaaaaaa:x=(w-tw)/2:y=(h-th)/2+40",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(intro),
        ],
        "intro",
    )
    ok(f"  intro ({INTRO_DUR}s)")

    outro_bg = SCREENSHOT_DIR / "step4-preview-export.png"
    if not outro_bg.exists():
        outro_bg = intro_bg

    outro = WORK_DIR / "outro.mp4"
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(outro_bg),
            "-t",
            str(OUTRO_DUR),
            "-vf",
            "scale=1440:900,gblur=sigma=25,eq=brightness=-0.3,"
            "drawtext=text='Try it yourself':fontsize=48:fontcolor=white:"
            "x=(w-tw)/2:y=(h-th)/2-50,"
            "drawtext=text='github.com/sam-dumont/immich-video-memory-generator':"
            "fontsize=28:fontcolor=0x6C8EBF:x=(w-tw)/2:y=(h-th)/2+10,"
            "drawtext=text='Open source · Self-hosted · Privacy-first':"
            "fontsize=20:fontcolor=0x888888:x=(w-tw)/2:y=(h-th)/2+60",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(outro),
        ],
        "outro",
    )
    ok(f"  outro ({OUTRO_DUR}s)")

    return intro, outro


def stage3_concatenate(intro: Path, segments: list[Path], outro: Path) -> Path:
    """Concatenate all segments with crossfade transitions."""
    log("Stage 3: Concatenating with crossfade...")

    all_inputs = [intro, *segments, outro]
    durations = [get_duration(f) for f in all_inputs]

    input_args: list[str] = []
    for f in all_inputs:
        input_args.extend(["-i", str(f)])

    n = len(all_inputs)
    filt_parts: list[str] = []
    cumulative = 0.0
    prev = "[0:v]"

    for i in range(1, n):
        cumulative += durations[i - 1] - FADE_DUR
        out = f"[xf{i}]" if i < n - 1 else "[vout]"
        filt_parts.append(
            f"{prev}[{i}:v]xfade=transition=fade:duration={FADE_DUR}:offset={cumulative:.3f}{out}"
        )
        prev = out

    filt = ";".join(filt_parts)
    concat_out = WORK_DIR / "concatenated.mp4"

    run_ffmpeg(
        [
            *input_args,
            "-filter_complex",
            filt,
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            str(concat_out),
        ],
        "concatenate",
    )
    ok(f"  concatenated with {FADE_DUR}s crossfades")
    return concat_out


def stage4_mix_music(video: Path) -> Path:
    """Mix ACE-Step background music under the video."""
    if not MUSIC.exists():
        log("Stage 4: No demo-music.wav found, skipping music mix")
        return video

    log("Stage 4: Mixing music...")
    video_dur = get_duration(video)
    output = WORK_DIR / "with_music.mp4"

    run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(MUSIC),
            "-filter_complex",
            f"[1:a]atrim=0:{video_dur},afade=t=in:d=1,afade=t=out:st={video_dur - 2}:d=2,"
            f"volume={MUSIC_DB}dB[aout]",
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output),
        ],
        "music mix",
    )
    ok("  music mixed")
    return output


def stage5_final_encode(source: Path) -> None:
    """Final encode optimized for web."""
    log("Stage 5: Final encode...")

    has_audio = source != WORK_DIR / "concatenated.mp4"
    audio_args = ["-c:a", "copy"] if has_audio else ["-an"]

    run_ffmpeg(
        [
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            *audio_args,
            "-movflags",
            "+faststart",
            str(OUTPUT),
        ],
        "final",
    )

    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    duration = get_duration(OUTPUT)
    ok(f"Done! {OUTPUT}")
    ok(f"  Duration: {duration:.1f}s  Size: {size_mb:.1f}MB")


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    segments = stage1_speed_ramp()
    if not segments:
        print("ERROR: No segments found in", RAW_DIR)
        print("Run `make demo-record` first (requires UI on port 8099)")
        sys.exit(1)

    intro, outro = stage2_intro_outro()
    concat = stage3_concatenate(intro, segments, outro)
    with_music = stage4_mix_music(concat)
    stage5_final_encode(with_music)

    log("Cleaning up work directory...")
    shutil.rmtree(WORK_DIR)
    ok("Cleaned up!")


if __name__ == "__main__":
    main()
