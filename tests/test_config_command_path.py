"""`--config PATH` must be where `config` writes, not just where it reads.

The command loaded the config the context resolved -- honouring `--config` --
and then saved to `Config.get_default_path()` regardless. So
`immich-memories --config ./test.yaml config` edited the test file's values and
wrote them over `~/.immich-memories/config.yaml`.

Anyone keeping more than one config has their real one silently replaced by
whichever they thought they were editing. It cost me Sam's config while testing
an unrelated fix; the backup is what saved it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cli import main


def test_the_named_config_is_the_one_written(tmp_path: Path):
    target = tmp_path / "alternate.yaml"
    target.write_text("immich:\n  url: http://named.invalid:2283\n  api_key: named-key\n")
    default = tmp_path / "home" / ".immich-memories" / "config.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("immich:\n  url: http://default.invalid:2283\n  api_key: default-key\n")

    # WHY: the real home and the real Immich server
    with (
        # WHY: the default location must be observable without touching a real home.
        patch.object(Path, "home", classmethod(lambda _cls: tmp_path / "home")),
        # WHY: the command offers to contact Immich after saving.
        patch("immich_memories.api.immich.SyncImmichClient"),
    ):
        CliRunner().invoke(
            main,
            ["--config", str(target), "config", "--url", "http://edited.invalid:2283"],
            input="n\n",
        )

    assert "edited.invalid" in target.read_text(), "the named config was not updated"
    assert "default-key" in default.read_text(), "the default config was overwritten"
    assert "edited.invalid" not in default.read_text()
