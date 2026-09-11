"""The CLI hands the pipeline a triage engine exactly when the config says so."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.config_loader import Config


def _run_until_pipeline_built(config: Config, tmp_path) -> MagicMock:
    """Drive run_pipeline_and_generate to the pipeline construction and stop."""
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.timeperiod import DateRange

    config.cache.database = str(tmp_path / "analysis.db")
    config.cache.directory = str(tmp_path / "cache")
    # The runtime refuses a blank model, and a bare Config() has one. Naming a
    # model here is what keeps this test off whatever the developer's own
    # ~/.immich-memories/config.yaml happens to say.
    config.llm.model = "wiring-test-model"
    clip = MagicMock()
    clip.asset.id = "asset-1"
    clip.width, clip.height = 1920, 1080

    # WHY: the run is cut short at pipeline construction, before Immich or the model.
    with (
        # WHY: Immich is the external boundary — this stands in for the library read.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: stands in for the selection engine so the test reads its constructor arguments.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
        pytest.raises(RuntimeError, match="stop here"),
    ):
        pipeline_type.return_value.run_editorial_source.side_effect = RuntimeError("stop here")
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60,
            transition="cut",
            music=None,
            output_path=tmp_path / "memory.mp4",
            memory_type="trip",
            person_names=[],
            date_range=DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2)),
            upload_to_immich=False,
            album=None,
            source="auto",
        )
    return pipeline_type


def test_the_cli_banks_head_facts_beside_the_other_caches(tmp_path) -> None:
    from immich_memories.triage.engine import TriageEngine

    config = Config()
    config.triage.enabled = True
    if not config.triage.encoder_path.is_file():
        pytest.skip("pinned DINOv2 export not on this machine")

    engine = _run_until_pipeline_built(config, tmp_path).call_args.kwargs["triage"]

    assert isinstance(engine, TriageEngine)
    run = engine.run(["asset-1"], lambda _asset_id: _jpeg())
    assert run.decided == 1
    assert (tmp_path / "cache" / "triage.db").is_file()


def _jpeg() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 200, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()
