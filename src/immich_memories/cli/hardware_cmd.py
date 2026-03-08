"""Hardware acceleration command for Immich Memories CLI."""

from __future__ import annotations

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_success


def register_hardware_commands(main: click.Group) -> None:
    """Register the hardware command on the main CLI group."""

    @main.command("hardware")
    def hardware_info() -> None:
        """Show hardware acceleration information."""
        from immich_memories.processing.hardware import (
            HWAccelBackend,
            detect_hardware_acceleration,
        )

        console.print("[bold]Hardware Acceleration Detection[/bold]")
        console.print()

        caps = detect_hardware_acceleration()

        if caps.backend == HWAccelBackend.NONE:
            console.print("[yellow]No hardware acceleration detected[/yellow]")
            console.print()
            console.print("Video encoding will use CPU (libx264).")
            console.print()
            console.print("To enable hardware acceleration:")
            console.print("  \u2022 NVIDIA: Install CUDA drivers and FFmpeg with NVENC support")
            console.print("  \u2022 Apple: Use macOS with VideoToolbox (built-in)")
            console.print("  \u2022 Intel: Install oneVPL/QSV drivers")
            console.print("  \u2022 AMD/Linux: Install VAAPI drivers")
            return

        table = Table(title=f"Hardware Acceleration: {caps.backend.value.upper()}")
        table.add_column("Feature", style="cyan")
        table.add_column("Status", style="green")

        table.add_row("Device", caps.device_name or "Unknown")
        if caps.vram_mb > 0:
            table.add_row("VRAM", f"{caps.vram_mb} MB")
        table.add_row("H.264 Encode", "\u2713" if caps.supports_h264_encode else "\u2717")
        table.add_row("H.265 Encode", "\u2713" if caps.supports_h265_encode else "\u2717")
        table.add_row("H.264 Decode", "\u2713" if caps.supports_h264_decode else "\u2717")
        table.add_row("H.265 Decode", "\u2713" if caps.supports_h265_decode else "\u2717")
        table.add_row("GPU Scaling", "\u2713" if caps.supports_scaling else "\u2717")
        table.add_row("OpenCV CUDA", "\u2713" if caps.opencv_cuda else "\u2717")

        console.print(table)
        console.print()

        if caps.has_encoding:
            print_success("Hardware encoding is available!")
            console.print("Video processing will use GPU acceleration for faster encoding.")
        else:
            console.print("[yellow]Hardware decoding only - encoding will use CPU[/yellow]")
