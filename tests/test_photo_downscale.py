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

FIXTURES = Path(__file__).parent / "fixtures" / "hdr_samples"
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


@pytest.mark.skipif(
    not (FIXTURES / "gain_mapped-photo-tokyo.jpg").exists(), reason="fixture not available"
)
def test_a_gain_mapped_photo_is_capped_and_still_hdr(tmp_path) -> None:
    """The cap is applied before the gain-map maths, which is per-pixel and so
    survives it. Losing HDR here would be worse than the OOM it prevents."""
    result = prepare_photo_source(FIXTURES / "gain_mapped-photo-tokyo.jpg", tmp_path, max_size=CAP)

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
