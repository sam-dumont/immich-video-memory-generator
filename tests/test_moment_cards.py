"""Moment cards are complete, deterministic views over canonical membership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import (
    project_episode_groups,
    project_moment_groups,
)
from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
from immich_memories.api.models import AssetType
from immich_memories.store.episode_readings import (
    EpisodeReadingProducer,
    EpisodeReadingStore,
)
from tests.conftest import make_asset


class _AnnotationLines:
    def __init__(self, lines: dict[str, str | AssetAnnotationLine]) -> None:
        self.lines = lines
        self.calls: list[tuple[str, ...]] = []

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch:
        self.calls.append(asset_ids)
        return AnnotationLineBatch(
            requested_asset_ids=asset_ids,
            lines=tuple(
                value
                if isinstance((value := self.lines[asset_id]), AssetAnnotationLine)
                else AssetAnnotationLine(asset_id, value)
                for asset_id in asset_ids
            ),
            missing_asset_ids=(),
            contract=AnnotationContract(
                "annotation-line-v1",
                ("description:student-v1", "heads:public-v1"),
            ),
        )


def test_scope_changes_selectable_members_without_changing_the_full_moment_card(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.moment_cards import build_moment_cards

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("id-wide-001", file_created_at=noon).model_copy(
                    update={"type": AssetType.IMAGE}
                ),
                make_asset(
                    "id-friend-002",
                    is_favorite=True,
                    file_created_at=noon + timedelta(minutes=2),
                ).model_copy(update={"type": AssetType.IMAGE}),
                make_asset("id-action-003", file_created_at=noon + timedelta(minutes=4)),
            )
        ),
    )
    annotations = _AnnotationLines(
        {
            "id-wide-001": "wide view of a road race | outdoor | crowd",
            "id-friend-002": "friend cheering beside the course | portrait | STARRED",
            "id-action-003": "the owner running through the race | sport-active | VIDEO 8s",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1", "heads:public-v1"),
    )
    prompts: list[str] = []

    def requester(prompt: str) -> str:
        prompts.append(prompt)
        return """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "The owner runs a road race while a friend cheers.",
            "representatives": [
              {"asset": 3, "reason": "Shows the owner actually running."}
            ],
            "cull": []
          }]
        }"""

    episode_result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=annotations,
        requester=requester,
    ).read(project_episode_groups(prepared, ("id-friend-002",)))

    friend_card = build_moment_cards(
        project_moment_groups(prepared, ("id-friend-002",)),
        episodes=episode_result,
    )[0]
    action_card = build_moment_cards(
        project_moment_groups(prepared, ("id-action-003",)),
        episodes=episode_result,
    )[0]

    assert annotations.calls == [prepared.candidate_ids]
    assert friend_card.text == action_card.text
    assert friend_card.full_asset_ids == ("id-wide-001", "id-friend-002", "id-action-003")
    assert friend_card.selectable_asset_ids == ("id-friend-002",)
    assert action_card.selectable_asset_ids == ("id-action-003",)
    assert "wide view of a road race | outdoor | crowd" in prompts[0]
    assert "wide view of a road race | outdoor | crowd" not in friend_card.text
    assert "friend cheering beside the course | portrait | STARRED" in friend_card.text
    assert "the owner running through the race | sport-active | VIDEO 8s" in friend_card.text
    assert friend_card.representative_asset_ids == ("id-action-003", "id-friend-002")
    assert "3 full members" not in friend_card.text
    assert "1 video" not in friend_card.text
    assert "1 starred" not in friend_card.text
    assert friend_card.text.splitlines()[0].startswith(
        'R\t"the owner running through the race | sport-active | VIDEO 8s"\t'
        '"Shows the owner actually running."'
    )
    assert friend_card.text.splitlines()[-1] == (
        'E\t"The owner runs a road race while a friend cheers."'
    )
    assert friend_card.evidence is not None
    assert friend_card.evidence.episode_meaning == (
        "The owner runs a road race while a friend cheers."
    )
    assert [row.description for row in friend_card.evidence.representatives] == [
        "the owner running through the race | sport-active | VIDEO 8s",
        "friend cheering beside the course | portrait | STARRED",
    ]
    assert [row.reason for row in friend_card.evidence.representatives] == [
        "Shows the owner actually running.",
        "rule: protected favourite",
    ]
    assert len(friend_card.text) <= 420
    assert "id-wide-001" not in friend_card.text
    assert "id-friend-002" not in friend_card.text
    assert "id-action-003" not in friend_card.text


def test_card_rolls_up_typed_heads_and_unique_stitching_bursts(tmp_path: Path) -> None:
    from immich_memories.analysis.moment_cards import build_moment_cards

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("private-action", file_created_at=noon),
                make_asset("private-finish", file_created_at=noon + timedelta(minutes=2)),
            )
        ),
    )
    annotations = _AnnotationLines(
        {
            "private-action": AssetAnnotationLine(
                "private-action",
                "12:00 | full evidence for the running frame | activity=sport-active",
                description="The owner runs toward the finish.",
                heads=(("people", "two"), ("activity", "sport-active"), ("location", "outdoor")),
                stitching_burst_id="private-burst-id",
            ),
            "private-finish": AssetAnnotationLine(
                "private-finish",
                "12:02 | full evidence for the finish frame | activity=sport-active",
                description="A friend waits at the finish line.",
                heads=(("people", "two"), ("activity", "sport-active"), ("location", "outdoor")),
                stitching_burst_id="private-burst-id",
            ),
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1", "heads:public-v1"),
    )
    episode_result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=annotations,
        requester=lambda _prompt: (
            """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "The owner runs while a friend waits at the finish.",
            "representatives": [{"asset": 1, "reason": "Shows the race action."}],
            "cull": []
          }]
        }"""
        ),
    ).read(project_episode_groups(prepared, prepared.candidate_ids))

    card = build_moment_cards(
        project_moment_groups(prepared, prepared.candidate_ids),
        episodes=episode_result,
    )[0]

    assert card.text.startswith('R\t"The owner runs toward the finish."\t"Shows the race action."')
    assert "A\tpeople=two\tactivity=sport-active\tlocation=outdoor\tstitch=1" in card.text
    assert card.evidence is not None
    assert card.evidence.annotations == (
        ("people", "two"),
        ("activity", "sport-active"),
        ("location", "outdoor"),
        ("stitch", "1"),
    )
    assert "private-burst-id" not in card.text
    assert len(card.text) <= 420
