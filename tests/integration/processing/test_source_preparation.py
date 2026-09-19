"""The production source queue prepares mixed media with real decoding and encoding."""

import shutil
from threading import get_ident

import pytest
from PIL import Image

from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_clips import _extract_clips
from immich_memories.processing.download_coordinator import DownloadCoordinator
from immich_memories.processing.output_canvas import OutputCanvas
from immich_memories.processing.probe_cache import ProbeCache
from tests.conftest import make_clip

pytestmark = pytest.mark.integration


def test_mixed_sources_prepare_in_workers_and_keep_editorial_order(tmp_path, no_audio_clip):
    image = tmp_path / "source.jpg"
    Image.new("RGB", (640, 360), (90, 160, 60)).save(image)
    video = make_clip("video", width=1280, height=720, duration=2)
    video.local_path = no_audio_clip
    photo = make_clip("photo", width=640, height=360, duration=2)
    photo.asset.type = AssetType.IMAGE
    photo.asset.original_file_name = "source.jpg"
    owners, closed = [], []

    # WHY: Downloads write fixture bytes; all source preparation and FFmpeg calls are real.
    class Client:
        def __init__(self):
            self.owner = get_ident()
            owners.append(self.owner)

        def download_asset(self, asset_id, destination):
            assert asset_id == "photo"
            assert get_ident() == self.owner
            shutil.copyfile(image, destination)

        def close(self):
            assert get_ident() == self.owner
            closed.append(self.owner)

    config = Config()
    config.hardware.enabled = False
    config.photos.duration = 2
    params = GenerationParams(
        clips=[photo, video],
        output_path=tmp_path / "film.mp4",
        config=config,
        output_canvas=OutputCanvas(320, 180, "landscape"),
        clip_segments={"video": (0, 1.5)},
    )
    clips = _extract_clips(
        params,
        None,
        tmp_path,
        download_coordinator=DownloadCoordinator(Client, None, max_workers=2),
    )
    assert [clip.asset_id for clip in clips] == ["photo", "video"]
    assert [clip.is_photo for clip in clips] == [True, False]
    assert len(owners) == 2
    assert sorted(owners) == sorted(closed)
    probes = [ProbeCache().get(clip.path) for clip in clips]
    assert probes[0].resolution == (320, 180)
    assert probes[0].duration_seconds == pytest.approx(2, abs=0.1)
    assert probes[1].duration_seconds >= 1.5
    assert clips[1].duration == pytest.approx(1.5)
