"""Photos are capped near the output size before any array work.

A 24 MP HEIC held as three float32 copies peaks around 0.9 GB and a 48 MP one
OOMs under the compose 4 GB limit. Ken Burns never samples more than about 2x
the output, so anything above that is memory spent on detail the encoder
throws away.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from immich_memories.photos.animator import prepare_photo_source
from tests.conftest import HDR_SAMPLES, lfs_fixture

CAP = (1024, 1024)


def _dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def test_an_oversized_jpeg_is_capped(tmp_path) -> None:
    source = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 4000), "blue").save(source, "JPEG")

    result = prepare_photo_source(source, tmp_path, max_size=CAP)

    assert max(_dimensions(result.path)) <= max(CAP)
    assert (result.width, result.height) == _dimensions(result.path)


def test_aspect_ratio_survives_the_cap(tmp_path) -> None:
    source = tmp_path / "wide.jpg"
    Image.new("RGB", (6000, 3000), "blue").save(source, "JPEG")

    result = prepare_photo_source(source, tmp_path, max_size=CAP)

    width, height = _dimensions(result.path)
    assert width / height == pytest.approx(2.0, rel=0.02)


def test_a_photo_already_under_the_cap_is_untouched(tmp_path) -> None:
    """No pointless re-encode, and no upscaling of a genuinely small photo."""
    source = tmp_path / "small.jpg"
    Image.new("RGB", (800, 600), "blue").save(source, "JPEG")

    result = prepare_photo_source(source, tmp_path, max_size=CAP)

    assert result.path == source
    assert (result.width, result.height) == (800, 600)


def test_no_cap_means_no_downscale(tmp_path) -> None:
    """Callers that did not ask for a cap keep full resolution."""
    source = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 4000), "blue").save(source, "JPEG")

    result = prepare_photo_source(source, tmp_path)

    assert (result.width, result.height) == (6000, 4000)


@pytest.mark.parametrize("max_size", [None, (1024, 1024), (320, 180)])
@pytest.mark.parametrize(
    "orientation,corners",
    [(2, "GRYB"), (3, "YBGR"), (4, "BYRG"), (5, "RBGY"), (6, "BRYG"), (7, "YGBR"), (8, "GYRB")],
)
def test_renderer_pixels_follow_exif_before_the_cap(tmp_path, max_size, orientation, corners):
    import cv2
    import numpy as np
    from PIL import ImageDraw

    colours = {"R": (255, 0, 0), "G": (0, 255, 0), "B": (0, 0, 255), "Y": (255, 255, 0)}
    picture = Image.new("RGB", (720, 480))
    draw = ImageDraw.Draw(picture)
    for colour, box in zip(
        "RGBY",
        [(0, 0, 359, 239), (360, 0, 719, 239), (0, 240, 359, 479), (360, 240, 719, 479)],
        strict=True,
    ):
        draw.rectangle(box, fill=colours[colour])
    exif = Image.Exif()
    exif[274] = orientation
    source = tmp_path / "rotated.jpg"
    picture.save(source, quality=98, exif=exif)
    original = source.read_bytes()

    prepared = prepare_photo_source(source, tmp_path, max_size=max_size)

    # The animator uses IMREAD_UNCHANGED: verify pixels, not a viewer's EXIF correction.
    pixels = cv2.cvtColor(cv2.imread(str(prepared.path), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
    height, width = pixels.shape[:2]
    assert (prepared.width, prepared.height) == (width, height)
    assert width / height == pytest.approx(2 / 3 if orientation >= 5 else 3 / 2, rel=0.01)
    if max_size:
        assert width <= max_size[0] and height <= max_size[1]
    else:
        assert sorted((width, height)) == [480, 720]
    for colour, (x, y) in zip(
        corners, [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)], strict=True
    ):
        np.testing.assert_allclose(pixels[int(y * height), int(x * width)], colours[colour], atol=5)
    with Image.open(prepared.path) as stored:
        assert stored.getexif().get(274, 1) == 1
    assert source.read_bytes() == original


def test_a_gain_mapped_photo_is_capped_and_still_hdr(tmp_path) -> None:
    """The cap is applied before the gain-map maths, which is per-pixel and so
    survives it. Losing HDR here would be worse than the OOM it prevents."""
    photo = lfs_fixture(HDR_SAMPLES / "gain_mapped-photo-tokyo.jpg")
    result = prepare_photo_source(photo, tmp_path, max_size=CAP)

    assert max(_dimensions(result.path)) <= max(CAP)
    assert result.has_gain_map


def test_the_pipeline_caps_the_source_at_1_5x_output(monkeypatch, tmp_path) -> None:
    """#423: the renderer samples at most output x 1.12 zoom x 1.26 margin
    = 1.41x; a 2.0x cap paid 0.63s and 0.32 GB per photo for pixels the
    internal resize threw away. 1.5x covers the worst case with headroom."""
    from immich_memories.photos import photo_pipeline

    seen = {}

    def spy_prepare(path, work_dir, max_size=None):  # WHY: capture the cap, skip real HEIC work
        seen["max_size"] = max_size
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(photo_pipeline, "prepare_photo_source", spy_prepare)
    from unittest.mock import MagicMock

    asset = MagicMock(id="a1", original_file_name="p.jpg")
    import contextlib

    with contextlib.suppress(RuntimeError):
        photo_pipeline._render_single_photo(  # noqa: SLF001 — the cap lives on this path
            asset,
            config=MagicMock(),
            target_w=3840,
            target_h=2160,
            work_dir=tmp_path,
            download_fn=lambda _id, p: p.write_bytes(b"x"),
        )

    assert seen["max_size"] == (5760, 3240)  # 1.5x of 4K, not 2.0x
