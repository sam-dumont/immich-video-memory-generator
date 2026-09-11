"""Exact preview reuse must survive process restarts without trusting stale asset IDs."""

import sqlite3

import cv2
import numpy as np
import pytest

from immich_memories.analysis import editorial_thumbnail_hashes as module
from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash
from immich_memories.analysis.editorial_thumbnail_hashes import CachedThumbnailHasher


def preview(reverse=False):
    pixels = np.zeros((64, 64, 3), dtype=np.uint8)
    pixels[32 if reverse else 0 : 64 if reverse else 32] = 255
    ok, encoded = cv2.imencode(".jpg", pixels)
    assert ok
    return encoded.tobytes()


def test_restart_reuses_exact_bytes_without_decoding_and_changed_bytes_miss(tmp_path, monkeypatch):
    payloads = {"same-id": preview()}
    path = tmp_path / "hashes.sqlite"
    first = CachedThumbnailHasher(path, payloads.get)
    expected = compute_thumbnail_hash(payloads["same-id"])
    assert first("same-id") == expected
    first.close()
    decoded = []

    def count(payload, size):
        decoded.append(payload)
        return compute_thumbnail_hash(payload, size)

    monkeypatch.setattr(module, "compute_thumbnail_hash", count)
    second = CachedThumbnailHasher(path, payloads.get)
    try:
        assert second("same-id") == expected
        assert decoded == []
        payloads["same-id"] = preview(reverse=True)
        assert second("same-id") == compute_thumbnail_hash(payloads["same-id"]) != expected
        assert len(decoded) == 1
        assert second.metrics()["cache_hits"] == 1
        assert second.metrics()["hashes_computed"] == 1
    finally:
        second.close()


@pytest.mark.parametrize("changed", ["size", "method"])
def test_hash_implementation_and_size_are_part_of_identity(tmp_path, monkeypatch, changed):
    path = tmp_path / "hashes.sqlite"
    payload = preview()
    first = CachedThumbnailHasher(path, lambda _asset: payload)
    first("a")
    first.close()
    if changed == "method":
        monkeypatch.setattr(module, "METHOD", "next-producer-version")
    second = CachedThumbnailHasher(
        path, lambda _asset: payload, hash_size=16 if changed == "size" else 8
    )
    try:
        second("a")
        assert second.metrics()["hashes_computed"] == 1
        assert second.metrics()["cache_hits"] == 0
    finally:
        second.close()


def test_identical_previews_can_share_a_fact_across_asset_ids(tmp_path):
    hasher = CachedThumbnailHasher(tmp_path / "hashes.sqlite", lambda _asset: preview())
    try:
        assert hasher("a") == hasher("b")
        assert hasher.metrics()["hashes_computed"] == 1
        assert hasher.metrics()["cache_hits"] == 1
    finally:
        hasher.close()


def test_unavailable_or_unreadable_previews_are_never_banked_as_valid_hashes(tmp_path):
    payloads = {}
    path = tmp_path / "hashes.sqlite"
    hasher = CachedThumbnailHasher(path, payloads.get)
    try:
        assert hasher("a") is None
        payloads["a"] = b"not an image"
        assert hasher("a") is None
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM thumbnail_hashes").fetchone()[0] == 0
        payloads["a"] = preview()
        assert hasher("a") == compute_thumbnail_hash(payloads["a"])
        assert hasher.metrics()["cache_hits"] == 0
    finally:
        hasher.close()


def test_sqlite_corruption_stops_the_campaign_instead_of_silently_recomputing(tmp_path):
    path = tmp_path / "hashes.sqlite"
    path.write_bytes(b"corrupt database")
    hasher = CachedThumbnailHasher(path, lambda _asset: preview())
    try:
        with pytest.raises(sqlite3.DatabaseError):
            hasher("a")
    finally:
        hasher.close()
