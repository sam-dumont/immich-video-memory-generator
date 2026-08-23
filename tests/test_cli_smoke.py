"""CLI smoke tests using Click's CliRunner."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from click.testing import CliRunner, Result

import immich_memories
from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoAction, AutoOutcome, AutoRejection, AutoRunResult
from immich_memories.automation.runtime_provenance import CheckoutDrift, RuntimeProvenance
from immich_memories.automation.system_scheduler import (
    SchedulerInstallResult,
    WorktreePinnedBinaryError,
)
from immich_memories.cli import main
from immich_memories.config_loader import Config


def _invoke(args: list[str], config: Config | None = None) -> Result:
    """Invoke the CLI with mocked config and init_config_dir."""
    config = config or Config()
    runner = CliRunner()
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return runner.invoke(main, args, catch_exceptions=False)


def _invoke_planned_generation(args: list[str], config: Config) -> Result:
    """Exercise CLI resolution while replacing only live planning boundaries."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_photos_for_date_range.return_value = []
    client.get_person_by_name.return_value = MagicMock(
        id="person-id",
        name="Test Person",
        birth_date=None,
    )
    asset = MagicMock(duration_seconds=10.0)
    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch(
            "immich_memories.cli.generate.fetch_videos_and_live_photos",
            return_value=([asset], []),
        ),
        patch(
            "immich_memories.cli.generate.run_pipeline_and_generate",
            return_value=(Path("dry-run-plan.mp4"), False, None),
        ),
    ):
        return _invoke(args, config=config)


def _auto_candidate() -> MemoryCandidate:
    return MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2026, 7, 1),
        date_range_end=date(2026, 7, 31),
        person_names=[],
        memory_key="monthly:2026-07",
        score=0.7,
        reason="July",
        asset_count=100,
    )


class TestCLIHelp:
    """Test that --help works for all commands."""

    def test_main_help(self):
        """Main group --help returns 0 and shows description."""
        result = _invoke(["--help"])
        assert result.exit_code == 0
        assert "Immich Memories" in result.output

    def test_version(self):
        """--version shows the version string."""
        result = _invoke(["--version"])
        assert result.exit_code == 0
        assert immich_memories.__version__ in result.output

    def test_generate_help(self):
        """generate --help lists expected flags."""
        result = _invoke(["generate", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output
        assert "--start" in result.output
        assert "--end" in result.output

    def test_generate_help_offers_only_implemented_scale_modes(self):
        """--help must not advertise a crop the assembler cannot do, nor a wrong default."""
        result = _invoke(["generate", "--help"])

        assert result.exit_code == 0
        assert "smart_crop" not in result.output
        assert "fit|blur" in result.output.replace("\n", "").replace(" ", "")

    def test_config_help(self):
        """config --help lists subcommands."""
        result = _invoke(["config", "--help"])
        assert result.exit_code == 0

    def test_titles_help(self):
        """titles --help lists subcommands."""
        result = _invoke(["titles", "--help"])
        assert result.exit_code == 0

    def test_music_help(self):
        """music --help lists subcommands."""
        result = _invoke(["music", "--help"])
        assert result.exit_code == 0

    def test_runs_help(self):
        """runs --help lists subcommands."""
        result = _invoke(["runs", "--help"])
        assert result.exit_code == 0

    def test_hardware_help(self):
        """hardware --help works."""
        result = _invoke(["hardware", "--help"])
        assert result.exit_code == 0

    def test_ui_help(self):
        """ui --help works."""
        result = _invoke(["ui", "--help"])
        assert result.exit_code == 0


class TestUIExposureWarning:
    """The UI entry point must make unsafe network exposure hard to miss."""

    @staticmethod
    def _invoke_ui(config: Config) -> tuple[Result, MagicMock]:
        fake_app = ModuleType("immich_memories.ui.app")
        fake_main = MagicMock()
        fake_app.main = fake_main  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"immich_memories.ui.app": fake_app}):
            return _invoke(["ui"], config=config), fake_main

    def test_ui_warns_before_unauthenticated_external_bind(self) -> None:
        config = Config(server={"host": "0.0.0.0"})  # noqa: S104

        result, fake_main = self._invoke_ui(config)

        assert result.exit_code == 0
        assert "Warning: authentication is disabled" in result.stderr
        assert "single-user, single-replica" in result.stderr
        fake_main.assert_called_once_with(port=8080, host="0.0.0.0", reload=False)  # noqa: S104

    def test_ui_does_not_warn_for_loopback_bind(self) -> None:
        config = Config(server={"host": "127.0.0.1"})

        result, _ = self._invoke_ui(config)

        assert result.exit_code == 0
        assert "authentication is disabled" not in result.stderr

    def test_ui_does_not_warn_when_external_bind_has_auth(self) -> None:
        config = Config(
            server={"host": "0.0.0.0"},  # noqa: S104
            auth={"enabled": True, "provider": "basic", "username": "admin", "password": "secret"},  # noqa: S106
        )

        result, _ = self._invoke_ui(config)

        assert result.exit_code == 0
        assert "authentication is disabled" not in result.stderr


