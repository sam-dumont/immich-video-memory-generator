"""Photos answer to the same rules about where footage came from.

The source filter lived on the video path only, so a collage somebody
forwarded through a messaging app walked into a year recap while a doorbell
clip beside it was turned away.
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.source_filter import from_an_excluded_source
from immich_memories.api.models import AssetType
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_asset


def _photo(asset_id: str, name: str):
    from immich_memories.api.models import AssetType

    asset = make_asset(asset_id, original_file_name=name)
    asset.type = AssetType.IMAGE
    return asset


def test_a_photo_from_the_camera_roll_is_kept() -> None:
    """The filter has to be a scalpel, not a broom."""
    patterns = AnalysisConfig().exclude_filename_patterns

    assert not from_an_excluded_source("IMG_0809.HEIC", patterns)
    assert not from_an_excluded_source("DSC_4471.JPG", patterns)


def test_the_default_patterns_catch_what_the_camera_roll_did_not_shoot() -> None:
    patterns = AnalysisConfig().exclude_filename_patterns

    assert from_an_excluded_source("RingVideo_6763648097558121116.mp4", patterns)
    assert from_an_excluded_source("IMG-20190105-WA0006.jpg", patterns)
    assert from_an_excluded_source("rpreplay_final1560343200.mp4", patterns)


def test_a_still_with_no_camera_in_its_exif_was_not_shot_here() -> None:
    """Measured across four months of a real library, 5260 assets.

    Stills with no EXIF make: 1498 .jpg named for a messaging app, 34 .png
    downloads, and 9 camera originals that had lost their make. Videos are a
    different story and are left alone — 25 of 224 make-less videos there are
    genuine phone clips, and the filename rule already catches the rest.
    """
    from immich_memories.analysis.source_filter import not_shot_here
    from immich_memories.api.models import AssetType

    received = make_asset("received", original_file_name="IMG_2841.jpg", exif_make=None)
    received.type = AssetType.IMAGE
    shot = make_asset("shot", original_file_name="IMG_1375.HEIC", exif_make="Apple")
    shot.type = AssetType.IMAGE
    clip = make_asset("clip", original_file_name="IMG_1365.MOV", exif_make=None)

    assert not_shot_here(received, patterns=(), stills_need_a_camera=True)
    assert not not_shot_here(shot, patterns=(), stills_need_a_camera=True)
    assert not not_shot_here(clip, patterns=(), stills_need_a_camera=True), (
        "a phone clip loses its make often enough that this rule cannot judge video"
    )


def test_the_camera_rule_can_be_turned_off() -> None:
    """A library of exported or edited originals would lose them to this."""
    from immich_memories.analysis.source_filter import not_shot_here
    from immich_memories.api.models import AssetType

    received = make_asset("received", original_file_name="IMG_2841.jpg", exif_make=None)
    received.type = AssetType.IMAGE

    assert not not_shot_here(received, patterns=(), stills_need_a_camera=False)


def test_pre_smartphone_media_is_considered_despite_modern_source_signals() -> None:
    """An old scan/export is evidence; the editor can decide whether it belongs."""
    from immich_memories.analysis.source_filter import not_shot_here

    historical = make_asset(
        "historical",
        original_file_name="IMG-20030812-WA0001.jpg",
        exif_make=None,
        file_created_at=datetime(2003, 8, 12, tzinfo=UTC),
    )
    historical.type = AssetType.IMAGE

    assert not not_shot_here(
        historical,
        patterns=AnalysisConfig().exclude_filename_patterns,
        stills_need_a_camera=True,
    )


def test_a_starred_photo_passes_whatever_its_filename_says() -> None:
    """Every other hard gate in the pipeline subordinates itself to a star.

    A photo somebody was sent and then went and starred is a photo they chose
    to keep. Dropping it before the favorites guarantee can see it contradicts
    the rule the rest of selection is built on.
    """
    from immich_memories.analysis.source_filter import not_shot_here

    forwarded = make_asset("forwarded", original_file_name="IMG-20190105-WA0006.jpg")
    forwarded.type = AssetType.IMAGE
    forwarded.is_favorite = True

    doorbell = make_asset("doorbell", original_file_name="RingVideo_1.mp4")
    doorbell.is_favorite = True

    patterns = AnalysisConfig().exclude_filename_patterns
    assert not not_shot_here(forwarded, patterns=patterns, stills_need_a_camera=True)
    assert not not_shot_here(doorbell, patterns=patterns, stills_need_a_camera=True)


def test_the_messaging_glob_does_not_match_a_place_called_wa() -> None:
    """WA is a state abbreviation as well as a messaging app's marker.

    '*-WA[0-9]*' matched 'Olympia-WA2019.jpg' — a photograph of Washington,
    dropped for its filename.
    """
    from immich_memories.analysis.source_filter import from_an_excluded_source

    patterns = AnalysisConfig().exclude_filename_patterns

    assert from_an_excluded_source("IMG-20190105-WA0006.jpg", patterns)
    assert from_an_excluded_source("VID-20190701-WA0000.mp4", patterns)
    assert not from_an_excluded_source("Olympia-WA2019.jpg", patterns)
    assert not from_an_excluded_source("Seattle-WA98101.jpg", patterns)


def _captured_filter(*, primaries: str) -> str:
    """The zscale expression a gain-mapped photo with these primaries is encoded through."""
    from immich_memories.photos.photo_pipeline import photo_filter_chain

    _, vf = photo_filter_chain(
        gain_map_hdr=True, has_zscale=True, peak_nits=1000, primaries=primaries
    )
    return vf


def test_a_display_p3_photo_is_not_encoded_as_if_it_were_bt709() -> None:
    """iPhone HEICs are Display P3, and calling them BT.709 desaturates them.

    pillow-heif hands back the RAW P3 values -- verified against Apple's own
    colour-managed render of a real photograph: as-is they differ by 10.77/255
    across the most saturated 5% of pixels, and converting them P3->sRGB first
    brings that to 0.91/255. A whole-image mean hides this at 3.3/255, because
    most of any photograph sits where the two gamuts agree.

    So hardcoding pin=bt709 described those values as the narrower gamut.
    Measured as chromaticity error against Apple's render, luminance divided out
    so tone mapping cannot confound it: **0.02544 naming them bt709 versus
    0.00518 naming them smpte432** over the saturated tenth of the frame.

    Naming beats converting: BT.2020 contains P3, so a correctly named source
    arrives whole, while a P3->sRGB conversion first would clip exactly the
    colours this is trying to keep. The video path already works this way --
    `_detect_color_primaries` reads the source and passes `pin=<its primaries>`.
    """
    assert "pin=smpte432" in _captured_filter(primaries="smpte432")
    assert "pin=bt709" not in _captured_filter(primaries="smpte432")
    assert "pin=bt709" in _captured_filter(primaries="bt709")


def test_a_gain_mapped_photo_is_encoded_pq_not_hlg() -> None:
    """An Apple HDR photograph is PQ. Encoding it HLG lifts its shadows.

    HLG is a relative system: the signal is scene-referred and the display is
    meant to apply an OOTF to bring it back down. It also spends enormous code
    space on the bottom of the range -- its OETF is sqrt(3L) below 1/12 of peak
    -- so a 12-nit shadow encodes as 0.173 rather than 0.01. Anything in the
    chain that does not apply the OOTF shows that as 17% of peak, around 200
    nits. The shadows come up and no amount of correctness upstream brings them
    back down, which is exactly what the owner saw after the gain-map maths had
    been verified to 2.23% against CoreImage.

    PQ is absolute: a code value names a luminance in nits and nothing
    downstream reinterprets it. It is also what the source actually is --
    CoreImage reports Apple's own expansion as "Display P3, SMPTE ST 2084 PQ",
    and the reference decoder ends at eotf_inverse_BT2100_PQ(203 * linear).

    Video clips stay HLG, which is what iPhone video is; the assembler already
    converts between the two in `_get_hdr_to_hdr_filter`, so one file can carry
    both kinds of source.
    """
    hdr = _captured_filter(primaries="smpte432")

    assert "t=smpte2084" in hdr
    assert "arib-std-b67" not in hdr


def test_a_jpeg_named_heic_still_renders(tmp_path) -> None:
    """Extensions lie, and one bad file must not lose a photograph.

    A real library asset carries a `.heic` name over JPEG bytes -- Immich hands
    back the original, and `file` reports "JPEG image data, Exif ... model=iPhone
    11". Routing on the extension sent it to pillow-heif, which raised
    `No 'ftyp' box: File does not start with 'ftyp'`, and the render failed.

    A photograph that a decoder can read should not be dropped over its name.
    """
    from PIL import Image

    from immich_memories.photos.animator import prepare_photo_source

    liar = tmp_path / "IMG_9999.heic"
    Image.new("RGB", (64, 48), "orange").save(liar, "JPEG")

    work = tmp_path / "work"
    work.mkdir()
    prepared = prepare_photo_source(liar, work)

    assert prepared.width == 64
    assert prepared.height == 48
    assert prepared.has_gain_map is False
