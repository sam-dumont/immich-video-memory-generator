"""The downloaded container, not its library filename, selects the decoder."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError

from immich_memories.photos import animator


@pytest.mark.parametrize(
    ("image_format", "suffix"), [("JPEG", ".HEIC"), ("PNG", ".jpg"), ("JPEG", ".unknown")]
)
def test_native_bytes_keep_the_fast_path_despite_the_suffix(tmp_path, image_format, suffix):
    source = tmp_path / f"source{suffix}"
    Image.new("RGB", (48, 32), "blue").save(source, image_format)
    before = source.read_bytes()

    prepared = animator.prepare_photo_source(source, tmp_path, max_size=(96, 96))

    assert prepared.path == source
    assert (prepared.width, prepared.height) == (48, 32)
    assert not prepared.has_gain_map
    assert source.read_bytes() == before


def test_mislabeled_jpeg_is_capped_without_modifying_the_original(tmp_path):
    source = tmp_path / "source.heic"
    Image.new("RGB", (600, 400), "blue").save(source, "JPEG")
    before = hashlib.sha256(source.read_bytes()).digest()

    prepared = animator.prepare_photo_source(source, tmp_path, max_size=(96, 96))

    assert prepared.path != source
    assert (prepared.width, prepared.height) == (96, 64)
    assert hashlib.sha256(source.read_bytes()).digest() == before
    with Image.open(prepared.path) as image:
        assert image.format == "JPEG"
        assert image.size == (96, 64)


@pytest.fixture
def heif_with_jpeg_suffix(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    source = tmp_path / "source.jpg"
    pillow_heif.from_pillow(Image.new("RGB", (48, 32), "blue")).save(source, quality=80)
    assert pillow_heif.is_supported(source)
    return source


def test_real_heif_uses_its_converter_and_cap_with_a_jpeg_suffix(heif_with_jpeg_suffix, tmp_path):
    prepared = animator.prepare_photo_source(heif_with_jpeg_suffix, tmp_path, max_size=(24, 24))

    assert prepared.path != heif_with_jpeg_suffix
    assert (prepared.width, prepared.height) == (24, 16)
    with Image.open(prepared.path) as image:
        assert image.format == "JPEG"


def test_heif_dispatch_preserves_the_hdr_converter_result(
    heif_with_jpeg_suffix, tmp_path, monkeypatch
):
    expected = animator.PreparedPhoto(tmp_path / "hdr.png", 24, 16, True, 812)
    calls = []

    def convert(source, work_dir, *, max_size):
        calls.append((source, work_dir, max_size))
        return expected

    monkeypatch.setattr(animator, "_convert_heif", convert)

    assert (
        animator.prepare_photo_source(heif_with_jpeg_suffix, tmp_path, max_size=(24, 24))
        is expected
    )
    assert calls == [(heif_with_jpeg_suffix, tmp_path, (24, 24))]


def test_mislabeled_ultrahdr_jpeg_retains_its_gain_map_and_cap(tmp_path):
    fixture = Path(__file__).parent / "fixtures/hdr_samples/gain_mapped-photo-tokyo.jpg"
    if not fixture.exists():
        pytest.skip("UltraHDR fixture not available")
    source = tmp_path / "source.HEIC"
    source.write_bytes(fixture.read_bytes())

    prepared = animator.prepare_photo_source(source, tmp_path, max_size=(128, 128))

    assert prepared.has_gain_map
    assert prepared.peak_nits > 203
    assert max(prepared.width, prepared.height) <= 128
    assert prepared.path.suffix == ".png"


def test_other_pillow_formats_still_use_the_conversion_fallback(tmp_path):
    source = tmp_path / "source.unknown"
    Image.new("RGB", (48, 32), "blue").save(source, "PPM")

    prepared = animator.prepare_photo_source(source, tmp_path)

    assert prepared.path != source
    with Image.open(prepared.path) as image:
        assert image.format == "JPEG"
        assert image.size == (48, 32)


def test_unreadable_source_is_not_treated_as_a_supported_photo(tmp_path):
    source = tmp_path / "source.HEIC"
    source.write_bytes(b"not an image")

    with pytest.raises(UnidentifiedImageError):
        animator.prepare_photo_source(source, tmp_path)


def _rotated_ultrahdr_jpeg(path: Path) -> None:
    """A gain-mapped JPEG stored landscape with an EXIF tag saying it displays portrait.

    The stored left half is red and the gain map brightens only that half, so the
    output shows whether the base image and its gain map were turned together.
    """
    import io

    stored = Image.new("RGB", (64, 32), "blue")
    stored.paste((255, 0, 0), (0, 0, 32, 32))
    exif = Image.Exif()
    exif[274] = 6  # WHY: 6 = rotate 90 degrees clockwise to display
    primary = io.BytesIO()
    stored.save(primary, "JPEG", quality=95, exif=exif, comment=b"hdrgm:Version=1.0")
    gain = Image.new("L", (64, 32), 0)
    gain.paste(255, (0, 0, 32, 32))
    gain_bytes = io.BytesIO()
    gain.save(gain_bytes, "JPEG", quality=95)
    path.write_bytes(primary.getvalue() + gain_bytes.getvalue())


def test_ultrahdr_jpeg_is_turned_upright_with_its_gain_map(tmp_path):
    import cv2

    source = tmp_path / "rotated.jpg"
    _rotated_ultrahdr_jpeg(source)

    prepared = animator.prepare_photo_source(source, tmp_path)

    assert prepared.has_gain_map
    assert (prepared.width, prepared.height) == (32, 64)
    pixels = cv2.cvtColor(cv2.imread(str(prepared.path), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
    top, bottom = pixels[8, 16], pixels[56, 16]
    assert top[0] > top[2], "the stored left (red) half displays on top"
    assert bottom[2] > bottom[0], "the stored right (blue) half displays at the bottom"
    assert top[0] > bottom[2], "the gain map brightened the same half it was stored with"