class TestCLIGenerateErrors:
    """Test that generate command handles errors gracefully."""

    def test_generate_no_time_period(self):
        """generate without date args fails with usage error."""
        result = _invoke(["generate"])
        # Should fail since no time period specified
        assert result.exit_code != 0

    def test_generate_dry_run_no_immich(self):
        """generate --dry-run with empty Immich URL shows error."""
        config = Config()  # Empty URL
        result = _invoke(["generate", "--year", "2024", "--dry-run"], config=config)
        # Should error about missing Immich connection
        assert result.exit_code != 0


class TestCLIMemoryTypeFlags:
    """Test that new memory type CLI flags appear in help."""

    def test_memory_type_in_help(self):
        """generate --help shows --memory-type flag."""
        result = _invoke(["generate", "--help"])
        assert "--memory-type" in result.output

    def test_season_in_help(self):
        """generate --help shows --season flag."""
        result = _invoke(["generate", "--help"])
        assert "--season" in result.output

    def test_month_in_help(self):
        """generate --help shows --month flag."""
        result = _invoke(["generate", "--help"])
        assert "--month" in result.output

    def test_hemisphere_in_help(self):
        """generate --help shows --hemisphere flag."""
        result = _invoke(["generate", "--help"])
        assert "--hemisphere" in result.output

    def test_person_allows_multiple(self):
        """--person flag accepts multiple values."""
        result = _invoke(["generate", "--help"])
        assert "--person" in result.output


class TestCLIMemoryTypeResolve:
    """Test memory type date resolution in dry-run mode."""

    def test_season_dry_run(self):
        """--memory-type season --season summer resolves to summer date range."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke_planned_generation(
            [
                "generate",
                "--memory-type",
                "season",
                "--season",
                "summer",
                "--year",
                "2024",
                "--dry-run",
            ],
            config,
        )
        assert result.exit_code == 0
        assert "Dry-run planning complete" in result.output

    def test_monthly_dry_run(self):
        """--memory-type monthly_highlights --month 7 resolves correctly."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke_planned_generation(
            [
                "generate",
                "--memory-type",
                "monthly_highlights",
                "--month",
                "7",
                "--year",
                "2024",
                "--dry-run",
            ],
            config,
        )
        assert result.exit_code == 0
        assert "Dry-run planning complete" in result.output

    def test_on_this_day_dry_run(self):
        """--memory-type on_this_day resolves with default target date."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke_planned_generation(
            ["generate", "--memory-type", "on_this_day", "--dry-run"],
            config,
        )
        assert result.exit_code == 0
        assert "Dry-run planning complete" in result.output

    def test_year_in_review_default(self):
        """--memory-type year_in_review with --year works (backward compat)."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke_planned_generation(
            ["generate", "--memory-type", "year_in_review", "--year", "2024", "--dry-run"],
            config,
        )
        assert result.exit_code == 0

    def test_person_spotlight_requires_person(self):
        """--memory-type person_spotlight without --person shows error."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke(
            ["generate", "--memory-type", "person_spotlight", "--year", "2024", "--dry-run"],
            config=config,
        )
        assert result.exit_code != 0


class TestQuietFlag:
    """Test --quiet flag appears and is accepted."""

    def test_quiet_in_help(self):
        """generate --help shows --quiet flag."""
        result = _invoke(["generate", "--help"])
        assert "--quiet" in result.output

    def test_quiet_dry_run(self):
        """--quiet flag accepted with dry run."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke_planned_generation(
            ["generate", "--year", "2024", "--dry-run", "--quiet"],
            config,
        )
        assert result.exit_code == 0


