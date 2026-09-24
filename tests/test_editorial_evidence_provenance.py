"""An attempt keeps hashes of the exact evidence each episode was read from."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from immich_memories.analysis.editorial_evidence_provenance import (
    EVIDENCE_HASHES_NAME,
    EVIDENCE_LINES_NAME,
    AttemptEvidenceProvenance,
    EpisodeEvidenceLines,
)


def _episode(group_id: str, lines: dict[str, str]) -> EpisodeEvidenceLines:
    return EpisodeEvidenceLines(
        group_id=group_id,
        evidence_key=f"{group_id}-key",
        lines=tuple(lines.items()),
    )


def test_public_hashes_name_every_asset_without_carrying_any_line_text(tmp_path: Path) -> None:
    lines = {"a1": "a1 | birthday cake | with family", "b2": "b2 | beside the cake"}
    AttemptEvidenceProvenance().capture((_episode("g1", lines),), directory=tmp_path)

    published = json.loads((tmp_path / EVIDENCE_HASHES_NAME).read_text())
    assert published["episodes"] == [
        {
            "group_id": "g1",
            "evidence_key": "g1-key",
            "assets": [
                {"asset_id": asset_id, "line_sha256": hashlib.sha256(line.encode()).hexdigest()}
                for asset_id, line in lines.items()
            ],
        }
    ]
    text = (tmp_path / EVIDENCE_HASHES_NAME).read_text()
    assert "birthday cake" not in text


def test_the_private_sibling_keeps_the_rendered_lines_owner_only(tmp_path: Path) -> None:
    lines = {"a1": "a1 | birthday cake | with family"}
    AttemptEvidenceProvenance().capture((_episode("g1", lines),), directory=tmp_path)

    kept = tmp_path / EVIDENCE_LINES_NAME
    assert json.loads(kept.read_text())["episodes"][0]["lines"] == [["a1", lines["a1"]]]
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600


def test_the_reader_records_the_evidence_behind_every_keyed_episode(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        SourceScope,
        prepare_editorial_source,
    )
    from immich_memories.analysis.selection_source_groups import project_episode_groups
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
    from immich_memories.store.episode_readings import (
        EpisodeReadingIdentity,
        EpisodeReadingProducer,
        EpisodeReadingStore,
    )
    from tests.conftest import make_asset
    from tests.test_text_episode_reader import _AnnotationLines

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("cake", file_created_at=noon),
                make_asset("friend", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("friend",))
    rendered = {"cake": "cake | birthday cake", "friend": "friend | beside the cake"}
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    recorded: list[tuple[EpisodeEvidenceLines, ...]] = []
    CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=_AnnotationLines(rendered),
        requester=lambda _prompt: '{"schema_version":"episode-reading-text-v1","episodes":[]}',
        record_evidence=lambda episodes: recorded.append(tuple(episodes)),
    ).read(projections)

    group = projections[0].group
    assert [episode.group_id for episode in recorded[0]] == [group.group_id]
    assert recorded[0][0].evidence_key == (
        EpisodeReadingIdentity.from_annotations(
            group_id=group.group_id,
            producer_key=producer.key(),
            annotation_lines=rendered,
        ).evidence_key
    )
    assert recorded[0][0].lines == tuple(
        (asset_id, rendered[asset_id]) for asset_id in group.candidate_ids
    )


def test_the_runtime_records_evidence_into_the_attempt_that_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    import immich_memories.analysis.editorial_runtime as runtime
    from immich_memories.analysis.editorial_planner import EditorialPlan
    from immich_memories.analysis.selection_trace import Trace
    from immich_memories.config_loader import Config
    from tests.test_editorial_runtime import _window

    context = runtime.EditorialRunContext(
        "evidence-control",
        "A period",
        "monthly_highlights",
        (_window(2024, 7, 12),),
        30,
        tmp_path / "artifacts",
    )
    planner = runtime.build_editorial_planner(
        client=object(),
        thumbnail_cache=object(),
        context=context,
        # The whole-film planner's up-front reader; the polish route reads on demand.
        config=Config(
            llm={"model": "test-model"},
            cache={"directory": str(tmp_path / "cache")},
            editorial={"thin_model_layer": False},
        ),
        ports=runtime.EditorialRuntimePorts(load_people=lambda: {}),
    )
    built: dict[str, object] = {}

    def reader(**kwargs):
        built.update(kwargs)
        return object()

    def plan_source(*_args, **_kwargs):
        planner._planner._episode_reader_factory(SimpleNamespace(candidates=()))
        built["record_evidence"]((_episode("g1", {"a1": "a1 | a rendered line"}),))
        return SimpleNamespace(plan=EditorialPlan(), duration_realization=None)

    # WHY: the reader itself needs a prepared annotation store and a text model; the
    # boundary under test is only which directory its recorder writes to.
    monkeypatch.setattr(runtime, "CachedTextEpisodeReader", reader)
    monkeypatch.setattr(planner, "_plan_source", plan_source)
    planner.plan_source([], trace=Trace())

    attempt = planner.last_attempt_directory
    assert json.loads((attempt / EVIDENCE_HASHES_NAME).read_text())["episodes"][0]["group_id"] == (
        "g1"
    )
    assert (attempt / EVIDENCE_LINES_NAME).exists()
    assert not (context.artifact_dir / EVIDENCE_HASHES_NAME).exists()
    planner.close()
