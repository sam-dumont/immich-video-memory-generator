"""Stable, plain-text summary for read-only generation planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from immich_memories.processing.output_canvas import OutputCanvas
from immich_memories.processing.timeline_budget import TimelinePlan


@dataclass(frozen=True, slots=True)
class GenerationPreview:
    """Resolved generation decisions available before rendering begins."""

    memory_type: str
    date_range: str
    video_candidates: int
    live_photo_candidates: int
    photo_candidates: int
    selected_videos: int
    selected_photos: int
    selected_duration: float
    timeline: TimelinePlan
    canvas: OutputCanvas
    output_path: Path
    upload_intent: bool
    music_policy: str

    @property
    def selected_total(self) -> int:
        return self.selected_videos + self.selected_photos

    @property
    def title_card_count(self) -> int:
        opening = int(self.timeline.title_duration > 0.0)
        ending = int(self.timeline.ending_duration > 0.0)
        return opening + self.timeline.max_dividers + ending

    @property
    def estimated_final_duration(self) -> float:
        return (
            min(self.selected_duration, self.timeline.content_budget)
            + self.timeline.title_budget
            - self.timeline.transition_budget
        )

    @property
    def month_divider_summary(self) -> str | None:
        if self.timeline.divider_policy == "all":
            if self.timeline.eligible_dividers == 0:
                return "none needed (one selected month)"
            return f"all {self.timeline.eligible_dividers} selected month changes"
        if self.timeline.divider_policy == "none":
            return (
                "none (complete set would exceed "
                f"{self.timeline.soft_max_duration:.1f}s soft maximum)"
            )
        return None


def music_policy(*, config, music: str | None, no_music: bool) -> str:
    """Describe music intent without generating or inspecting media."""
    if no_music:
        return "disabled"
    if music:
        return "provided file"
    from immich_memories.generate_music import music_config_available

    return "automatic" if music_config_available(config) else "none configured"


def print_generation_preview(preview: GenerationPreview) -> None:
    """Print a stable summary even when interactive progress is disabled."""
    click.echo("Dry-run plan (no video will be created)")
    click.echo(f"Memory: {preview.memory_type}")
    click.echo(f"Date range: {preview.date_range}")
    click.echo(
        "Candidates: "
        f"{preview.video_candidates} video, "
        f"{preview.live_photo_candidates} Live Photo, "
        f"{preview.photo_candidates} photo"
    )
    click.echo(
        f"Selected: {preview.selected_total} "
        f"({preview.selected_videos} video, {preview.selected_photos} photo)"
    )
    click.echo(f"Estimated content duration: {preview.selected_duration:.1f}s")
    if preview.month_divider_summary is not None:
        click.echo(f"Month dividers: {preview.month_divider_summary}")
    click.echo(f"Title cards: {preview.title_card_count} ({preview.timeline.title_budget:.1f}s)")
    click.echo(f"Estimated final duration: {preview.estimated_final_duration:.1f}s")
    click.echo(
        f"Canvas: {preview.canvas.width}x{preview.canvas.height} ({preview.canvas.orientation})"
    )
    click.echo(f"Music: {preview.music_policy}")
    click.echo(f"Output (planned): {preview.output_path}")
    click.echo(f"Upload: {'planned' if preview.upload_intent else 'disabled'}")