class TestAutoRunOutput:
    def test_root_custom_config_path_reaches_auto_runner(self, tmp_path: Path) -> None:
        """The root option remains provenance after Click loads the config object."""
        config_path = tmp_path / "Config dir" / "family & photos.yaml"
        config_path.parent.mkdir()
        Config(
            immich={"url": "http://immich.test", "api_key": "test-key"},
            cache={"database": str(tmp_path / "cache.db")},
        ).save_yaml(config_path)
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.SKIPPED,
            reason="cooldown active",
            action=AutoAction.GENERATION,
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner) as cls:
            result = CliRunner().invoke(
                main,
                ["--config", str(config_path), "auto", "run", "--quiet"],
            )

        assert result.exit_code == 0, result.output
        assert cls.call_args.kwargs["config_path"] == config_path.resolve()

    def test_root_custom_config_path_reaches_scheduler_renderer(self, tmp_path: Path) -> None:
        """Installed daily jobs must retain the config selected during installation."""
        config_path = tmp_path / "Config dir" / "family & photos.yaml"
        config_path.parent.mkdir()
        Config().save_yaml(config_path)

        with patch(
            "immich_memories.automation.system_scheduler.show_scheduler_config",
            return_value="scheduler definition",
        ) as show:
            result = CliRunner().invoke(
                main,
                ["--config", str(config_path), "auto", "install", "--show"],
            )

        assert result.exit_code == 0, result.output
        show.assert_called_once_with(9, 0, 24, config_path=config_path.resolve())

    def test_worktree_pinned_install_fails_loudly_and_points_at_force(self) -> None:
        """A scheduler pinned to scratch space is a silent stale-code bug, so refuse it."""
        # WHY: install_scheduler touches launchd/systemd/crontab on the host
        with patch(
            "immich_memories.automation.system_scheduler.install_scheduler",
            side_effect=WorktreePinnedBinaryError("/wt/.venv/bin/immich-memories", Path("/wt")),
        ):
            result = _invoke(["auto", "install"])

        assert result.exit_code == 1
        assert "/wt" in result.output
        assert "--force" in result.output

    def test_force_passes_the_override_through_to_the_installer(self) -> None:
        # WHY: install_scheduler touches launchd/systemd/crontab on the host
        with patch(
            "immich_memories.automation.system_scheduler.install_scheduler",
            return_value=SchedulerInstallResult(platform="crontab"),
        ) as install:
            result = _invoke(["auto", "install", "--force"])

        assert result.exit_code == 0, result.output
        assert install.call_args.kwargs["force"] is True

    def test_quiet_completed_emits_exactly_one_json_object(self, tmp_path: Path) -> None:
        output = tmp_path / "memory.mp4"
        auto_result = AutoRunResult(
            outcome=AutoOutcome.COMPLETED,
            reason="generation completed",
            action=AutoAction.GENERATION,
            candidate=_auto_candidate(),
            run_id="run-123",
            output_path=output,
        )
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = auto_result

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet"])

        assert result.exit_code == 0
        assert result.stdout.count("\n") == 1
        payload = json.loads(result.stdout)
        assert payload.pop("runtime")["version"] == immich_memories.__version__
        assert payload == {
            "outcome": "completed",
            "action": "generation",
            "reason": "generation completed",
            "candidate_key": "monthly:2026-07",
            "category": "monthly_review",
            "run_id": "run-123",
            "error": None,
            "output_path": str(output),
            "recent_categories": [],
            "rejections": [],
        }

    def test_stale_checkout_warns_on_stderr_even_when_quiet(self) -> None:
        """--quiet silences logging, so the one thing #573 needed must not go through it."""
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.SKIPPED, reason="cooldown active"
        )
        stale = RuntimeProvenance(
            version="1.2.3",
            checkout=Path("/home/me/.immich-memories/runtime"),
            commit="ea892ad",
            drift=CheckoutDrift(upstream="origin/main", commits_behind=32),
        )

        with (
            patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner),
            # WHY: runtime_provenance shells out to git against the real checkout
            patch(
                "immich_memories.automation.runtime_provenance.runtime_provenance",
                return_value=stale,
            ),
        ):
            result = _invoke(["auto", "run", "--quiet"])

        assert result.exit_code == 0
        assert "32 commit(s)" in result.stderr
        assert "origin/main" in result.stderr
        assert json.loads(result.stdout)["runtime"]["stale"] is True

    def test_human_completed_states_outcome_and_reason(self, tmp_path: Path) -> None:
        output = tmp_path / "memory.mp4"
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.COMPLETED,
            reason="generation completed",
            candidate=_auto_candidate(),
            run_id="run-123",
            output_path=output,
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run"])

        assert result.exit_code == 0
        assert "completed" in result.output
        assert "generation completed" in result.output

    def test_skipped_is_zero_and_json_has_null_identity(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.SKIPPED,
            reason="cooldown active",
            action=AutoAction.GENERATION,
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload.pop("runtime")["version"] == immich_memories.__version__
        assert payload == {
            "outcome": "skipped",
            "action": "generation",
            "reason": "cooldown active",
            "candidate_key": None,
            "category": None,
            "run_id": None,
            "error": None,
            "output_path": None,
            "recent_categories": [],
            "rejections": [],
        }

    def test_dry_run_is_zero_success(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.DRY_RUN,
            reason="dry run",
            action=AutoAction.GENERATION,
            candidate=_auto_candidate(),
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet", "--dry-run"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "dry_run"
        assert payload["category"] == "monthly_review"

    def test_quiet_variety_rejection_exposes_rotation_and_rules(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.SKIPPED,
            reason="no eligible candidates",
            action=AutoAction.GENERATION,
            recent_categories=("monthly_review", "trip"),
            rejections=(
                AutoRejection(
                    category="monthly_review",
                    memory_key="monthly:2026-07",
                    rule="same_category_as_previous",
                ),
            ),
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet", "--dry-run"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload.pop("runtime")["version"] == immich_memories.__version__
        assert payload == {
            "outcome": "skipped",
            "action": "generation",
            "reason": "no eligible candidates",
            "candidate_key": None,
            "category": None,
            "run_id": None,
            "error": None,
            "output_path": None,
            "recent_categories": ["monthly_review", "trip"],
            "rejections": [
                {
                    "category": "monthly_review",
                    "memory_key": "monthly:2026-07",
                    "rule": "same_category_as_previous",
                }
            ],
        }

    def test_human_dry_run_explains_variety_rejection(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.SKIPPED,
            reason="no eligible candidates",
            recent_categories=("monthly_review", "trip"),
            rejections=(
                AutoRejection(
                    category="monthly_review",
                    memory_key="monthly:2026-07",
                    rule="same_category_as_previous",
                ),
            ),
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--dry-run"])

        assert result.exit_code == 0
        assert "Recent auto categories: monthly_review, trip" in result.output
        assert (
            "Rejected monthly_review (monthly:2026-07): same_category_as_previous" in result.output
        )

    def test_failed_writes_error_to_stderr_and_exits_one(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.FAILED,
            reason="generation subprocess exited with code 7",
            action=AutoAction.GENERATION,
            candidate=_auto_candidate(),
            error="root cause on stdout",
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload.pop("runtime")["version"] == immich_memories.__version__
        assert payload == {
            "outcome": "failed",
            "action": "generation",
            "reason": "generation subprocess exited with code 7",
            "candidate_key": "monthly:2026-07",
            "category": "monthly_review",
            "run_id": None,
            # The cause the child wrote to stdout, which previously reached
            # stderr only and never the machine-readable result.
            "error": "root cause on stdout",
            "output_path": None,
            "recent_categories": [],
            "rejections": [],
        }
        assert "root cause on stdout" in result.stderr

    def test_failed_delivery_retry_exposes_action_and_exits_one(self) -> None:
        auto_runner = MagicMock()
        auto_runner.run_one.return_value = AutoRunResult(
            outcome=AutoOutcome.FAILED,
            reason="pending delivery failed",
            action=AutoAction.DELIVERY_RETRY,
            run_id="run-pending",
            error="safe upload error",
        )

        with patch("immich_memories.automation.runner.AutoRunner", return_value=auto_runner):
            result = _invoke(["auto", "run", "--quiet"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["action"] == "delivery_retry"
        assert payload["run_id"] == "run-pending"
        assert "safe upload error" in result.stderr

    def test_real_preflight_error_path_is_failed_json_and_exit_one(self, tmp_path: Path) -> None:
        from immich_memories.preflight import CheckResult, CheckStatus

        config = Config(
            immich={"url": "http://immich.test:2283", "api_key": "preflight-secret"},
            cache={"database": str(tmp_path / "runs.db")},
        )
        preflight = CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Authentication failed",
            details="API key rejected: preflight-secret",
        )

        with patch("immich_memories.preflight.check_immich", return_value=preflight):
            result = _invoke(["auto", "run", "--force", "--quiet"], config=config)

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "failed"
        assert payload["reason"] == "Immich preflight failed"
        assert "Authentication failed" in result.stderr
        assert "preflight-secret" not in result.stderr

    def test_auto_suggest_reports_preflight_error_without_second_check(
        self, tmp_path: Path
    ) -> None:
        from immich_memories.preflight import CheckResult, CheckStatus

        config = Config(
            immich={"url": "http://immich.test:2283", "api_key": "test-key"},
            cache={"database": str(tmp_path / "runs.db")},
        )
        preflight = CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details="server unavailable",
        )

        with patch("immich_memories.preflight.check_immich", return_value=preflight) as check:
            result = _invoke(["auto", "suggest"], config=config)

        assert result.exit_code == 1
        assert "Immich preflight failed" in result.stderr
        assert "server unavailable" in result.stderr
        check.assert_called_once()

    def test_auto_suggest_says_when_a_candidate_is_backing_off(self, tmp_path: Path) -> None:
        """Silently omitting a suppressed candidate reads as "nothing to do".

        The operator needs to tell "no candidates" apart from "the candidate you
        expect keeps failing and is waiting", or a stuck memory looks like an
        idle library.
        """
        from immich_memories.automation.models import AutoOutcome
        from immich_memories.automation.state_store import AutomationStateStore
        from immich_memories.preflight import CheckStatus

        config = Config(
            immich={"url": "http://immich.test:2283", "api_key": "test-key"},
            cache={"database": str(tmp_path / "runs.db")},
        )
        store = AutomationStateStore(tmp_path / "runs.db")
        for _ in range(2):
            attempt = store.start_attempt(reason="daily wake")
            store.finish_attempt(
                attempt.id, AutoOutcome.FAILED, reason="exit 1", memory_key="monthly:2026-06"
            )

        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get_time_buckets.return_value = []
        client.get_all_people.return_value = []

        # WHY: external Immich server and its preflight check
        with (
            # WHY: external Immich server
            patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
            # WHY: external Immich server (preflight check)
            patch(
                "immich_memories.preflight.check_immich",
                return_value=MagicMock(status=CheckStatus.OK),
            ),
        ):
            result = _invoke(["auto", "suggest"], config=config)

        assert "Backing off monthly:2026-06" in result.output
        assert "failed 2x" in result.output


class TestPreflightCommand:
    """Test preflight command with real checks (no mocks)."""

    def test_preflight_with_no_servers_shows_errors(self):
        """Preflight with no servers configured should show errors but not crash."""
        config = Config()  # Default config — no real servers
        result = _invoke(["preflight"], config=config)

        # Should complete (exit 0 or 1) and display the results table
        assert result.exit_code in (0, 1)
        assert "Preflight" in result.output or "Provider" in result.output
