"""The app and worker agree on the cut at their HTTP boundary."""

from uuid import uuid4


def manual_params(tmp_path):
    from immich_memories.api.models import Asset, VideoClipInfo
    from immich_memories.config import Config
    from immich_memories.generate import GenerationParams

    asset = Asset(
        id=str(uuid4()),
        type="VIDEO",
        originalFileName="source.mp4",
        fileCreatedAt="2026-01-01T12:00:00Z",
        fileModifiedAt="2026-01-01T12:00:00Z",
        updatedAt="2026-01-01T12:00:00Z",
        duration="00:00:10",
    )
    config = Config()
    config.immich.url = "http://immich.invalid"
    config.immich.api_key = "test-scoped-key"
    config.title_screens.enabled = False
    return GenerationParams(
        clips=[VideoClipInfo(asset=asset, duration_seconds=10, width=1920, height=1080)],
        output_path=tmp_path / "film.mp4",
        config=config,
        target_duration_seconds=30,
        output_orientation="square",
        output_resolution="720p",
        clip_segments={asset.id: (2.5, 5.75)},
        memory_key_override="manual-cut",
    )


def round_trip(params, tmp_path):
    from immich_memories_render_worker.admission import certify_envelope
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native_plan import generation_params

    from immich_memories.processing.remote_render_plan import build_render_request

    body = build_render_request(params)
    request = RenderRequest.model_validate(body)
    certify_envelope(request)

    class Client:
        def get_asset(self, asset_id):
            return next(clip.asset for clip in params.clips if clip.asset.id == asset_id)

    # WHY: this contract round trip needs the source metadata, not a running Immich server.
    return generation_params(request, tmp_path / "worker", Client(), lambda *_: None), body


def test_app_envelope_preserves_manual_cut_and_square_canvas(tmp_path):
    params = manual_params(tmp_path)
    asset = params.clips[0].asset
    received, body = round_trip(params, tmp_path)
    assert [clip.asset.id for clip in received.clips] == [asset.id]
    assert received.clip_segments == {asset.id: (2.5, 5.75)}
    assert received.output_orientation == "square"
    assert received.output_resolution == "720p"
    assert received.target_duration_seconds == 30
    assert received.editorial_render_timing == body["timing"]


def test_an_editorial_directive_keeps_its_cut_without_a_manual_segment_map(tmp_path):
    from immich_memories.analysis.editorial_planner import EditorialSelection

    params = manual_params(tmp_path)
    asset_id = params.clips[0].asset.id
    params.clip_segments = {}
    params.editorial_selections = (EditorialSelection(asset_id, 2.5, 5.75, "motion"),)
    received, _ = round_trip(params, tmp_path)
    assert received.clip_segments == {asset_id: (2.5, 5.75)}


def test_a_directive_with_omitted_bounds_uses_the_full_source(tmp_path):
    from immich_memories.analysis.editorial_planner import EditorialSelection

    params = manual_params(tmp_path)
    asset_id = params.clips[0].asset.id
    params.clip_segments = {}
    params.editorial_selections = (EditorialSelection(asset_id, None, None, None),)
    received, _ = round_trip(params, tmp_path)
    assert received.clip_segments == {asset_id: (0, 10)}


def test_remote_render_retains_film_settings_and_source_audio_markers(tmp_path):
    from immich_memories.processing.encoding_plan import HdrMode

    params = manual_params(tmp_path)
    params.config.title_screens.enabled = True
    params.config.title_screens.animated_background = False
    params.config.title_screens.show_decorative_lines = True
    params.config.title_screens.use_first_name_only = False
    params.config.output.hdr_mode = HdrMode.AUTO
    params.config.output.quality = "fast"
    params.config.photos.duration = 2.5
    params.scale_mode = "fit"
    params.add_date_overlay = True
    params.add_place_overlay = True
    params.privacy_mode = True
    params.person_name = "Example Person"
    params.memory_preset_params = {"birthday_age": 10}
    clip = params.clips[0]
    clip.audio_categories = ["speech", "music"]
    clip.llm_emotion = "happy"
    params.clip_rotations = {clip.asset.id: 90}

    received, _ = round_trip(params, tmp_path)
    assert received.config.title_screens == params.config.title_screens
    assert received.config.photos.duration == 2.5
    assert received.config.output.hdr_mode == HdrMode.AUTO
    assert received.config.output.quality == "fast"
    assert received.scale_mode == "fit"
    assert received.add_date_overlay and received.add_place_overlay and received.privacy_mode
    assert received.person_name == params.person_name
    assert received.memory_preset_params == params.memory_preset_params
    assert received.clip_rotations == params.clip_rotations
    assert received.clips[0].audio_categories == ["speech", "music"]
    assert received.clips[0].llm_emotion == "happy"


def test_an_explicit_hevc_output_is_still_hevc_on_the_worker(tmp_path):
    from immich_memories.processing.encoding_plan import resolve_output_selection

    params = manual_params(tmp_path)
    params.output_format = "h265"
    received, body = round_trip(params, tmp_path)
    assert body["output"]["codec"] == "h265"
    selected = resolve_output_selection(
        config_codec=received.config.output.codec,
        config_container=received.config.output.format,
        format_override=received.output_format,
    )
    assert selected.codec.value == "h265"


def test_manual_photo_without_a_video_duration_uses_its_chosen_hold(tmp_path):
    from immich_memories.api.models import AssetType

    params = manual_params(tmp_path)
    params.clips[0].asset.type = AssetType.IMAGE
    params.clips[0].duration_seconds = 0
    params.clip_segments = {}
    params.target_duration_seconds = None
    params.config.photos.duration = 2.5
    received, body = round_trip(params, tmp_path)
    assert body["plan"]["clips"][0]["render_mode"] == "still"
    assert received.clip_segments == {params.clips[0].asset.id: (0, 2.5)}


def test_the_workers_place_captions_use_the_apps_home_location(tmp_path):
    params = manual_params(tmp_path)
    params.add_place_overlay = True
    params.config.trips.homebase_latitude = 40.0
    params.config.trips.homebase_longitude = -70.0
    received, _ = round_trip(params, tmp_path)
    assert received.config.trips.homebase_latitude == 40.0
    assert received.config.trips.homebase_longitude == -70.0


def test_a_manually_selected_live_carrier_keeps_motion_and_its_stitched_duration(tmp_path):
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native_plan import generation_params
    from test_live_contract import LiveAssets, live_body

    body, material = live_body()
    params = generation_params(
        RenderRequest.model_validate(body), tmp_path, LiveAssets(material), lambda *_: None
    )
    params.clips[0].editorial_live_manifest = None
    params.editorial_render_timing = None
    params.editorial_selections = ()
    received, envelope = round_trip(params, tmp_path)
    chosen = envelope["plan"]["clips"][0]
    assert chosen["render_mode"] == "motion"
    assert chosen["live"]["material"] == material.as_dict()
    assert received.clips[0].duration_seconds == material.duration_seconds
    assert chosen["live"]["selected_interval"] == [0.5, 2.5]
