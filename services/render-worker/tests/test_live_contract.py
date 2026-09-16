"""A remote cut keeps the certified source lineage of a stitched Live carrier."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_worker import _settle

from conftest import AUTH, render_request_body, stub_artifact, worker_app


def live_body(*, titles=None, **settings):
    from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry

    still_a, still_b, video_a, video_b = [str(uuid4()) for _ in range(4)]
    material = LiveRenderMaterial(
        (
            LiveSourceEntry(still_a, video_a, 0.0, 0.0, 1.5),
            LiveSourceEntry(still_b, video_b, 1.0, 0.5, 2.0),
        )
    )
    body = render_request_body(
        clips=[{"asset_id": still_a, "start": 0.5, "end": 2.5, "render_mode": "motion"}],
        certified_content_intervals={still_a: [0.5, 2.5]},
        titles=titles,
        **settings,
    )
    body["plan"]["clips"][0]["live"] = {
        "version": "editorial-live-render-v1",
        "material": material.as_dict(),
        "selected_interval": [0.5, 2.5],
    }
    return body, material


class LiveAssets:
    def __init__(self, material):
        self.material = material

    def get_asset(self, asset_id):
        from immich_memories.api.models import Asset

        entry = next(row for row in self.material.source_entries if row.still_id == asset_id)
        return Asset(
            id=entry.still_id,
            type="IMAGE",
            livePhotoVideoId=entry.video_id,
            originalFileName="live.heic",
            fileCreatedAt="2026-01-01T12:00:00Z",
            fileModifiedAt="2026-01-01T12:00:00Z",
            updatedAt="2026-01-01T12:00:00Z",
        )


def test_a_live_job_keeps_its_sources_and_exact_selected_interval(tmp_path):
    from immich_memories_render_worker.native_plan import generation_params

    from immich_memories.processing.editorial_live_render import validate_editorial_live_clip

    body, material = live_body(
        titles={"animated_background": False, "use_first_name_only": False},
        output={"orientation": "square", "quality": "fast", "hdr_mode": "auto"},
        options={"scale_mode": "fit", "privacy_mode": True, "photo_duration": 2.5},
        memory={"person_name": "Example Person", "preset_params": {"birthday_age": 10}},
    )
    body["plan"]["clips"][0].update(
        audio_categories=["speech", "music"],
        llm_emotion="happy",
        rotation_override=90,
    )
    rendered = []

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            # WHY: the HTTP job contract needs asset metadata, not an external Immich library.
            params = generation_params(request, directory, LiveAssets(material), progress)
            rendered.append(params)
            return stub_artifact(directory)

    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
        assert response.status_code == 202, response.text
        status = _settle(client, response.json()["job_id"])
        assert status.status_code == 200, status.text
        assert status.json()["state"] == "ready", status.text

    assert len(rendered) == 1
    params = rendered[0]
    clip = params.clips[0]
    assert validate_editorial_live_clip(clip) == material
    assert clip.editorial_live_manifest == body["plan"]["clips"][0]["live"]
    assert params.clip_segments == {clip.asset.id: (0.5, 2.5)}
    assert [(row.start_time, row.end_time) for row in params.editorial_selections] == [(0.5, 2.5)]
    assert clip.audio_categories == ["speech", "music"]
    assert clip.llm_emotion == "happy"
    assert params.clip_rotations == {clip.asset.id: 90}
    assert params.output_orientation == "square"
    assert params.config.output.quality == "fast"
    assert params.config.output.hdr_mode.value == "auto"
    assert params.scale_mode == "fit" and params.privacy_mode
    assert params.config.photos.duration == 2.5
    assert not params.config.title_screens.animated_background
    assert not params.config.title_screens.use_first_name_only
    assert params.person_name == "Example Person"
    assert params.memory_preset_params == {"birthday_age": 10}


@pytest.mark.parametrize("changed", ["directive", "certificate", "missing_intervals"])
def test_changed_live_trims_are_refused_before_rendering(tmp_path, changed):
    body, _ = live_body()
    chosen = body["plan"]["clips"][0]
    if changed == "directive":
        chosen["end"] = 2.0
    elif changed == "certificate":
        chosen["live"]["selected_interval"] = [0.5, 2.0]
    else:
        body["certified_content_intervals"] = {}

    class Renderer:
        def health(self):
            return {"ready": True}

    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 409
