"""Clip review/refinement UI for Step 2: Clip Review."""

from __future__ import annotations

import base64

from nicegui import run, ui

from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.pages.step2_helpers import (
    _download_immich_preview,
    _get_preview_path,
    format_duration,
    get_thumbnail,
)
from immich_memories.ui.state import get_app_state


def _render_clip_thumbnail(clip: VideoClipInfo) -> None:
    """Render the initial thumbnail for a clip (sync, placeholder)."""
    thumb = get_thumbnail(clip.asset.id)
    if thumb:
        b64 = base64.b64encode(thumb).decode()
        ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-24 object-cover rounded")
    else:
        ui.element("div").classes("w-full h-24 bg-gray-200 rounded")


def _make_preview_loader(video_aid: str, container: ui.element, vid_id: str):
    """Return async loader that swaps thumbnail with video preview."""

    async def load_preview():
        from nicegui import app as nicegui_app

        preview_path = await run.io_bound(_get_preview_path, video_aid)
        if not preview_path:
            preview_path = await run.io_bound(_download_immich_preview, video_aid)

        if preview_path:
            preview_url = nicegui_app.add_media_file(local_file=preview_path)
            container.clear()
            with container:
                video_el = ui.video(preview_url).classes("w-full rounded")
                video_el.props(f'muted playsinline id="{vid_id}"')

    return load_preview


def _make_toggle_handler(asset_id: str, state, update_summary):
    """Return a checkbox handler that toggles selection of a clip."""

    def handler(e):
        value = e.value if hasattr(e, "value") else e
        if value:
            state.selected_clip_ids.add(asset_id)
        else:
            state.selected_clip_ids.discard(asset_id)
        update_summary()

    return handler


def _make_range_handler(asset_id: str, vid_id: str | None, state, update_summary):
    """Return a range slider handler."""

    def handler(e):
        value = e.value if hasattr(e, "value") else e
        state.clip_segments[asset_id] = (value["min"], value["max"])
        update_summary()
        if vid_id:
            ui.run_javascript(f'''
                const v = document.getElementById("{vid_id}");
                if (v) {{ v.currentTime = {value["min"]}; }}
            ''')

    return handler


def _make_play_handler(vid_id: str, asset_id: str, default_start: float, default_end: float, state):
    """Return a play-preview button handler."""

    def handler():
        s, e = state.clip_segments.get(asset_id, (default_start, default_end))
        ui.run_javascript(f'''
            const v = document.getElementById("{vid_id}");
            if (v) {{
                v.currentTime = {s};
                v.play();
                const endTime = {e};
                const checkEnd = setInterval(() => {{
                    if (v.currentTime >= endTime) {{
                        v.pause();
                        clearInterval(checkEnd);
                    }}
                }}, 50);
            }}
        ''')

    return handler


def _make_quick_btn_handler(
    asset_id: str,
    new_start: float,
    new_end: float,
    vid_id: str | None,
    slider,
    state,
    update_summary,
):
    """Return a quick-range button handler."""

    def handler():
        state.clip_segments[asset_id] = (new_start, new_end)
        slider.value = {"min": new_start, "max": new_end}
        update_summary()
        if vid_id:
            ui.run_javascript(f'''
                const v = document.getElementById("{vid_id}");
                if (v) {{ v.currentTime = {new_start}; }}
            ''')

    return handler


def _render_quick_range_buttons(
    clip: VideoClipInfo,
    video_id: str,
    range_slider,
    start: float,
    end: float,
    state,
    update_summary,
) -> None:
    """Render the quick range buttons (First 5s, Last 5s, Middle 5s, Full)."""
    duration = float(clip.duration_seconds or 10)
    mid = duration / 2
    with ui.row().classes("gap-2 mt-2"):
        ui.button(
            "Preview",
            on_click=_make_play_handler(video_id, clip.asset.id, start, end, state),
        ).props("outline size=sm color=primary").classes("font-semibold")
        ui.button(
            "First 5s",
            on_click=_make_quick_btn_handler(
                clip.asset.id,
                0.0,
                min(5.0, duration),
                video_id,
                range_slider,
                state,
                update_summary,
            ),
        ).props("outline size=sm")
        ui.button(
            "Last 5s",
            on_click=_make_quick_btn_handler(
                clip.asset.id,
                max(0, duration - 5.0),
                duration,
                video_id,
                range_slider,
                state,
                update_summary,
            ),
        ).props("outline size=sm")
        ui.button(
            "Middle 5s",
            on_click=_make_quick_btn_handler(
                clip.asset.id,
                max(0, mid - 2.5),
                min(duration, mid + 2.5),
                video_id,
                range_slider,
                state,
                update_summary,
            ),
        ).props("outline size=sm")
        ui.button(
            "Full clip",
            on_click=_make_quick_btn_handler(
                clip.asset.id, 0.0, duration, video_id, range_slider, state, update_summary
            ),
        ).props("outline size=sm")


