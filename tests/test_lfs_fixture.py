"""A checkout without `git lfs pull` skips the media tests instead of failing them."""

from __future__ import annotations

import pytest

from tests.conftest import lfs_fixture

POINTER = (
    b"version https://git-lfs.github.com/spec/v1\n"
    b"oid sha256:9ab0c04b2600000000000000000000000000000000000000000000000000000\n"
    b"size 123456\n"
)


def test_a_pointer_file_skips_with_the_fix_in_the_reason(tmp_path):
    pointer = tmp_path / "ultrahdr_colorful_daisies.jpg"
    pointer.write_bytes(POINTER)

    with pytest.raises(pytest.skip.Exception, match="Git LFS pointer.*git lfs pull"):
        lfs_fixture(pointer)


def test_a_missing_file_skips_with_the_fix_in_the_reason(tmp_path):
    with pytest.raises(pytest.skip.Exception, match="missing.*git lfs pull"):
        lfs_fixture(tmp_path / "gain_mapped-photo-tokyo.jpg")


def test_a_downloaded_file_is_handed_back(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe1 real jpeg bytes")

    assert lfs_fixture(photo) == photo
