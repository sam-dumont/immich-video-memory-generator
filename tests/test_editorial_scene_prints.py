"""A frame's scene print is read once from its preview and banked, like its hash."""

import io

import numpy as np
from PIL import Image

from immich_memories.analysis.editorial_scene_prints import CachedScenePrints


def _jpeg(colour) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


PREVIEWS = {"red": _jpeg((200, 0, 0)), "blue": _jpeg((0, 0, 200))}


class _Encoder:
    """Stands in for the pinned DINOv2 export: a pack from each image's mean colour."""

    key = "test-encoder"

    def __init__(self) -> None:
        self.images = 0

    def embed(self, batch: np.ndarray) -> np.ndarray:
        self.images += len(batch)
        return batch.mean(axis=(2, 3))


def _prints(tmp_path, encoder):
    opened = []

    def open_encoder():
        opened.append(True)
        return encoder

    prints = CachedScenePrints(
        tmp_path / "scene-prints.sqlite", PREVIEWS.get, open_encoder=open_encoder
    )
    return prints, opened


def test_a_preview_is_encoded_once_and_read_from_the_bank_after(tmp_path):
    encoder = _Encoder()
    prints, _ = _prints(tmp_path, encoder)
    first = prints("red")
    prints.close()

    again, opened = _prints(tmp_path, encoder)
    second = again("red")

    assert encoder.images == 1
    assert opened == []
    np.testing.assert_allclose(first, second, atol=1e-3)


def test_different_scenes_give_different_prints(tmp_path):
    prints, _ = _prints(tmp_path, _Encoder())

    red, blue = prints("red"), prints("blue")

    assert not np.allclose(red, blue)


def test_a_frame_with_no_preview_has_no_print(tmp_path):
    prints, opened = _prints(tmp_path, _Encoder())

    assert prints("missing") is None
    assert opened == []


def test_an_install_without_the_encoder_reads_no_scene(tmp_path):
    prints = CachedScenePrints(
        tmp_path / "scene-prints.sqlite", PREVIEWS.get, open_encoder=lambda: None
    )

    assert prints("red") is None
