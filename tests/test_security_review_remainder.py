"""The security findings left open from docs/reviews/2026-08-18-security-perf-review.md.

S3 (rate limiter behind a proxy), S5 (stored XSS in the config viewer),
S10 (unbounded music upload), S16 (secrets partly shown).
"""

from __future__ import annotations

from immich_memories.config_models_auth import AuthConfig


class TestS5ConfigViewerEscapesValues:
    """immich.url is editable from the browser and persisted; rendering it into
    ui.html with an f-string makes it stored XSS for the next admin visit."""

    def test_a_script_in_a_value_is_escaped(self):
        from immich_memories.ui.pages.settings_config import build_config_table_html

        html = build_config_table_html({"immich": {"url": "<script>alert(1)</script>"}})

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_script_in_a_key_is_escaped(self):
        from immich_memories.ui.pages.settings_config import build_config_table_html

        html = build_config_table_html({"<img src=x onerror=alert(1)>": "v"})

        assert "<img" not in html

    def test_ordinary_values_still_render(self):
        from immich_memories.ui.pages.settings_config import build_config_table_html

        html = build_config_table_html({"immich": {"url": "http://immich:2283"}})

        assert "http://immich:2283" in html


class TestS16SecretsAreFullyMasked:
    """abc***yz leaks five characters of every secret, auth.password included."""

    def test_no_characters_of_the_secret_survive(self):
        from immich_memories.ui.pages.settings_config import redact_config

        redacted = redact_config({"immich": {"api_key": "abcdefghijklmnop"}})

        assert redacted["immich"]["api_key"] == "***"
        assert "abc" not in redacted["immich"]["api_key"]

    def test_an_empty_secret_stays_empty(self):
        from immich_memories.ui.pages.settings_config import redact_config

        assert redact_config({"immich": {"api_key": ""}})["immich"]["api_key"] == ""


class TestS3RateLimiterSeesTheRealClient:
    """Behind Traefik/nginx every request carries the proxy's IP, so one bad
    actor locks out everyone. Trust X-Forwarded-For only from a trusted peer."""

    def test_the_forwarded_client_is_used_when_the_peer_is_trusted(self):
        from immich_memories.ui.auth import client_ip_for_rate_limit

        ip = client_ip_for_rate_limit(
            peer_ip="10.0.0.5",
            forwarded_for="203.0.113.9, 10.0.0.5",
            auth_config=AuthConfig(trusted_proxies=["10.0.0.0/8"]),
        )

        assert ip == "203.0.113.9"

    def test_an_untrusted_peer_cannot_spoof_its_bucket(self):
        from immich_memories.ui.auth import client_ip_for_rate_limit

        ip = client_ip_for_rate_limit(
            peer_ip="198.51.100.7",
            forwarded_for="1.2.3.4",
            auth_config=AuthConfig(trusted_proxies=["10.0.0.0/8"]),
        )

        assert ip == "198.51.100.7"

    def test_no_header_means_the_peer(self):
        from immich_memories.ui.auth import client_ip_for_rate_limit

        ip = client_ip_for_rate_limit(
            peer_ip="10.0.0.5",
            forwarded_for=None,
            auth_config=AuthConfig(trusted_proxies=["10.0.0.0/8"]),
        )

        assert ip == "10.0.0.5"


class TestS10MusicUploadIsBounded:
    def test_the_upload_declares_a_size_cap(self):
        import inspect

        from immich_memories.ui.pages import step3_options

        source = inspect.getsource(step3_options)

        assert "max_file_size" in source

    def test_a_non_audio_payload_is_rejected(self):
        from immich_memories.ui.pages.step3_options import is_supported_audio

        assert is_supported_audio("track.mp3", b"ID3\x04\x00\x00\x00")
        assert not is_supported_audio("evil.mp3", b"MZ\x90\x00\x03\x00\x00\x00")
        assert not is_supported_audio("evil.exe", b"ID3\x04\x00\x00\x00")
