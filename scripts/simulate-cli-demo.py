#!/usr/bin/env python3
"""Simulate the CLI generate flow for demo recording.

Drives the real LiveDisplay and Rich components with log messages
extracted from a real pipeline run. No Immich connection needed.

NOTE: Log messages sourced from a real recording (2026-03-27).
Update when the pipeline phases change significantly.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from immich_memories.cli._helpers import print_info, print_success
from immich_memories.cli._live_display import LiveDisplay
from immich_memories.cli.generate import _build_params_table
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange

# Scripted sequence extracted from a real pipeline run.
# Each entry: (delay_seconds, action, args)
# Actions: "spinner" (indeterminate task), "log" (add_log), "progress" (update bar), "success"/"info"
_SCRIPT: list[tuple[float, str, str]] = [
    # --- Connection & discovery ---
    (0.8, "spinner_done", "Connecting to Immich..."),
    (0.6, "spinner_done", "Finding person: Alice..."),
    (0.0, "success", "Found person: Alice"),
    (0.7, "spinner_done", "Fetching videos..."),
    (0.0, "success", "Found 147 videos"),
    (0.5, "spinner_done", "Fetching live photos..."),
    (0.0, "log", "Live Photos: 200 photos → 89 clusters → 89 clips [devices: Apple]"),
    (0.0, "success", "Found 23 live photo clips"),
    (0.5, "spinner_done", "Fetching photos..."),
    (0.0, "info", "Found 312 photos"),
    (0.3, "success", "177 clips ready for generation"),
    # --- Analysis phase: 0→20% ---
    (0.0, "progress_start", ""),
    (0.5, "log", "Phase 1: Clustered 177 → 173 clips (4 duplicates)"),
    (0.3, "log", "Duration filter: removed 4 clips shorter than 2.0s minimum"),
    (0.3, "log", "Quality gate: removed 101 clips (below 1425px for 2160p output)"),
    (0.2, "log", "Density budget: 9 favorites (118s) + 0 gap-fillers = 118s"),
    (0.3, "progress", "Analyzing: scoring clips|5"),
    (0.5, "log", "→ Found 2 visual boundaries"),
    (0.3, "log", "→ Found 2 audio boundaries (silence gaps)"),
    (0.5, "progress", "Analyzing: content analysis|8"),
    (0.4, "log", "LLM: baby in striped shirt turns onto tummy | emotion=cute, score=0.51"),
    (0.5, "progress", "Analyzing: scene detection|12"),
    (0.3, "log", "→ Found 12 audio boundaries (silence gaps)"),
    (0.3, "log", "Made 13 boundary adjustments to avoid mid-speech cuts"),
    (0.5, "progress", "Analyzing: scoring clips|16"),
    (0.4, "log", "LLM: woman snuggling with baby on play mat | emotion=happy, score=0.68"),
    (0.3, "log", "→ Adjusted 36 candidates to 36 candidates"),
    (0.5, "progress", "Analyzing: final selection|20"),
    (0.3, "log", "Unified selection: 9 videos + 3 photos = 63s content"),
    (0.2, "success", "Selected 12 clips for final video"),
    # --- Generation phase: 20→100% ---
    (0.3, "progress", "Selecting and rendering photos...|25"),
    (0.4, "log", "Photos: detected portrait orientation, rendering to 2160x3840"),
    (0.3, "log", "Ken Burns animation: face at (0.48, 0.32), zoom 1.2x→1.0x"),
    (0.5, "progress", "Generating title screen...|30"),
    (0.4, "log", "TaichiTitleRenderer initialized: 2160x3840 @ 60.0fps"),
    (0.3, "log", "Generating title with Taichi: Août 2024"),
    # Title screen rendering 30→45%
    (0.2, "progress", "Generating title screen...|33"),
    (0.2, "progress", "Generating title screen...|36"),
    (0.2, "progress", "Generating title screen...|39"),
    (0.2, "progress", "Generating title screen...|42"),
    (0.2, "progress", "Generating title screen...|45"),
    (0.3, "log", "Generated title screen: title_screen.mp4"),
    # Ending screen
    (0.3, "progress", "Generating ending screen...|48"),
    (0.3, "log", "Detected HLG format (iPhone) — 9 clips"),
    (0.2, "log", "Using reverse slow-mo ending from last_clip_processed.mp4"),
    (0.2, "progress", "Generating ending screen...|50"),
    (0.2, "progress", "Generating ending screen...|52"),
    (0.3, "log", "Generated ending screen: ending_screen.mp4"),
    # Encoding 53→100%
    (0.3, "progress", "Encoding video...|55"),
    (0.3, "log", "Streaming assembly: 12 clips at 2160x3840"),
    (0.2, "log", "Streaming assembly with HLG HDR preservation"),
    (0.15, "progress", "Encoding (0:05 / 0:55) — 10%|60"),
    (0.15, "progress", "Encoding (0:10 / 0:55) — 20%|65"),
    (0.15, "progress", "Encoding (0:15 / 0:55) — 30%|70"),
    (0.15, "progress", "Encoding (0:20 / 0:55) — 40%|75"),
    (0.15, "progress", "Encoding (0:25 / 0:55) — 50%|80"),
    (0.15, "progress", "Encoding (0:30 / 0:55) — 60%|85"),
    (0.15, "progress", "Encoding (0:40 / 0:55) — 70%|88"),
    (0.15, "progress", "Encoding (0:45 / 0:55) — 80%|92"),
    (0.15, "progress", "Encoding (0:50 / 0:55) — 90%|95"),
    (0.15, "progress", "Encoding (0:55 / 0:55) — 100%|98"),
    (0.3, "log", "Streaming assembly complete: 12 clips → output.mp4"),
    (0.2, "progress", "Complete!|100"),
]


def _print_fake_prompt(console: Console) -> None:
    prompt = Text()
    prompt.append("❯ ", style="green bold")
    prompt.append("immich-memories generate \\\n")
    prompt.append("    --memory-type person_spotlight \\\n")
    prompt.append("    --person 'Alice' --year 2024 --month 8 \\\n")
    prompt.append("    --duration 60 --include-photos --include-live-photos\n")
    console.print(prompt)


def main() -> None:
    console = Console()

    _print_fake_prompt(console)
    time.sleep(0.3)

    console.print()
    console.print("[bold]Immich Memories Generator[/bold]")
    console.print()

    config = Config()
    config.immich.url = "https://photos.example.com"
    config.immich.api_key = "demo-api-key"

    date_range = DateRange(
        start=datetime(2024, 8, 1),
        end=datetime(2024, 8, 31),
    )

    table = _build_params_table(
        config=config,
        memory_type="person_spotlight",
        date_range=date_range,
        person_names=["Alice"],
        duration=60,
        orientation="landscape",
        scale_mode=None,
        transition="smart",
        resolution="auto",
        output_format="mp4",
        output_path=Path("/tmp/alice_person_spotlight_aug2024.mp4"),  # noqa: S108
        add_date=False,
        keep_intermediates=False,
        privacy_mode=False,
        title_override=None,
        subtitle_override=None,
        use_live_photos=True,
        music="auto",
        music_volume=0.5,
    )
    console.print(table)
    console.print()
    time.sleep(0.5)

    progress_task = None

    with LiveDisplay(console=console) as display:
        for delay, action, args in _SCRIPT:
            if delay > 0:
                time.sleep(delay)

            if action == "spinner_done":
                task = display.add_task(args, total=None)
                time.sleep(0.4)
                display.update(task, completed=True)

            elif action == "success":
                print_success(args)

            elif action == "info":
                print_info(args)

            elif action == "log":
                display.add_log(args)

            elif action == "progress_start":
                progress_task = display.add_task("Analyzing clips...", total=100)

            elif action == "progress" and progress_task is not None:
                parts = args.split("|")
                desc = parts[0]
                pct = int(parts[1]) if len(parts) > 1 else 0
                display.update(progress_task, completed=pct, description=desc)

    console.print()
    print_success("Video saved to: /tmp/alice_person_spotlight_aug2024.mp4")
    console.print()


if __name__ == "__main__":
    main()