def _make_rotation_handler(asset_id: str, opts: dict, state):
    """Return a rotation select handler."""

    def handler(e):
        value = e.value if hasattr(e, "value") else e
        state.clip_rotations[asset_id] = opts.get(value)

    return handler


def _render_clip_controls(
    clip: VideoClipInfo, video_id: str, start: float, end: float, state, update_summary
) -> None:
    """Render the range slider, quick buttons, rotation, and metadata."""
    duration = float(clip.duration_seconds or 10)
    rotation_options = {
        "Auto": None,
        "0°": 0,
        "90° CW": 90,
        "180°": 180,
        "90° CCW": 270,
    }
    current_rotation = state.clip_rotations.get(clip.asset.id)
    current_opt = next(
        (name for name, val in rotation_options.items() if val == current_rotation), "Auto"
    )

    with ui.column().classes("flex-1"):
        ui.label("Select range").classes("text-sm text-gray-500")
        range_slider = ui.range(
            min=0, max=duration, step=0.1, value={"min": start, "max": end}
        ).classes("w-full")
        range_slider.on_value_change(
            _make_range_handler(clip.asset.id, video_id, state, update_summary)
        )

        _render_quick_range_buttons(clip, video_id, range_slider, start, end, state, update_summary)

        rotation_select = ui.select(
            options=list(rotation_options.keys()),
            label="Rotation",
            value=current_opt,
        ).classes("w-32")
        rotation_select.on_value_change(
            _make_rotation_handler(clip.asset.id, rotation_options, state)
        )

        meta_parts = []
        if clip.width and clip.height:
            meta_parts.append(f"{clip.width}x{clip.height}")
        if clip.fps:
            meta_parts.append(f"{clip.fps:.0f}fps")
        if clip.codec:
            meta_parts.append(clip.codec)
        if meta_parts:
            ui.label(" | ".join(meta_parts)).classes("text-xs text-gray-400")


def _render_review_clip_row(
    clip: VideoClipInfo,
    idx: int,
    state,
    update_summary,
) -> None:
    """Render a single clip refinement row in the review UI."""
    duration = float(clip.duration_seconds or 10)
    start, end = state.clip_segments.get(clip.asset.id, (0.0, min(5.0, duration)))
    start = float(start)
    end = float(end)
    segment_duration = end - start

    # Build title
    date_str = clip.asset.file_created_at.strftime("%B %d, %Y")
    title_parts = []
    if clip.asset.is_favorite:
        title_parts.append("\u2605")
    if clip.is_hdr:
        title_parts.append(f"[{clip.hdr_format}]")
    title_parts.extend(
        (
            date_str,
            f"\u2022 Using {format_duration(segment_duration)} of {format_duration(duration)}",
        )
    )

    with ui.expansion(" ".join(title_parts), value=idx < 5).classes("w-full"):  # noqa: SIM117
        with ui.row().classes("w-full gap-4"):
            with ui.column().classes("w-64"):
                video_id = f"preview_{clip.asset.id.replace('-', '_')}"

                with ui.element("div").classes("w-full") as video_container:
                    _render_clip_thumbnail(clip)

                ui.timer(
                    0.1,
                    _make_preview_loader(clip.video_asset_id, video_container, video_id),
                    once=True,
                )

                keep_checkbox = ui.checkbox("Include in compilation", value=True)
                keep_checkbox.on_value_change(
                    _make_toggle_handler(clip.asset.id, state, update_summary)
                )

            _render_clip_controls(clip, video_id, start, end, state, update_summary)


def _render_summary_metrics(
    summary_container: ui.element, selected_clips, state, calc_total_duration, target_duration
) -> None:
    """Render summary metrics row."""
    summary_container.clear()
    total_selected = calc_total_duration()
    diff = total_selected - target_duration
    with summary_container:
        with ui.column().classes("items-center"):
            ui.label("Selected Clips").classes("text-sm text-gray-500")
            ui.label(
                str(len([c for c in selected_clips if c.asset.id in state.selected_clip_ids]))
            ).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Total Duration").classes("text-sm text-gray-500")
            ui.label(format_duration(total_selected)).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Target").classes("text-sm text-gray-500")
            ui.label(format_duration(target_duration)).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Difference").classes("text-sm text-gray-500")
            diff_str = f"{'+' if diff > 0 else ''}{format_duration(abs(diff))}"
            ui.label(diff_str).classes("text-2xl font-bold")


