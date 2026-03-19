"""Tests for trusted proxy IP matching."""

from __future__ import annotations

from immich_memories.ui.auth import is_trusted_proxy


class TestIsTrustedProxy:
    """IP address matching against trusted proxy list."""

    def test_exact_ip_match(self):
        assert is_trusted_proxy("10.0.0.1", ["10.0.0.1"]) is True

    def test_exact_ip_no_match(self):
        assert is_trusted_proxy("10.0.0.2", ["10.0.0.1"]) is False

    def test_cidr_match(self):
        assert is_trusted_proxy("10.0.0.5", ["10.0.0.0/24"]) is True

    def test_cidr_no_match(self):
        assert is_trusted_proxy("10.0.1.5", ["10.0.0.0/24"]) is False

    def test_multiple_ranges(self):
        proxies = ["192.168.1.0/24", "10.0.0.0/8"]
        assert is_trusted_proxy("10.5.5.5", proxies) is True
        assert is_trusted_proxy("192.168.1.50", proxies) is True
        assert is_trusted_proxy("172.16.0.1", proxies) is False

    def test_invalid_client_ip(self):
        assert is_trusted_proxy("not-an-ip", ["10.0.0.0/24"]) is False

    def test_empty_proxy_list(self):
        assert is_trusted_proxy("10.0.0.1", []) is False

    def test_ipv6_exact(self):
        assert is_trusted_proxy("::1", ["::1"]) is True

    def test_ipv6_no_match(self):
        assert is_trusted_proxy("::2", ["::1"]) is False

    def test_ipv6_cidr(self):
        assert is_trusted_proxy("fd00::1", ["fd00::/16"]) is True
        assert is_trusted_proxy("fe80::1", ["fd00::/16"]) is False

    def test_invalid_proxy_entry_skipped(self):
        """Invalid CIDR in trusted_proxies is skipped, doesn't crash."""
        assert is_trusted_proxy("10.0.0.1", ["not-valid", "10.0.0.1"]) is True

    def test_all_invalid_proxies(self):
        assert is_trusted_proxy("10.0.0.1", ["garbage", "also-garbage"]) is False
