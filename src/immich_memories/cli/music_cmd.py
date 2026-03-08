"""Music commands for Immich Memories CLI."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_success


def register_music_commands(main: click.Group) -> None:
    """Register the music command group on the main CLI group."""

    @main.group()
    def music() -> None:
        """Music and audio commands."""
        pass

    main.add_command(music)

    @music.command("search")
    @click.option("--mood", "-m", type=str, help="Mood (happy, calm, energetic, etc.)")
    @click.option("--genre", "-g", type=str, help="Genre (acoustic, electronic, cinematic, etc.)")
    @click.option("--tempo", "-t", type=click.Choice(["slow", "medium", "fast"]), help="Tempo")
    @click.option("--min-duration", type=float, default=60, help="Minimum duration in seconds")
    @click.option("--limit", "-n", type=int, default=10, help="Number of results")
    @click.pass_context
    def music_search(
        ctx: click.Context,
        mood: str | None,
        genre: str | None,
        tempo: str | None,
        min_duration: float,
        limit: int,
    ) -> None:
        """Search for royalty-free music."""
        import asyncio

        from immich_memories.audio.music_sources import PixabayMusicSource

        async def search():
            source = PixabayMusicSource()
            try:
                tracks = await source.search(
                    mood=mood,
                    genre=genre,
                    tempo=tempo,
                    min_duration=min_duration,
                    limit=limit,
                )
                return tracks
            finally:
                await source.close()

        console.print("[bold]Searching for music...[/bold]")
        console.print()

        if mood:
            console.print(f"Mood: {mood}")
        if genre:
            console.print(f"Genre: {genre}")
        if tempo:
            console.print(f"Tempo: {tempo}")
        console.print()

        tracks = asyncio.get_event_loop().run_until_complete(search())

        if not tracks:
            print_error("No tracks found matching criteria")
            return

        table = Table(title=f"Found {len(tracks)} tracks")
        table.add_column("Title", style="cyan")
        table.add_column("Artist", style="green")
        table.add_column("Duration", style="yellow")
        table.add_column("Tags")

        for track in tracks:
            duration = f"{int(track.duration_seconds // 60)}:{int(track.duration_seconds % 60):02d}"
            tags = ", ".join(track.tags[:3]) if track.tags else ""
            table.add_row(track.title, track.artist, duration, tags)

        console.print(table)

    @music.command("analyze")
    @click.argument("video_path", type=click.Path(exists=True))
    @click.option("--ollama-url", default=None, help="Ollama API URL (default: from config)")
    @click.option("--ollama-model", default=None, help="Ollama vision model (default: from config)")
    @click.pass_context
    def music_analyze(
        ctx: click.Context,
        video_path: str,
        ollama_url: str | None,
        ollama_model: str | None,
    ) -> None:
        """Analyze a video to determine its mood for music selection."""
        import asyncio

        from immich_memories.audio.mood_analyzer import get_mood_analyzer

        config = ctx.obj["config"]

        # Use config values as defaults, allow CLI overrides
        effective_ollama_url = ollama_url or config.llm.ollama_url
        effective_ollama_model = ollama_model or config.llm.ollama_model

        async def analyze():
            analyzer = await get_mood_analyzer(
                ollama_url=effective_ollama_url,
                ollama_model=effective_ollama_model,
                openai_api_key=config.llm.openai_api_key,
                openai_model=config.llm.openai_model,
                openai_base_url=config.llm.openai_base_url,
            )
            return await analyzer.analyze_video(Path(video_path))

        console.print("[bold]Analyzing video mood...[/bold]")
        console.print()

        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Extracting keyframes and analyzing...", total=None)

            try:
                mood = asyncio.get_event_loop().run_until_complete(analyze())
                progress.update(task, completed=True)
            except Exception as e:
                print_error(f"Analysis failed: {e}")
                return

        table = Table(title="Video Mood Analysis")
        table.add_column("Attribute", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Primary Mood", mood.primary_mood)
        if mood.secondary_mood:
            table.add_row("Secondary Mood", mood.secondary_mood)
        table.add_row("Energy Level", mood.energy_level)
        table.add_row("Suggested Tempo", mood.tempo_suggestion)
        table.add_row("Color Palette", mood.color_palette)
        table.add_row("Genre Suggestions", ", ".join(mood.genre_suggestions))
        table.add_row("Confidence", f"{mood.confidence:.0%}")

        console.print(table)
        console.print()

        if mood.description:
            console.print(f"[dim]Description: {mood.description}[/dim]")

    @music.command("add")
    @click.argument("video_path", type=click.Path(exists=True))
    @click.argument("output_path", type=click.Path())
    @click.option(
        "--music",
        "-m",
        type=click.Path(exists=True),
        help="Music file (auto-select if not provided)",
    )
    @click.option("--mood", type=str, help="Override mood for music selection")
    @click.option("--genre", "-g", type=str, help="Override genre for music selection")
    @click.option("--volume", "-v", type=float, default=-6.0, help="Music volume in dB")
    @click.option("--fade-in", type=float, default=2.0, help="Fade in duration in seconds")
    @click.option("--fade-out", type=float, default=3.0, help="Fade out duration in seconds")
    @click.pass_context
    def music_add(
        ctx: click.Context,
        video_path: str,
        output_path: str,
        music: str | None,
        mood: str | None,
        genre: str | None,
        volume: float,
        fade_in: float,
        fade_out: float,
    ) -> None:
        """Add background music to a video with automatic ducking.

        If no music file is provided, automatically selects music based on video mood.
        Music volume is automatically lowered when speech/sounds are detected.
        """
        import asyncio

        from immich_memories.audio.mixer import AudioMixer

        ctx.obj["config"]

        async def add_music():
            mixer = AudioMixer()
            return await mixer.add_music_to_video(
                video_path=Path(video_path),
                output_path=Path(output_path),
                music_path=Path(music) if music else None,
                mood=mood,
                genre=genre,
                fade_in=fade_in,
                fade_out=fade_out,
                music_volume_db=volume,
                auto_select=music is None,
            )

        console.print("[bold]Adding Music to Video[/bold]")
        console.print()
        console.print(f"Input: {video_path}")
        console.print(f"Output: {output_path}")
        if music:
            console.print(f"Music: {music}")
        else:
            console.print("Music: [dim]Auto-select based on video mood[/dim]")
        console.print()

        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Processing...", total=None)

            try:
                result = asyncio.get_event_loop().run_until_complete(add_music())
                progress.update(task, completed=True)
            except Exception as e:
                print_error(f"Failed: {e}")
                return

        print_success(f"Video saved to: {result}")