def _render_bulk_actions(selected_clips, state, update_summary) -> None:
    """Render quick bulk action buttons."""
    with ui.row().classes("w-full gap-2 mb-4"):

        def set_all_first_5s():
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                state.clip_segments[clip.asset.id] = (0.0, min(5.0, duration))
            update_summary()

        def set_all_middle_5s():
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                mid = duration / 2
                state.clip_segments[clip.asset.id] = (max(0, mid - 2.5), min(duration, mid + 2.5))
            update_summary()

        ui.button("Set all to first 5s", on_click=set_all_first_5s).props("outline")
        ui.button("Set all to middle 5s", on_click=set_all_middle_5s).props("outline")

        custom_sec_input = ui.number("Custom seconds", value=5, min=1, max=30).classes("w-24")

        def set_all_custom():
            custom_sec = float(custom_sec_input.value)
            for clip in selected_clips:
                duration = clip.duration_seconds or 10
                state.clip_segments[clip.asset.id] = (0.0, min(custom_sec, duration))
            update_summary()

        ui.button("Apply", on_click=set_all_custom).props("outline")


def _render_review_nav(state) -> None:
    """Render navigation buttons at the bottom of clip review."""
    with ui.row().classes("w-full gap-4 mt-4"):

        def go_back_selection():
            state.review_selected_mode = False
            ui.navigate.to("/step2")

        def rerun_analysis():
            state.review_selected_mode = False
            state.selected_clip_ids = set()
            state.clip_segments = {}
            ui.navigate.to("/step2")

        def continue_to_generation():
            if state.selected_clip_ids:
                state.review_selected_mode = False
                state.step = 3
                ui.navigate.to("/step3")
            else:
                ui.notify("Please select at least one clip", type="warning")

        ui.button("Back to Selection", on_click=go_back_selection, icon="arrow_back").props(
            "outline"
        )
        ui.button("Re-run Analysis", on_click=rerun_analysis, icon="refresh").props("outline")
        ui.button(
            "Continue to Generation",
            on_click=continue_to_generation,
            icon="arrow_forward",
        ).props("color=primary")


def _render_review_selected_clips(clips: list[VideoClipInfo]) -> None:
    """Render the review/refinement UI for selected clips only."""
    state = get_app_state()

    ui.label("Review & Refine Selected Clips").classes("text-xl font-semibold")
    ui.label("Adjust time segments, preview clips, and remove any unwanted selections.").classes(
        "text-sm text-gray-500 mb-4"
    )

    selected_clips = [c for c in clips if c.asset.id in state.selected_clip_ids]

    if not selected_clips:
        with ui.card().classes("w-full p-4 bg-yellow-50"):
            ui.label("No clips selected. Return to generate new selections.").classes(
                "text-yellow-700"
            )

        def go_back():
            state.review_selected_mode = False
            ui.navigate.to("/step2")

        ui.button("Back to Clip Selection", on_click=go_back, icon="arrow_back")
        return

    avg_clip_duration = state.avg_clip_duration
    for clip in selected_clips:
        if clip.asset.id not in state.clip_segments:
            duration = clip.duration_seconds or 10
            end_time = min(duration, float(avg_clip_duration))
            state.clip_segments[clip.asset.id] = (0.0, end_time)

    def calc_total_duration():
        return sum(
            end - start
            for asset_id, (start, end) in state.clip_segments.items()
            if asset_id in state.selected_clip_ids
        )

    target_duration = state.target_duration * 60

    summary_container = ui.row().classes("w-full gap-8 mb-4")

    def update_summary():
        _render_summary_metrics(
            summary_container, selected_clips, state, calc_total_duration, target_duration
        )

    update_summary()
    ui.separator()

    _render_bulk_actions(selected_clips, state, update_summary)
    ui.separator()

    selected_clips_sorted = sorted(selected_clips, key=lambda c: c.asset.file_created_at)
    for i, clip in enumerate(selected_clips_sorted):
        if clip.asset.id not in state.selected_clip_ids:
            continue
        _render_review_clip_row(clip, i, state, update_summary)

    ui.separator()

    final_duration = calc_total_duration()
    ui.label(f"Final Duration: {format_duration(final_duration)}").classes("text-lg font-semibold")

    _render_review_nav(state)
