#!/usr/bin/env python3
"""Simulate the CLI generate flow for demo recording.

Drives the real LiveDisplay and Rich components. No Immich connection needed,
and nothing here comes from a real library: the scope, the counts and every log
line are invented. The stage strings of the edit are the exact labels the
editorial planner reports through ``on_stage``.

Update the stage strings when the planner's phases change.
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

# The scripted run, invented end to end.
# Each entry: (delay_seconds, action, args)
# Actions: "spinner" (indeterminate task), "log" (add_log), "progress" (update bar), "success"/"info"
_SCRIPT: list[tuple[float, str, str]] = [
    # --- Connection & discovery ---
    (0.8, "spinner_done", "Connecting to Immich..."),
    (0.6, "spinner_done", "Resolving scope: year in review 2025..."),
    (0.0, "success", "Scope: 2025-01-01 to 2025-12-31"),
    (0.7, "spinner_done", "Fetching videos..."),
    (0.0, "success", "Found 147 videos"),
    (0.5, "spinner_done", "Fetching live photos..."),
    (0.0, "log", "Live Photos: 200 photos -> 89 clusters -> 89 clips [devices: Apple]"),
    (0.0, "success", "Found 23 live photo clips"),
    (0.5, "spinner_done", "Fetching photos..."),
    (0.0, "info", "Found 312 photos"),
    (0.3, "success", "482 pictures ready to cut"),
    # --- The edit: the stages the editorial planner reports, in order ---
    (0.0, "progress_start", ""),
    (0.5, "progress", "Preparing source metadata|5"),
    (0.4, "log", "Annotations: 482 pictures, 11 read fresh, 471 from the store"),
    (0.5, "progress", "Reading event evidence|9"),
    (0.4, "log", "34 moments projected from 482 pictures"),
    (0.4, "log", "Event evidence read for 34 moments"),
    (0.5, "progress", "Reading the period account|13"),
    (0.4, "log", "Period account: 9 stories weighed against the thesis"),
    (0.5, "progress", "Building editorial cards|17"),
    (0.4, "log", "Workprint: 73 carriers across 9 stories"),
    (0.5, "progress", "Editing the memory|21"),
    (0.4, "log", "Granted: 3 dominant, 4 major, 2 glimpse"),
    (0.4, "progress", "Validating selected source timing|25"),
    (0.3, "log", "Duration: 312s of content for a 600s memory - near target"),
    (0.2, "success", "Selected 12 pictures for the memory"),
    # --- Generation phase: 25->100% ---
    (0.3, "progress", "Selecting and rendering photos...|30"),
    (0.4, "log", "Photos: detected portrait orientation, rendering to 2160x3840"),
    (0.3, "log", "Ken Burns animation: subject at (0.48, 0.32), zoom 1.2x->1.0x"),
    (0.5, "progress", "Generating title screen...|34"),
    (0.4, "log", "KernelTitleRenderer initialized: 2160x3840 @ 60.0fps"),
    (0.3, "log", "Generating title on the GPU: 2025"),
    # Title screen rendering 34->45%
    (0.2, "progress", "Generating title screen...|37"),
    (0.2, "progress", "Generating title screen...|40"),
    (0.2, "progress", "Generating title screen...|43"),
    (0.2, "progress", "Generating title screen...|45"),
    (0.3, "log", "Generated title screen: title_screen.mp4"),
    # Ending screen
    (0.3, "progress", "Generating ending screen...|48"),
    (0.3, "log", "Detected HLG format (iPhone) - 9 clips"),
    (0.2, "log", "Using reverse slow-mo ending from last_clip_processed.mp4"),
    (0.2, "progress", "Generating ending screen...|50"),
    (0.2, "progress", "Generating ending screen...|52"),
    (0.3, "log", "Generated ending screen: ending_screen.mp4"),
    # Encoding 53->100%
    (0.3, "progress", "Encoding video...|55"),
    (0.3, "log", "Streaming assembly: 12 clips at 2160x3840"),
    (0.2, "log", "Streaming assembly with HLG HDR preservation"),
    (0.15, "progress", "Encoding (0:30 / 5:12) - 10%|60"),
    (0.15, "progress", "Encoding (1:02 / 5:12) - 20%|65"),
    (0.15, "progress", "Encoding (1:34 / 5:12) - 30%|70"),
    (0.15, "progress", "Encoding (2:05 / 5:12) - 40%|75"),
    (0.15, "progress", "Encoding (2:36 / 5:12) - 50%|80"),
    (0.15, "progress", "Encoding (3:07 / 5:12) - 60%|85"),
    (0.15, "progress", "Encoding (3:38 / 5:12) - 70%|88"),
    (0.15, "progress", "Encoding (4:10 / 5:12) - 80%|92"),
    (0.15, "progress", "Encoding (4:41 / 5:12) - 90%|95"),
    (0.15, "progress", "Encoding (5:12 / 5:12) - 100%|98"),
    (0.3, "log", "Streaming assembly complete: 12 clips -> output.mp4"),
    (0.2, "progress", "Complete!|100"),
]


def _print_fake_prompt(console: Console) -> None:
    prompt = Text()
    prompt.append("❯ ", style="green bold")
    prompt.append("immich-memories generate \\\n")
    prompt.append("    --memory-type year_in_review --year 2025 \\\n")
    prompt.append("    --duration 600 --include-photos --include-live-photos\n")
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
        start=datetime(2025, 1, 1),
        end=datetime(2025, 12, 31),
    )

    table = _build_params_table(
        config=config,
        memory_type="year_in_review",
        date_range=date_range,
        person_names=[],
        duration=600,
        orientation="landscape",
        scale_mode=None,
        transition="smart",
        resolution="auto",
        output_format="mp4",
        output_path=Path("/tmp/year_2025_memories.mp4"),  # noqa: S108
        add_date=False,
        add_place=False,
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
                progress_task = display.add_task("Cutting the memory...", total=100)

            elif action == "progress" and progress_task is not None:
                parts = args.split("|")
                desc = parts[0]
                pct = int(parts[1]) if len(parts) > 1 else 0
                display.update(progress_task, completed=pct, description=desc)

    console.print()
    print_success("Video saved to: /tmp/year_2025_memories.mp4")
    console.print()


if __name__ == "__main__":
    main()
