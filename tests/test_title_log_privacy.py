"""The title banner must not copy a person's name or age into the logs."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
from immich_memories.titles.text_builder import SelectionType


def test_a_birthday_title_logs_neither_the_name_nor_the_age(tmp_path: Path, caplog) -> None:
    # WHY: RenderingService encodes video; the banner is logged before it runs
    with patch("immich_memories.titles.generator.RenderingService", return_value=MagicMock()):
        generator = TitleScreenGenerator(config=TitleScreenConfig(), output_dir=tmp_path)
        with caplog.at_level(logging.DEBUG, logger="immich_memories.titles.generator"):
            generator.generate_title_screen(
                year=2024,
                person_name="Zephyrine",
                birthday_age=37,
                selection_type=SelectionType.BIRTHDAY_YEAR,
            )

    # The output path is pytest's numbered temp dir, which can hold the digits of any age.
    logged = "\n".join(record.getMessage() for record in caplog.records).replace(
        str(tmp_path), "<out>"
    )
    assert "TITLE SCREEN GENERATION" in logged
    assert "Zephyrine" not in logged
    # The output path is logged too, and pytest numbers its temp dirs: pytest-3743 is not an age.
    assert "37" not in logged.replace(str(tmp_path), "")
