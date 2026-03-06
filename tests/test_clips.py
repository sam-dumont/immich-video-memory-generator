"""Tests for clip processing module."""

from __future__ import annotations

import pytest

from immich_memories.processing.clips import _validate_header, _validate_url


class TestUrlValidation:
    """Tests for URL validation security."""

    def test_valid_https_url(self):
        """Test valid HTTPS URL passes validation."""
        url = "https://example.com/api/video/123"
        assert _validate_url(url) == url

    def test_valid_http_url(self):
        """Test valid HTTP URL passes validation."""
        url = "http://localhost:8080/video"
        assert _validate_url(url) == url

    def test_null_byte_rejected(self):
        """Test URL with null byte is rejected."""
        url = "https://example.com/video\x00.mp4"
        with pytest.raises(ValueError, match="null bytes"):
            _validate_url(url)

    def test_invalid_scheme_rejected(self):
        """Test non-http/https schemes are rejected."""
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            _validate_url("file:///etc/passwd")

        with pytest.raises(ValueError, match="Invalid URL scheme"):
            _validate_url("ftp://example.com/video")

    def test_missing_hostname_rejected(self):
        """Test URL without hostname is rejected."""
        with pytest.raises(ValueError, match="missing hostname"):
            _validate_url("https:///path/to/video")

    def test_shell_metacharacters_rejected(self):
        """Test URLs with shell metacharacters are rejected."""
        dangerous_urls = [
            "https://example.com/video;rm -rf /",
            "https://example.com/video|cat /etc/passwd",
            "https://example.com/video&whoami",
            "https://example.com/video$(whoami)",
            "https://example.com/video`whoami`",
            "https://example.com/video\nX-Inject: header",
            "https://example.com/video\rX-Inject: header",
        ]
        for url in dangerous_urls:
            with pytest.raises(ValueError, match="suspicious character"):
                _validate_url(url)


class TestHeaderValidation:
    """Tests for HTTP header validation security."""

    def test_valid_header(self):
        """Test valid header passes validation."""
        key, value = _validate_header("x-api-key", "secret123")
        assert key == "x-api-key"
        assert value == "secret123"

    def test_valid_header_with_dashes(self):
        """Test header key with dashes is valid."""
        key, value = _validate_header("Content-Type", "application/json")
        assert key == "Content-Type"

    def test_null_byte_in_key_rejected(self):
        """Test header key with null byte is rejected."""
        with pytest.raises(ValueError, match="null bytes"):
            _validate_header("api\x00key", "value")

    def test_null_byte_in_value_rejected(self):
        """Test header value with null byte is rejected."""
        with pytest.raises(ValueError, match="null bytes"):
            _validate_header("api-key", "value\x00inject")

    def test_newline_in_value_rejected(self):
        """Test header value with newline is rejected (header injection)."""
        with pytest.raises(ValueError, match="newline"):
            _validate_header("api-key", "value\nX-Inject: evil")

        with pytest.raises(ValueError, match="newline"):
            _validate_header("api-key", "value\rX-Inject: evil")

    def test_invalid_key_characters_rejected(self):
        """Test header key with invalid characters is rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_header("api:key", "value")

        with pytest.raises(ValueError, match="invalid characters"):
            _validate_header("api key", "value")

        with pytest.raises(ValueError, match="invalid characters"):
            _validate_header("api/key", "value")

    def test_long_value_rejected(self):
        """Test excessively long header value is rejected."""
        long_value = "x" * 5000  # Exceeds 4096 limit
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _validate_header("api-key", long_value)

    def test_max_length_value_accepted(self):
        """Test header value at max length is accepted."""
        max_value = "x" * 4096
        key, value = _validate_header("api-key", max_value)
        assert len(value) == 4096
