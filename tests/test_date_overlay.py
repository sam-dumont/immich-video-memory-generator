"""The per-clip date overlay (#313).

`--add-date` and the Step 3 switch have always been plumbed as far as
``AssemblySettings.add_date_overlay`` and then read by nothing, while the CLI
run summary printed "Date Overlay: Enabled". These cover the wiring that makes
the option mean something.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.clip_caption import ClipCaption
from immich_memories.processing.streaming_frame_decoder import FrameDecoder


def _decoder(**kwargs) -> FrameDecoder:
    return FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, **kwargs)


class TestOverlayIsDrawn:
    def test_the_date_reaches_the_filter_chain(self):
        vf = _decoder(caption=ClipCaption(date="5 Jan 2026"))._build_vf()

        assert "drawtext=" in vf
        assert "5 JAN 2026" in vf  # captions render uppercase (v4 review)

    def test_no_overlay_without_text(self):
        assert "drawtext" not in _decoder()._build_vf()


class TestOverlayScalesWithTheFrame:
    """A fixed 24 px date is unreadable at 4K and huge on a phone-sized render."""

    def test_font_size_grows_with_the_frame(self):
        hd = FrameDecoder(
            Path("/f.mp4"), width=1920, height=1080, fps=30, caption=ClipCaption(date="x")
        )
        uhd = FrameDecoder(
            Path("/f.mp4"), width=3840, height=2160, fps=30, caption=ClipCaption(date="x")
        )

        assert _font_size(uhd._build_vf()) > _font_size(hd._build_vf())

    def test_text_stays_clear_of_the_frame_edge(self):
        vf = _decoder(caption=ClipCaption(date="x"))._build_vf()

        assert "x=w-tw-" in vf
        # constant y anchor — descenders must not shift the baseline (v4 review)
        assert ":y=h-" in vf and "y=h-th-" not in vf

    def test_text_is_readable_over_a_bright_background(self):
        """White-on-white is invisible; the shadow is what guarantees contrast."""
        vf = _decoder(caption=ClipCaption(date="x"))._build_vf()

        assert "shadowcolor=" in vf


def _font_size(vf: str) -> int:
    import re

    match = re.search(r"fontsize=(\d+)", vf)
    assert match, f"no fontsize in {vf}"
    return int(match.group(1))


class TestWhatGetsCaptioned:
    """The date comes off the clip; the option decides whether it is drawn."""

    def test_the_clips_date_is_captioned_when_the_option_is_on(self):
        from immich_memories.processing.streaming_frame_decoder import make_decoder

        decoder = make_decoder(
            _fake_clip(date="2026-01-05"), 0, 1920, 1080, 30, caption=ClipCaption(date="5 Jan 2026")
        )

        assert "5 JAN 2026" in decoder._build_vf()

    def test_nothing_is_drawn_when_the_option_is_off(self):
        from immich_memories.processing.streaming_frame_decoder import make_decoder

        decoder = make_decoder(_fake_clip(date="2026-01-05"), 0, 1920, 1080, 30)

        assert "drawtext" not in decoder._build_vf()

    def test_a_title_card_gets_no_date(self):
        """The title card is not a moment; stamping it with a date reads as a bug."""
        from immich_memories.processing.streaming_frame_decoder import make_decoder

        clip = _fake_clip(date="2026-01-05", is_title_screen=True)
        decoder = make_decoder(clip, 0, 1920, 1080, 30, caption=ClipCaption(date="5 Jan 2026"))

        assert "drawtext" not in decoder._build_vf()

    def test_a_clip_with_no_date_is_left_alone(self):
        from immich_memories.processing.streaming_frame_decoder import make_decoder

        decoder = make_decoder(_fake_clip(date=None), 0, 1920, 1080, 30, caption=ClipCaption())

        assert "drawtext" not in decoder._build_vf()


def _fake_clip(date, is_title_screen=False):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(
        path=Path("/does-not-exist.mp4"),
        duration=4.0,
        date=date,
        asset_id="a",
        is_title_screen=is_title_screen,
    )


class TestHdrCaptionBrightness:
    """Measured: white@0.85 lands at 872/1023 in a 10-bit pipeline.

    HLG puts graphics white at 75% of code range (~203 nits); anything above
    that glares next to the picture instead of sitting on it. SDR has no such
    headroom, so its caption stays white.
    """

    def test_hdr_output_draws_the_caption_below_graphics_white(self):
        hdr = FrameDecoder(
            Path("/f.mp4"),
            width=1920,
            height=1080,
            fps=30,
            pix_fmt="yuv420p10le",
            caption=ClipCaption(date="x"),
        )

        assert "fontcolor=white" not in hdr._build_vf()

    def test_sdr_output_keeps_a_white_caption(self):
        assert "fontcolor=white" in _decoder(caption=ClipCaption(date="x"))._build_vf()


class TestMalformedDates:
    def test_an_unparseable_date_captions_nothing_rather_than_raising(self):
        """Clip dates come from Immich metadata; a broken one must not stop a render."""
        from immich_memories.processing.clip_caption import captions_for_timeline

        (caption,) = captions_for_timeline([_fake_clip(date="not-a-date")])

        assert caption.date == ""
