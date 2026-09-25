"""The terminal reads a finished cut: `runs story`, `runs why`, and the run index behind them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    PassTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.cli import main
from immich_memories.cli._run_summary import render_run_summary
from immich_memories.config_loader import Config
from immich_memories.operations.run_index import (
    attempt_dir_for_run,
    record_run_attempt,
    run_id_for_attempt,
)
from immich_memories.operations.storyboard import (
    PLAN_FILE,
    PROJECTION_FILE,
    TRACE_FILE,
    read_storyboard,
)
from immich_memories.tracking import RunDatabase
from immich_memories.tracking.models import RunMetadata

RUN_ID = "20260913_080000_ab12"

_PLAN = {
    "story": {
        "thesis": "A month that ends by the lake.",
        "episodes": [
            {"episode": "garden", "title": "Lunch in the garden"},
            {"episode": "lake", "title": "Two nights by the lake"},
        ],
    },
    "carriers": [
        {
            "asset_id": "lake-1",
            "taken": "2024-06-21T18:45:00",
            "story_episode": "lake",
            "kind": "video",
            "seconds": 4.0,
            "why": "Two nights by the lake: the tents on the slope",
            "depicted_moment": "m-lake",
        },
        {
            "asset_id": "garden-1",
            "taken": "2024-06-08T12:15:00",
            "story_episode": "garden",
            "kind": "photo",
            "seconds": 4.0,
            "why": "Lunch in the garden: the table still out",
            "depicted_moment": "m-garden",
        },
        {
            "asset_id": "garden-2",
            "taken": "2024-06-09T16:30:00",
            "story_episode": "garden",
            "kind": "photo",
            "seconds": 4.0,
            "why": "Lunch in the garden: the cake",
            "depicted_moment": "m-garden",
        },
    ],
}


class _Candidate:
    """The least a trace needs of a pool item: an id to name it by."""

    def __init__(self, asset_id: str) -> None:
        self.id = asset_id


def _provenance(name: str) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name=name,
        pass_version="1",  # noqa: S106 - a version label, not a secret
        schema_version="1",
        model_identity="rules",
        input_ids=(),
        sheet_hashes=(),
        request_key="",
        cache_hit=False,
    )


def _trace() -> Trace:
    trace = Trace()
    trace.clips = {"garden-1": "photo, 2024-06-08", "woods-9": "photo, 2024-06-15"}
    trace.editorial_passes.append(
        PassTrace(
            name="picture-review",
            input_ids=("garden-1", "garden-2", "lake-1", "woods-9"),
            kept_ids=("garden-1", "garden-2", "lake-1"),
            rejected=(TraceDecision("woods-9", "a near duplicate of the path shot"),),
            unresolved=(),
            duration_before=16.0,
            duration_after=12.0,
            provenance=_provenance("picture-review"),
        )
    )
    return trace


@pytest.fixture
def cut(tmp_path: Path) -> tuple[Config, Path]:
    """A config whose cache holds one completed run with its attempt directory and index."""
    cache = tmp_path / "cache"
    attempt = cache / "editorial-runs" / "june" / "attempts" / "a1"
    attempt.mkdir(parents=True)
    (attempt / PLAN_FILE).write_text(json.dumps(_PLAN))
    (attempt / PROJECTION_FILE).write_text(
        json.dumps({"intervals": {"garden-1": [0.0, 3.5], "garden-2": [3.5, 7.5]}})
    )
    (attempt / TRACE_FILE).write_text(json.dumps(_trace().as_dict()))
    config = Config()
    config.cache.directory = str(cache)
    config.cache.database = str(cache / "runs.db")
    db = RunDatabase(db_path=config.cache.database_path)
    db.save_run(
        RunMetadata(
            run_id=RUN_ID,
            created_at=datetime(2026, 9, 13, 8, 0, tzinfo=UTC),
            completed_at=datetime(2026, 9, 13, 8, 1, tzinfo=UTC),
            status="completed",
            output_path=str(tmp_path / "june.mp4"),
        )
    )
    record_run_attempt(config.cache.cache_path, RUN_ID, attempt, tmp_path / "june.mp4")
    return config, attempt


def _invoke(config: Config, args: list[str]):
    # WHY: `runs` reads the real config file and cache path from $HOME otherwise.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.config.get_config", return_value=config),
    ):
        return CliRunner().invoke(main, args, catch_exceptions=False)


class TestRunIndex:
    def test_a_finished_run_is_found_from_its_id_and_the_attempt_knows_its_run(self, cut):
        config, attempt = cut
        assert attempt_dir_for_run(config.cache.cache_path, RUN_ID) == attempt
        assert run_id_for_attempt(attempt) == RUN_ID

    def test_an_unknown_run_or_a_run_without_an_attempt_resolves_to_nothing(self, cut, tmp_path):
        config, _ = cut
        assert attempt_dir_for_run(config.cache.cache_path, "nope") is None
        record_run_attempt(config.cache.cache_path, "gone", None, tmp_path / "x.mp4")
        assert attempt_dir_for_run(config.cache.cache_path, "gone") is None


class TestRunsStory:
    def test_the_storyboard_prints_in_capture_order_with_the_projected_lengths(self, cut):
        config, _ = cut
        result = _invoke(config, ["runs", "story", RUN_ID])
        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.lstrip().startswith("0:")]
        assert [line.split()[1] for line in lines] == ["2024-06-08", "2024-06-09", "2024-06-21"]
        assert lines[0].lstrip().startswith("0:00")
        assert "3.5 s" in lines[0]  # the renderer's interval, not the plan's seconds
        assert "video" in lines[2] and "Two nights by the lake" in lines[2]
        assert "3 pictures, 0:11 of pictures and video" in result.output

    def test_no_argument_reads_the_latest_completed_run(self, cut):
        config, _ = cut
        result = _invoke(config, ["runs", "story"])
        assert result.exit_code == 0, result.output
        assert f"Run {RUN_ID}" in result.output

    def test_an_unknown_run_says_so_and_fails(self, cut):
        config, _ = cut
        result = _invoke(config, ["runs", "story", "20250101_000000_zzzz"])
        assert result.exit_code == 1
        assert "Run not found" in result.output


class TestRunsWhy:
    def _snapshot(self, attempt, provenance):
        (attempt / "preparation.private.json").write_text(
            json.dumps({"caption_provenance": provenance})
        )

    def test_caption_origin_comes_from_the_run_snapshot(self, cut):
        config, attempt = cut
        self._snapshot(
            attempt,
            {
                "origins": [
                    {
                        "model_id": "public-captioner",
                        "endpoint": "http://original.invalid/v1",
                        "artifact_id": "gguf-q8@one",
                        "served": {"owned_by": "llamacpp"},
                        "control_digest": "6b1d0a0c11b612f4",
                        "assets": 1,
                    }
                ],
                "by_asset": {},
            },
        )
        config.editorial.preparation.caption_base_url = "http://replacement.invalid/v1"
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "public-captioner" in result.output
        assert "gguf-q8@one" in result.output
        assert "owned_by=llamacpp" in result.output
        assert "6b1d0a0c11b612f4" in result.output
        assert "original.invalid" in result.output
        assert "replacement.invalid" not in result.output

    def test_a_caption_banked_before_origins_were_recorded_reads_as_unknown(self, cut):
        """The answer to "what happens to the bank I already have"."""
        config, attempt = cut
        self._snapshot(attempt, {"origins": [{"status": "unknown", "assets": 1}], "by_asset": {}})
        config.editorial.preparation.caption_base_url = "http://replacement.invalid/v1"
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "Caption origin: unknown (not recorded with this caption)" in result.output
        assert "replacement.invalid" not in result.output

    def test_a_picture_with_no_caption_at_all_gets_no_origin_sentence(self, cut):
        config, attempt = cut
        self._snapshot(
            attempt,
            {
                "origins": [
                    {
                        "model_id": "public-captioner",
                        "endpoint": "http://one.invalid/v1",
                        "assets": 1,
                    },
                    {"status": "none", "assets": 1},
                ],
                "by_asset": {"garden-2": 1},
            },
        )
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "Caption origin" not in result.output

    def test_a_run_that_predates_caption_origins_says_so(self, cut):
        config, attempt = cut
        (attempt / "preparation.private.json").write_text(json.dumps({"tier": "full"}))
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "Caption origin: unknown (not recorded for this run)" in result.output

    def test_the_music_answer_names_its_text_source(self, cut):
        config, attempt = cut
        (attempt / "music-mood.private.json").write_text(
            json.dumps(
                {
                    "source": "cut_text",
                    "mood": {"primary_mood": "playful"},
                }
            )
        )
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "Music mood: playful (saved cut text; no pictures sent)" in result.output

    def test_a_dropped_picture_names_the_pass_and_the_reason(self, cut):
        config, _ = cut
        result = _invoke(config, ["runs", "why", "woods-9", "--run", RUN_ID])
        assert result.exit_code == 0, result.output
        assert "left out at the picture review: a near duplicate of the path shot" in result.output

    def test_a_kept_picture_says_where_it_plays(self, cut):
        config, _ = cut
        result = _invoke(config, ["runs", "why", "garden-2"])
        assert result.exit_code == 0, result.output
        assert "in the cut at 0:03, 2024-06-09, story: Lunch in the garden" in result.output

    def test_the_owner_s_word_on_the_picture_is_said_last(self, cut):
        from immich_memories.operations import picture_holds

        config, _ = cut
        picture_holds.never_use(config, "garden-2", via="cli")

        result = _invoke(config, ["runs", "why", "garden-2"])

        assert result.exit_code == 0, result.output
        assert "Your word on it now: You'll never use this picture." in result.output


class TestTraceRoundTrip:
    def test_a_trace_read_back_from_its_file_tells_the_same_story(self):
        original = _trace()
        restored = Trace.from_dict(json.loads(json.dumps(original.as_dict())))
        assert restored.story_of("woods-9") == original.story_of("woods-9")
        assert restored.story_of("garden-1") == original.story_of("garden-1")

    def test_a_saved_run_still_knows_which_pictures_its_final_cut_dropped(self):
        """A real run drops most of the pool at the planner, not at a pass.

        The passes reject a few and the planner keeps fifteen of a hundred and
        thirty: without the final-cut stage, every picture in between reads as
        one the run never saw.
        """
        original = _trace()
        original.record(
            "editorial final cut",
            [_Candidate("garden-1"), _Candidate("garden-2"), _Candidate("lake-1")],
            [_Candidate("garden-1"), _Candidate("lake-1")],
        )

        restored = Trace.from_dict(json.loads(json.dumps(original.as_dict())))

        kept_by_every_pass = restored.story_of("garden-2")
        assert kept_by_every_pass.dropped_at == "editorial final cut"
        assert restored.story_of("garden-1").shipped
        # The pass that rejected it said why; the stage only counts it again.
        assert restored.story_of("woods-9").dropped_at == "picture-review"
        assert restored.story_of("woods-9").reason == "a near duplicate of the path shot"


class TestRunSummary:
    def test_the_summary_prints_the_cut_in_order_and_where_to_read_the_rest(self, cut):
        _, attempt = cut
        text = render_run_summary(
            total_seconds=42,
            analysis_seconds=11,
            generation_seconds=31,
            eligible=6,
            planned=3,
            counters=None,
            storyboard=read_storyboard(attempt),
            run_id=RUN_ID,
        )
        assert "3 planned from 6 candidates" in text
        assert "the cut, in order (3 pictures, 0:11 of pictures and video)" in text
        order = [line.split()[1] for line in text.splitlines() if line.lstrip().startswith("0:")]
        assert order == ["2024-06-08", "2024-06-09", "2024-06-21"]
        assert f"runs why <asset id> --run {RUN_ID}" in text
        assert all(len(line) <= 100 for line in text.splitlines())


class TestIncludeExclude:
    def test_generate_offers_include_and_exclude_on_the_real_command(self):
        generate = main.commands["generate"]
        names = {param.name for param in generate.params}
        assert {"include_asset", "exclude_asset"} <= names
        help_text = CliRunner().invoke(main, ["generate", "--help"]).output
        assert "--include ASSET_ID" in help_text
        assert "--exclude ASSET_ID" in help_text

    def test_the_ids_reach_the_editorial_context(self):
        from datetime import datetime

        from immich_memories.cli._editorial_context import build_editorial_context
        from immich_memories.cli._run_inputs import ResolvedRunInputs
        from immich_memories.timeperiod import DateRange

        config = Config()
        window = DateRange(datetime(2024, 1, 1), datetime(2024, 12, 31))
        resolved = ResolvedRunInputs.from_arguments(
            include_photos=False,
            photo_assets=None,
            dry_run=False,
            automation_attempt_id=None,
            upload_to_immich=False,
            config=config,
            person_names=None,
            music=None,
            memory_preset_params=None,
        )
        context = build_editorial_context(
            resolved=resolved,
            config=config,
            memory_type="year_in_review",
            memory_key=None,
            output_stem="year",
            assets=[],
            date_range=window,
            date_ranges=None,
            duration=60.0,
            transition="smart",
            title_override=None,
            person_names=[],
            accept_any_provenance=False,
            owner_required_asset_ids=("keep-me",),
            owner_excluded_asset_ids=("drop-me",),
        )
        assert context.owner_required_asset_ids == ("keep-me",)
        assert context.owner_excluded_asset_ids == ("drop-me",)
