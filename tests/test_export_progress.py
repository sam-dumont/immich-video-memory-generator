"""Export callbacks share one monotonic scale, including extraction and delivery."""

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_progress import _PipelineProgress


def test_extraction_and_upload_cannot_finish_or_rewind_the_export_bar(tmp_path):
    seen = []
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        upload_enabled=True,
        progress_callback=lambda _phase, pct, _msg: seen.append(pct),
    )
    progress = _PipelineProgress(params, clip_count=10)
    for phase, pct in [
        ("download", 0),
        ("extract", 0.5),
        ("extract", 1),
        ("extract", 0.2),
        ("unknown", 1),
        ("download", 1),
        ("assembly", 0),
        ("assembly", 1),
        ("music", 0),
        ("music", 1),
        ("upload", 0.95),
    ]:
        progress.report(phase, pct, phase)
    assert seen == sorted(seen)
    assert all(value < 1 for value in seen)
    progress.report("done", 1, "Complete")
    assert seen[-1] == 1
