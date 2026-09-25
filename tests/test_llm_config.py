"""Tests for LLMConfig with flat provider fields."""

from unittest.mock import MagicMock, patch

import httpx

from immich_memories.config_models_llm import LLMConfig


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


class TestPreflightLLMCheck:
    # WHY: httpx.Client makes real HTTP requests — mock to avoid hitting external LLM server
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

    # WHY: httpx.Client makes real HTTP requests — mock to avoid hitting Ollama server
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

    # WHY: httpx.Client makes real HTTP requests — simulate connection failure
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
