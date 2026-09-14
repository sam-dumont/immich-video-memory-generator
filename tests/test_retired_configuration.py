"""An old config can load without advertising retired settings again."""

from pathlib import Path

from immich_memories.config_loader import Config


def test_retired_settings_are_reported_and_omitted_when_saved(tmp_path: Path, caplog) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "scheduler:\n  enabled: true\n"
        "cache:\n  max_age_days: 30\n"
        "title_screens:\n  show_decorative_lines: true\n"
        "advanced:\n  triage:\n    enabled: true\n    bundle: old.npz\n    provider: cpu\n"
    )

    config = Config.from_yaml(path)
    config.save_yaml(path)

    saved = path.read_text()
    assert "scheduler:" not in saved
    assert "\n  max_age_days:" not in saved
    assert "triage.enabled" in caplog.text
    assert "triage.bundle" in caplog.text
    assert "cache.max_age_days" in caplog.text
    assert "scheduler" in caplog.text
    assert "title_screens.show_decorative_lines" in caplog.text
    assert "show_decorative_lines" not in config.title_screens.model_dump()
    assert "enabled" not in config.triage.model_dump()
    assert "bundle" not in config.triage.model_dump()
    assert config.triage.provider == "cpu"
