"""Generate command for Immich Memories CLI."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import click

from immich_memories.analysis.live_photo_pipeline import drop_live_photo_components
from immich_memories.cli._asset_fetch import fetch_photos, fetch_videos
from immich_memories.cli._date_resolution import (
    BIRTHDAY_FLAG_FORMAT,
    default_duration_for_type,
    duration_from_date_range,
    infer_memory_type,
    resolve_date_range,
)
from immich_memories.cli._generate_display import (
    _build_params_table,
    _print_generation_result,
)
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
from immich_memories.cli._trip_generation import handle_trip_generation, resolve_music_arg
from immich_memories.cli.generate_options import (
    automation_options,
    output_options,
    per_memory_type_options,
    run_options,
    scope_options,
    selection_options,
)
from immich_memories.cli.generate_resolution import (
    _apply_scalar_overrides,
    _arm_selection_trace,
    _reject_album_scope_conflicts,
    _resolve_generation_scope,
    name_from_catalogue,
    resolve_inclusion,
    resolve_short_form,
    resolve_special_day,
)
from immich_memories.filename_builder import build_memory_output_path, normalize_output_path
from immich_memories.memory_types.date_builders import BIRTHDAY_HISTORY_FROM, birthday_anchor
from immich_memories.processing.encoding_plan import resolve_output_selection
from immich_memories.timeperiod import DateRange, parse_date


def register_generate_commands(main: click.Group) -> None:
    """Register the generate command on the main CLI group."""

    @main.command()
    @scope_options
    @output_options
    @run_options
    @selection_options
    @per_memory_type_options
    @automation_options
    @click.option("--quiet", is_flag=True, help="Suppress interactive progress, emit log lines")
    @click.pass_context
    def generate(
        ctx: click.Context,
        year: int | None,
        start: str | None,
        end: str | None,
        period: str | None,
        birthday: str | None,
        person: tuple[str, ...],
        memory_type: str | None,
        holiday: str | None,
        season: str | None,
        month: int | None,
        hemisphere: str,
        duration: float | None,
        short_form: str | None,
        orientation: str,
        scale_mode: str | None,
        transition: str,
        resolution: str,
        music_volume: float,
        output_format: str | None,
        quality: str | None,
        output: str | None,
        music: str | None,
        no_music: bool,
        dry_run: bool,
        no_render: bool,
        trace_selection: Path | None,
        upload_to_immich: bool,
        album: str | None,
        from_album: str | None,
        add_date: bool,
        add_place: bool,
        keep_intermediates: bool,
        privacy_mode: bool,
        title_override: str | None,
        subtitle_override: str | None,
        llm_title: bool,
        include_live_photos: bool | None,
        include_photos: bool | None,
        accept_any_provenance: bool,
        photo_duration: float | None,
        refinement_passes: int | None,
        analysis_depth: str | None,
        trip_index: int | None,
        all_trips: bool,
        years_back: int | None,
        near_date: str | None,
        day: date | None,
        source: str,
        memory_key: str | None,
        memory_category: str | None,
        automation_attempt_id: str | None,
        automation_target_date: str | None,
        quiet: bool,
    ) -> None:
        """Generate a video compilation.

        \b
        Memory type presets:
          --memory-type season --season summer --year 2024
          --memory-type person_spotlight --person "Alice" --year 2024
          --memory-type multi_person --person "Alice" --person "Bob" --year 2024
          --memory-type monthly_highlights --month 7 --year 2024
          --memory-type on_this_day

        \b
        Manual time period options:
          --year 2024                    Calendar year
          --year 2024 --birthday 02/07   Birthday-based year
          --start 2024-01-01 --end 2024-06-30   Custom range
          --start 2024-01-01 --period 6m        Period from start
        """
        _arm_selection_trace(trace_selection)

        from immich_memories.cli._live_display import LiveDisplay, ProgressDisplay, QuietDisplay

        config = ctx.obj["config"]
        output_selection = resolve_output_selection(
            config_codec=config.output.codec,
            config_container=config.output.format,
            format_override=output_format,
        )

        # CLI quality flag overrides config
        if quality:
            config.output.quality = quality
            config.output.crf = None  # Let quality preset determine CRF

        person_names = list(person) if person else []

        if not config.immich.url or not config.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        if automation_attempt_id is not None and source != "auto":
            raise click.UsageError("--automation-attempt-id requires --source=auto")

        exact_on_this_day: date | None = None
        if automation_target_date is not None:
            trusted_on_this_day = (
                source == "auto"
                and bool(memory_key)
                and memory_category == memory_type == "on_this_day"
            )
            if not trusted_on_this_day:
                raise click.UsageError(
                    "--automation-target-date requires complete on_this_day automation identity"
                )
            try:
                exact_on_this_day = parse_date(automation_target_date)
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc

        if from_album:
            _reject_album_scope_conflicts(
                year=year,
                start=start,
                end=end,
                period=period,
                birthday=birthday,
                season=season,
                month=month,
                memory_type=memory_type,
                person_names=person_names,
            )

        # Read the memory from the date flags when it was not named. Without
        # this --month did nothing unless --memory-type was also given, so
        # `--year 2025 --month 7` rendered the whole year.
        memory_type = infer_memory_type(
            memory_type,
            year=year,
            month=month,
            has_person=bool(person_names),
            season=season,
            birthday=birthday,
            from_album=from_album,
        )

        # Validate memory type constraints
        if memory_type in ("person_spotlight", "multi_person") and not person_names:
            print_error(f"--person is required with --memory-type {memory_type}")
            sys.exit(1)

        if memory_type == "trip" and not year:
            print_error("--year is required with --memory-type trip")
            sys.exit(1)

        if (trip_index is not None or all_trips) and memory_type != "trip":
            print_error("--trip-index and --all-trips require --memory-type trip")
            sys.exit(1)

        if near_date and memory_type != "trip":
            print_error("--near-date requires --memory-type trip")
            sys.exit(1)

        # A birthday memory reaches back over earlier birthdays, so --years-back
        # means something to it too -- how many of them to look for.
        if (
            years_back is not None
            and not birthday
            and memory_type
            not in (
                "on_this_day",
                "holiday",
                "then_and_now",
            )
        ):
            print_error(
                "--years-back requires --birthday, or --memory-type on_this_day, "
                "holiday or then_and_now"
            )
            sys.exit(1)

        # The catalogue, not the command line, knows what the day was called.
        special_day = resolve_special_day(day, memory_type)
        title_override, subtitle_override = name_from_catalogue(
            special_day, title_override, subtitle_override
        )

        date_range, date_ranges = _resolve_generation_scope(
            from_album=from_album,
            year=year,
            start=start,
            end=end,
            period=period,
            birthday=birthday,
            memory_type=memory_type,
            season=season,
            month=month,
            hemisphere=hemisphere,
            years_back=years_back,
            on_this_day_target=exact_on_this_day,
            holiday=holiday,
            preset_params=special_day,
        )

        # Determine output path
        if output:
            output_path = normalize_output_path(Path(output), output_selection.container)
        elif from_album:
            # A placeholder directory anchor; the album handler names the file.
            output_path = config.output.output_path / f"album.{output_selection.container}"
        else:
            output_path = build_memory_output_path(
                output_dir=config.output.output_path,
                person_names=person_names,
                memory_type=memory_type,
                date_range=date_range,
                container=output_selection.container,
            )

        if not quiet:
            console.print()
            console.print("[bold]Immich Memories Generator[/bold]")
            console.print()

        use_live_photos = resolve_inclusion(
            include_live_photos, config_enabled=config.analysis.include_live_photos
        )
        use_photos = resolve_inclusion(include_photos, config_enabled=config.photos.enabled)
        _apply_scalar_overrides(
            config, photo_duration=photo_duration, refinement_passes=refinement_passes
        )

        # Analysis depth: CLI override → stored for PipelineConfig
        effective_analysis_depth = analysis_depth or "auto"

        # Infer memory type from context when not explicitly set
        if memory_type is None and person_names:
            memory_type = "person_spotlight" if len(person_names) == 1 else "multi_person"

        short = resolve_short_form(
            short_form,
            duration=duration,
            orientation=orientation,
            orientation_was_given=ctx.get_parameter_source("orientation")
            is not click.core.ParameterSource.DEFAULT,
        )
        duration, orientation = short.duration, short.orientation

        # Resolve duration: CLI --duration > memory type default > date-range scaling
        # Album mode defers to the pipeline, which sizes it from the album's media.
        if duration is None and not from_album:
            duration = default_duration_for_type(
                memory_type,
                date_range,
                special_day,
                primary_window=next(iter(date_ranges), None),
            )
            if duration is None:
                duration = duration_from_date_range(date_range)

        table = _build_params_table(
            config=config,
            memory_type="album" if from_album else memory_type,
            album_ref=from_album,
            date_range=date_range,
            person_names=person_names,
            duration=duration,
            orientation=orientation,
            scale_mode=scale_mode,
            transition=transition,
            resolution=resolution,
            output_format=output_format,
            output_path=output_path,
            add_date=add_date,
            add_place=add_place,
            keep_intermediates=keep_intermediates,
            privacy_mode=privacy_mode,
            title_override=title_override,
            subtitle_override=subtitle_override,
            use_live_photos=use_live_photos,
            music=music,
            music_volume=music_volume,
            no_music=no_music,
        )
        show_interactive = not quiet and sys.stdout.isatty()
        if not show_interactive:
            from immich_memories.cli._helpers import set_quiet_mode

            set_quiet_mode(True)
        else:
            console.print(table)
            console.print()

        from immich_memories.api.immich import ImmichAPIError, SyncImmichClient
        from immich_memories.generate import GenerationError

        try:
            # WHY: LiveDisplay coordinates Rich Live output with logging to
            # prevent raw log lines from breaking cursor-controlled rendering.
            def _make_progress(interactive: bool) -> ProgressDisplay:
                if interactive:
                    return LiveDisplay(console=console)
                return QuietDisplay()

            with _make_progress(show_interactive) as progress:
                # Connect to Immich
                task = progress.add_task("Connecting to Immich...", total=None)

                with SyncImmichClient(
                    base_url=config.immich.url,
                    api_key=config.immich.api_key,
                    api_version=config.immich.api_version,
                ) as client:
                    progress.update(task, completed=True)
                    # Album flow: the album is the pool, so branch before discovery
                    if from_album:
                        from immich_memories.cli._album_generation import (
                            handle_album_generation,
                        )

                        handle_album_generation(
                            client=client,
                            config=config,
                            progress=progress,
                            album_ref=from_album,
                            person_names=[],
                            output_path=output_path,
                            use_live_photos=use_live_photos,
                            use_photos=use_photos,
                            effective_analysis_depth=effective_analysis_depth,
                            transition=transition,
                            music=music,
                            music_volume=music_volume,
                            no_music=no_music,
                            resolution=resolution,
                            scale_mode=scale_mode,
                            output_format=output_format,
                            add_date=add_date,
                            add_place=add_place,
                            keep_intermediates=keep_intermediates,
                            privacy_mode=privacy_mode,
                            title_override=title_override,
                            subtitle_override=subtitle_override,
                            llm_title=llm_title,
                            upload_to_immich=upload_to_immich,
                            album=album,
                            duration=duration,
                            orientation=orientation,
                            source=source,
                            memory_key=memory_key,
                            memory_category=memory_category,
                            automation_attempt_id=automation_attempt_id,
                            dry_run=dry_run,
                            accept_any_provenance=accept_any_provenance,
                        )
                        return

                    # Trip detection flow: branch early
                    if memory_type == "trip" and year:
                        handle_trip_generation(
                            client=client,
                            config=config,
                            progress=progress,
                            year=year,
                            month=month,
                            trip_index=trip_index,
                            all_trips=all_trips,
                            near_date=near_date,
                            person_names=person_names,
                            output_path=output_path,
                            use_live_photos=use_live_photos,
                            use_photos=use_photos,
                            effective_analysis_depth=effective_analysis_depth,
                            transition=transition,
                            music=music,
                            music_volume=music_volume,
                            no_music=no_music,
                            resolution=resolution,
                            orientation=orientation,
                            scale_mode=scale_mode,
                            output_format=output_format,
                            add_date=add_date,
                            add_place=add_place,
                            keep_intermediates=keep_intermediates,
                            privacy_mode=privacy_mode,
                            title_override=title_override,
                            subtitle_override=subtitle_override,
                            llm_title=llm_title,
                            upload_to_immich=upload_to_immich,
                            album=album,
                            duration=duration,
                            requested_start=date_range.start.date() if start and end else None,
                            requested_end=date_range.end.date() if start and end else None,
                            source=source,
                            memory_key=memory_key,
                            memory_category=memory_category,
                            automation_attempt_id=automation_attempt_id,
                            dry_run=dry_run,
                            accept_any_provenance=accept_any_provenance,
                        )
                        return

                    # Find person(s) if specified
                    person_ids: list[str] = []
                    if person_names:
                        for pname in person_names:
                            task = progress.add_task(f"Finding person: {pname}...", total=None)
                            found_person = client.get_person_by_name(pname)
                            if not found_person:
                                print_error(f"Person not found: {pname}")
                                sys.exit(1)
                            person_ids.append(found_person.id)
                            progress.update(task, completed=True)
                            print_success(f"Found person: {found_person.name}")

                    # Immich holds the birth date; the bare --birthday flag is
                    # how a run says "use it". Curating one there is what makes
                    # every birthday memory land on the right week.
                    if birthday == "auto" and person_names:
                        found = client.get_person_by_name(person_names[0])
                        try:
                            anchor = birthday_anchor(
                                found.birth_date if found else None,
                                None,
                                person_name=person_names[0],
                            )
                        except ValueError as exc:
                            print_error(str(exc))
                            sys.exit(1)
                        birthday = anchor.strftime(BIRTHDAY_FLAG_FORMAT)
                        print_success(f"Using birthday: {birthday}")

                        # The first resolution ran before the birthday was known
                        # and yielded a stand-in calendar year, so both the
                        # window and the file named after it are redone here.
                        date_result = resolve_date_range(
                            year,
                            start,
                            end,
                            period,
                            birthday,
                            memory_type=memory_type,
                            season=season,
                            month=month,
                            hemisphere=hemisphere,
                            years_back=years_back,
                            on_this_day_target=exact_on_this_day,
                        )
                        if isinstance(date_result, list):
                            date_ranges = date_result
                            date_range = DateRange(
                                start=date_ranges[-1].start, end=date_ranges[0].end
                            )
                        else:
                            date_range = date_result
                            date_ranges = [date_result]
                        print_info(f"Memory window: {date_range.description}")
                        if not output:
                            output_path = build_memory_output_path(
                                output_dir=config.output.output_path,
                                person_names=person_names,
                                memory_type=memory_type,
                                date_range=date_range,
                                container=output_selection.container,
                            )

                    # A birthday memory's flashback windows are single days years
                    # apart, so most of them are empty and #661's per-window
                    # warning would bury the one that matters — the rolling year.
                    assets = fetch_videos(
                        history_from=BIRTHDAY_HISTORY_FROM if birthday else None,
                        client=client,
                        progress=progress,
                        date_ranges=date_ranges,
                        person_ids=person_ids,
                    )

                    # Fetch photos (if enabled)
                    fetched_photos: list = []
                    if use_photos:
                        fetched_photos = fetch_photos(
                            client=client,
                            date_ranges=date_ranges,
                            person_ids=person_ids,
                            merge_window_seconds=(config.analysis.live_photo_merge_window_seconds),
                        )
                        if fetched_photos:
                            print_info(f"Found {len(fetched_photos)} photos")

                    # A Live Photo's video half is part of a photograph, not
                    # footage: it must not compete as a video against its own still.
                    assets = drop_live_photo_components(assets, fetched_photos)

                    if not assets and not fetched_photos:
                        print_error("No videos or photos found matching criteria")
                        sys.exit(1)

                    # Display video summary
                    total_dur = sum(a.duration_seconds or 0 for a in assets)
                    print_info(f"Total video duration: {total_dur / 60:.1f} minutes")
                    if fetched_photos:
                        print_info(f"Photo clips to render: {len(fetched_photos)}")

                    # Config fallbacks: CLI flag > config > hardcoded default
                    effective_transition = (
                        transition if transition != "smart" else config.defaults.transition
                    )
                    effective_scale_mode = scale_mode or config.defaults.scale_mode

                    resolved_music = resolve_music_arg(music)

                    result_path, should_upload, album_name = run_pipeline_and_generate(
                        assets=assets,
                        photo_assets=fetched_photos if use_photos else None,
                        include_photos=use_photos and bool(fetched_photos),
                        use_live_photos=use_live_photos,
                        analysis_depth=effective_analysis_depth,
                        client=client,
                        config=config,
                        progress=progress,
                        duration=duration,
                        transition=effective_transition,
                        music=resolved_music,
                        music_volume=music_volume,
                        no_music=no_music,
                        output_path=output_path,
                        output_resolution=resolution,
                        output_orientation=orientation,
                        scale_mode=effective_scale_mode,
                        output_format=output_format,
                        add_date_overlay=add_date,
                        add_place_overlay=add_place,
                        debug_preserve_intermediates=keep_intermediates,
                        privacy_mode=privacy_mode,
                        title_override=title_override,
                        subtitle_override=subtitle_override,
                        llm_title=llm_title,
                        memory_type=memory_type,
                        person_names=person_names,
                        date_range=date_range,
                        upload_to_immich=upload_to_immich,
                        album=album,
                        source=source,
                        memory_key=memory_key,
                        memory_category=memory_category,
                        automation_attempt_id=automation_attempt_id,
                        dry_run=dry_run,
                        no_render=no_render,
                        accept_any_provenance=accept_any_provenance,
                    )

                _print_generation_result(
                    dry_run=dry_run,
                    no_render=no_render,
                    result_path=result_path,
                    should_upload=should_upload,
                    album_name=album_name,
                )

        except ImmichAPIError as e:
            print_error(f"Immich API error: {e}")
            sys.exit(1)
        except GenerationError as e:
            print_error(str(e))
            sys.exit(1)
        except click.ClickException:
            raise
        except Exception as e:  # WHY: CLI top-level error boundary — sanitizes and displays error
            from immich_memories.security import sanitize_error_message

            print_error(f"Error: {sanitize_error_message(str(e))}")
            sys.exit(1)

    # Register analyze and export-project commands from separate module
    from immich_memories.cli._analyze_export import register_analyze_export_commands

    register_analyze_export_commands(main)
