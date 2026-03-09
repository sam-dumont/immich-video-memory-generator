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


class TestLLMConfigMigration:
    def test_old_ollama_fields_migrate(self):
        """Old ollama_url/ollama_model fields should map to new flat fields."""
        config = LLMConfig.model_validate(
            {
                "ollama_url": "http://myserver:11434",
                "ollama_model": "moondream",
                "provider": "ollama",
            }
        )
        assert config.base_url == "http://myserver:11434"
        assert config.model == "moondream"
        assert config.provider == "ollama"

    def test_old_openai_fields_migrate(self):
        """Old openai_* fields should map to new flat fields."""
        config = LLMConfig.model_validate(
            {
                "openai_api_key": "sk-test",
                "openai_model": "gpt-4o-mini",
                "openai_base_url": "https://api.openai.com/v1",
                "provider": "openai",
            }
        )
        assert config.api_key == "sk-test"
        assert config.model == "gpt-4o-mini"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.provider == "openai-compatible"

    def test_old_auto_provider_migrates_to_ollama(self):
        """Old 'auto' provider should migrate to 'ollama'."""
        config = LLMConfig.model_validate(
            {
                "provider": "auto",
                "ollama_url": "http://localhost:11434",
                "ollama_model": "llava",
            }
        )
        assert config.provider == "ollama"
        assert config.base_url == "http://localhost:11434"

    def test_new_fields_take_priority(self):
        """If both old and new fields present, new fields win."""
        config = LLMConfig.model_validate(
            {
                "base_url": "http://new:8080/v1",
                "model": "qwen3.5",
                "ollama_url": "http://old:11434",
                "ollama_model": "llava",
            }
        )
        assert config.base_url == "http://new:8080/v1"
        assert config.model == "qwen3.5"
