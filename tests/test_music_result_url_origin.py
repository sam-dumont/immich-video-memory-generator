"""The music API's own job JSON decides which host receives its API key.

httpx honours an absolute URL over `base_url` and still sends client-level
headers, so a `result_urls` entry naming another host causes a request there
carrying `X-API-Key`. The configured host has to be malicious or compromised for
it to matter -- but it is a host the user chose, and the key should not leave it.
"""

from __future__ import annotations

import pytest

from immich_memories.audio.music_generator_client import (
    MusicGenClient,
    MusicGenClientConfig,
    result_url_within,
)

_BASE = "http://music.local:8000"


class TestResultUrlWithin:
    @pytest.mark.parametrize(
        "url",
        [
            "/files/track.wav",
            "files/track.wav",
            "http://music.local:8000/files/track.wav",
            "http://MUSIC.LOCAL:8000/files/track.wav",
        ],
    )
    def test_it_allows_relative_paths_and_the_configured_origin(self, url):
        assert result_url_within(url, _BASE) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://evil.example/collect",
            "https://music.local:8000/files/track.wav",
            "http://music.local:9999/files/track.wav",
            "http://music.local.evil.example/files/track.wav",
            "//evil.example/collect",
            "file:///etc/passwd",
        ],
    )
    def test_it_rejects_anything_that_leaves_that_origin(self, url):
        assert result_url_within(url, _BASE) is False

    def test_an_implicit_port_matches_the_explicit_one(self):
        assert result_url_within("http://music.local:80/f.wav", "http://music.local") is True
        assert result_url_within("https://m.local/f.wav", "https://m.local:443") is True


class TestDownloadRefusesForeignHosts:
    @pytest.mark.asyncio
    async def test_it_raises_before_any_request_is_made(self, tmp_path):
        config = MusicGenClientConfig(base_url=_BASE, api_key="secret")
        async with MusicGenClient(config) as client:
            with pytest.raises(ValueError, match="outside the configured"):
                await client._download_file("http://evil.example/collect", tmp_path / "track.wav")
