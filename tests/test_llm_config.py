"""Tests for LLMConfig with flat provider fields."""

from immich_memories.config_models import LLMConfig


class TestLLMConfigDefaults:
    def test_default_provider_is_openai_compatible(self):
        config = LLMConfig()
        assert config.provider == "openai-compatible"

    def test_default_base_url(self):
        config = LLMConfig()
        assert config.base_url == "http://localhost:8080/v1"

    def test_default_model_is_empty(self):
        config = LLMConfig()
        assert config.model == ""

    def test_default_api_key_is_empty(self):
        config = LLMConfig()
        assert config.api_key == ""
