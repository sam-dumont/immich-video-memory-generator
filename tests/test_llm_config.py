"""Tests for LLMConfig with flat provider fields."""

from unittest.mock import MagicMock, patch

import httpx

from immich_memories.config_models import LLMConfig


class TestLLMConfigDefaults:
    def test_default_base_url(self):
        config = LLMConfig()
        assert config.base_url == "http://localhost:8080/v1"


class TestLLMConfigNoMigration:
    """Old field names are silently ignored — no migration code."""

    def test_old_fields_ignored(self):
        """Deprecated field names don't set new fields (migration removed)."""
        config = LLMConfig.model_validate(
            {"ollama_url": "http://old:11434", "ollama_model": "llava"}
        )
        # Old values are ignored — defaults are used instead
        assert config.base_url == "http://localhost:8080/v1"
        assert config.model == ""

    def test_new_fields_work(self):
        """Current field names are accepted."""
        config = LLMConfig.model_validate(
            {
                "base_url": "http://new:8080/v1",
                "model": "qwen3.5",
                "provider": "openai-compatible",
            }
        )
        assert config.base_url == "http://new:8080/v1"
        assert config.model == "qwen3.5"


class TestOpenAICompatibleProvider:
    def test_import_new_name(self):
        """OpenAICompatibleContentAnalyzer should be importable."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        assert OpenAICompatibleContentAnalyzer is not None

    def test_accepts_empty_api_key(self):
        """Local servers (mlx-vlm) don't need an API key."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="qwen3.5-9b",
            base_url="http://localhost:8080/v1",
            api_key="",
        )
        assert analyzer.model == "qwen3.5-9b"
        assert analyzer.api_key == ""

    def test_timeout_default_is_300s(self):
        """Local vision models need longer timeouts (default 300s)."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="test", base_url="http://localhost:8080/v1", api_key=""
        )
        client = analyzer.client
        assert client.timeout.read == 300.0
        analyzer.close()

    def test_timeout_configurable(self):
        """Timeout can be configured via parameter."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="test", base_url="http://localhost:8080/v1", api_key="", timeout=600.0
        )
        client = analyzer.client
        assert client.timeout.read == 600.0
        analyzer.close()

    def test_no_auth_header_when_no_key(self):
        """Should NOT set Authorization header when api_key is empty."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="test", base_url="http://localhost:8080/v1", api_key=""
        )
        client = analyzer.client
        assert "authorization" not in {k.lower() for k in client.headers}
        analyzer.close()

    def test_auth_header_when_key_provided(self):
        """Should set Authorization header when api_key is non-empty."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="test", base_url="http://localhost:8080/v1", api_key="sk-test"
        )
        client = analyzer.client
        assert client.headers.get("authorization") == "Bearer sk-test"
        analyzer.close()


class TestGetContentAnalyzer:
    def test_ollama_provider_returns_ollama_analyzer(self):
        from immich_memories.analysis._content_providers import OllamaContentAnalyzer
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        analyzer = get_content_analyzer(
            provider="ollama",
            base_url="http://localhost:11434",
            model="llava",
        )
        assert isinstance(analyzer, OllamaContentAnalyzer)
        assert analyzer.model == "llava"
        assert analyzer.base_url == "http://localhost:11434"

    def test_openai_compatible_provider_returns_compat_analyzer(self):
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        analyzer = get_content_analyzer(
            provider="openai-compatible",
            base_url="http://localhost:8080/v1",
            model="qwen3.5-9b",
            api_key="",
        )
        assert isinstance(analyzer, OpenAICompatibleContentAnalyzer)
        assert analyzer.model == "qwen3.5-9b"

    def test_unknown_provider_returns_none(self):
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        analyzer = get_content_analyzer(
            provider="unknown",
            base_url="http://localhost:8080",
            model="test",
        )
        assert analyzer is None

    def test_no_auto_fallback(self):
        """Auto mode should not exist - provider must be explicit."""
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        # "auto" is not a valid provider, should return None
        analyzer = get_content_analyzer(
            provider="auto",
            base_url="http://localhost:11434",
            model="llava",
        )
        assert analyzer is None


class TestOpenAICompatibleMoodAnalyzer:
    def test_import_new_name(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        assert OpenAICompatibleMoodAnalyzer is not None

    def test_accepts_empty_api_key(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(
            model="qwen3.5-9b",
            base_url="http://localhost:8080/v1",
            api_key="",
        )
        assert analyzer.model == "qwen3.5-9b"
        assert analyzer.api_key == ""

    def test_backwards_compat_alias(self):
        from immich_memories.audio.mood_analyzer_backends import (
            OpenAICompatibleMoodAnalyzer,
        )

        assert OpenAICompatibleMoodAnalyzer is not None


class TestPreflightLLMCheck:
    @patch("immich_memories.preflight.httpx.Client")
    def test_openai_compatible_sends_test_completion(self, mock_client_cls):
        """Preflight for openai-compatible should send a minimal chat completion."""
        from immich_memories.config import Config
        from immich_memories.preflight import CheckStatus, check_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}}],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = Config()
        config.llm.provider = "openai-compatible"
        config.llm.base_url = "http://localhost:8080/v1"
        config.llm.model = "test-model"

        result = check_llm(config)
        assert result.status == CheckStatus.OK
        # Verify it sent a POST to chat/completions
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "chat/completions" in call_url

    @patch("immich_memories.preflight.httpx.Client")
    def test_ollama_checks_api_tags(self, mock_client_cls):
        """Preflight for ollama should check /api/tags."""
        from immich_memories.config import Config
        from immich_memories.preflight import CheckStatus, check_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llava:latest"}],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = Config()
        config.llm.provider = "ollama"
        config.llm.base_url = "http://localhost:11434"
        config.llm.model = "llava"

        result = check_llm(config)
        assert result.status == CheckStatus.OK
        # Verify it sent a GET to /api/tags
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        assert "/api/tags" in call_url

    @patch("immich_memories.preflight.httpx.Client")
    def test_openai_compatible_connection_error(self, mock_client_cls):
        """Should return WARNING when server is unreachable."""
        from immich_memories.config import Config
        from immich_memories.preflight import CheckStatus, check_llm

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        config = Config()
        config.llm.provider = "openai-compatible"
        config.llm.base_url = "http://localhost:8080/v1"
        config.llm.model = "test"

        result = check_llm(config)
        assert result.status == CheckStatus.WARNING
