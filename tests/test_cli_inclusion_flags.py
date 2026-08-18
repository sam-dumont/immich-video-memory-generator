"""The CLI flags must be able to turn a feature OFF, not only ON.

`use_photos = include_photos or config.photos.enabled` means a config with
photos enabled makes the feature unconditional: there is no way to run a
video-only memory from the command line.
"""

from __future__ import annotations

from immich_memories.cli.generate import resolve_inclusion


class TestResolveInclusion:
    def test_flag_absent_falls_back_to_config(self):
        assert resolve_inclusion(None, config_enabled=True) is True
        assert resolve_inclusion(None, config_enabled=False) is False

    def test_flag_can_enable_against_config(self):
        assert resolve_inclusion(True, config_enabled=False) is True

    def test_flag_can_disable_against_config(self):
        """The case that was impossible before."""
        assert resolve_inclusion(False, config_enabled=True) is False
