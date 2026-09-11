"""What a watcher sees while a long stage works: pictures, a bar, and the lines behind them."""

from __future__ import annotations

import base64
import io

from PIL import Image

from immich_memories.ui.pages.cut_progress_view import (
    STRIP_THUMBNAIL_PX,
    PictureRing,
    remember_stage_line,
    strip_thumbnail,
)


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buffer, "JPEG")
    return buffer.getvalue()


class TestTheRingOfSlots:
    """Fixed slots, oldest overwritten first, so a run of any length draws the same DOM."""

    def test_arriving_pictures_take_successive_slots(self):
        ring = PictureRing(size=4)

        assert ring.admit(["a", "b"], limit=4) == [(0, "a"), (1, "b")]

    def test_a_picture_already_on_screen_is_not_drawn_again(self):
        ring = PictureRing(size=4)
        ring.admit(["a", "b"], limit=4)

        assert ring.admit(["a", "b", "c"], limit=4) == [(2, "c")]

    def test_only_the_newest_few_arrive_per_tick_so_a_batch_cannot_flood_the_socket(self):
        ring = PictureRing(size=8)

        assert ring.admit(["a", "b", "c", "d", "e"], limit=2) == [(0, "d"), (1, "e")]

    def test_the_slots_wrap_and_the_oldest_picture_is_the_one_replaced(self):
        ring = PictureRing(size=3)
        ring.admit(["a", "b", "c"], limit=3)

        assert ring.admit(["d"], limit=3) == [(0, "d")]
        assert ring.admit(["a"], limit=3) == [(1, "a")]


class TestTheStripThumbnail:
    """A library preview is a full-size JPEG; a strip cell is a postage stamp."""

    def test_a_preview_becomes_a_small_data_uri(self):
        uri = strip_thumbnail(_jpeg(1440, 1080))

        assert uri.startswith("data:image/jpeg;base64,")
        payload = base64.b64decode(uri.split(",", 1)[1])
        with Image.open(io.BytesIO(payload)) as image:
            assert max(image.size) <= STRIP_THUMBNAIL_PX
        assert len(payload) < 20_000

    def test_a_picture_that_is_not_cached_or_not_readable_is_simply_skipped(self):
        assert strip_thumbnail(None) is None
        assert strip_thumbnail(b"") is None
        assert strip_thumbnail(b"not a jpeg at all") is None


class TestTheDetailLines:
    """The engine's own stage strings, newest last, bounded."""

    def test_a_new_line_is_remembered_and_a_repeat_is_not(self):
        lines: list[str] = []

        assert remember_stage_line(lines, "Preparing previews: 50/900", limit=5) is True
        assert remember_stage_line(lines, "Preparing previews: 50/900", limit=5) is False
        assert lines == ["Preparing previews: 50/900"]

    def test_an_empty_line_is_never_remembered(self):
        lines: list[str] = []

        assert remember_stage_line(lines, "", limit=5) is False
        assert lines == []

    def test_the_oldest_lines_fall_off_so_a_long_cut_cannot_grow_the_page(self):
        lines: list[str] = []
        for index in range(20):
            remember_stage_line(lines, f"Preparing previews: {index}/900", limit=5)

        assert len(lines) == 5
        assert lines[-1] == "Preparing previews: 19/900"
        assert lines[0] == "Preparing previews: 15/900"
