"""Tests for /health endpoint functions."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

import immich_memories
from immich_memories.config_loader import Config
from immich_memories.security import configured_secret_values


def _config_with_every_secret_class() -> Config:
    """Build one health config covering the centralized credential inventory."""
    return Config(
        immich={"url": "http://immich.secret.test", "api_key": "immich-log-secret"},
        llm={"api_key": "primary-llm-log-secret"},
        title_llm={"api_key": "title-llm-log-secret"},
        musicgen={"api_key": "musicgen-log-secret"},
        ace_step={"api_key": "ace-step-log-secret"},
        auth={
            "password": "basic-password-log-secret",
            "client_secret": "oidc-client-log-secret",
        },
        notifications={"urls": ["https://notify.test/notification-log-secret"]},
    )


class TestHealthEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failing_boundary", "expected_diagnostic"),
        [("automation", "automation status"), ("last_run", "run status")],
    )
    async def test_optional_status_errors_log_only_type_not_url_or_secrets(
        self,
        caplog: pytest.LogCaptureFixture,
        failing_boundary: str,
        expected_diagnostic: str,
    ) -> None:
        """Optional status failures retain class diagnostics without their sensitive message."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = _config_with_every_secret_class()
        sensitive_values = (config.immich.url, *configured_secret_values(config))
        failure = RuntimeError(f"{failing_boundary} failure " + " ".join(sensitive_values))
        automation = MagicMock(return_value=None)
        last_run = MagicMock(return_value=None)
        if failing_boundary == "automation":
            automation.side_effect = failure
        else:
            last_run.side_effect = failure

        with (
            caplog.at_level(logging.WARNING, logger="immich_memories.ui.app"),
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=_ImmichDependency(
                    status="ready",
                    reachable=True,
                    resolved_api_version="v3",
                ),
            ),
            patch("immich_memories.ui.app._get_automation_status", automation),
            patch("immich_memories.ui.app._get_last_successful_run", last_run),
        ):
            response = await _readiness_handler(MagicMock())

        assert response.status_code == 200
        assert expected_diagnostic in caplog.text
        assert "RuntimeError" in caplog.text
        for sensitive in sensitive_values:
            assert sensitive not in caplog.text

    def test_registered_health_routes_keep_individual_fallbacks_without_config(self):
        """The real app bypasses config before exact liveness/readiness/legacy handlers."""
        from immich_memories.ui.app import app

        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "immich_memories.ui.app.get_config",
            side_effect=RuntimeError("configuration unavailable"),
        ):
            live = client.get("/health/live")
            ready = client.get("/health/ready")
            legacy = client.get("/health")

        assert live.status_code == 200
        assert live.json() == {"status": "alive", "version": immich_memories.__version__}
        assert ready.status_code == 503
        assert ready.json()["status"] == "degraded"
        assert legacy.status_code == 200
        assert legacy.json()["status"] == "degraded"

    def test_registered_readiness_uses_one_configuration_snapshot(self):
        """A real detailed request cannot reload config while reading run history."""
        from immich_memories.ui.app import _ImmichDependency, app

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = TestClient(app, raise_server_exceptions=False)
        with (
            patch("immich_memories.ui.app.get_config", return_value=config) as get_config_mock,
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=_ImmichDependency(
                    status="ready",
                    reachable=True,
                    resolved_api_version="v3",
                ),
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.tracking.run_database.RunDatabase") as database,
        ):
            database.return_value.list_runs.return_value = []
            response = client.get("/health/ready")

        assert response.status_code == 200
        assert get_config_mock.call_count == 1

    def test_automation_status_truthfully_defaults_pending_delivery_without_queue(
        self, tmp_path: Path
    ):
        """Until Encoding Task 5, the real producer reports no pending delivery state."""
        from immich_memories.automation.runner import AutoRunner

        config = Config(
            cache={"database": str(tmp_path / "health.db"), "directory": str(tmp_path / "cache")}
        )

        status = AutoRunner(config).status().to_dict()

        assert status["pending_delivery_count"] == 0
        assert status["oldest_pending_delivery"] is None

    @pytest.mark.asyncio
    async def test_readiness_projects_real_automation_delivery_defaults(self, tmp_path: Path):
        """The detailed endpoint consumes delivery fields from the real status producer."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = Config(
            immich={"url": "http://immich.test", "api_key": "health-secret"},
            cache={"database": str(tmp_path / "health.db"), "directory": str(tmp_path / "cache")},
        )
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=_ImmichDependency(
                    status="ready",
                    reachable=True,
                    resolved_api_version="v3",
                ),
            ),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        data = json.loads(response.body)
        assert response.status_code == 200
        assert data["pending_delivery_count"] == 0
        assert data["oldest_pending_delivery"] is None

    @pytest.mark.asyncio
    async def test_notification_cooldown_warns_without_failing_readiness(self, tmp_path: Path):
        from immich_memories.automation.notification_state import (
            NotificationFailureCategory,
            NotificationStateStore,
        )
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = Config(
            immich={"url": "http://immich.test", "api_key": "health-secret"},
            cache={"database": str(tmp_path / "health.db"), "directory": str(tmp_path / "cache")},
            notifications={"enabled": True, "urls": ["ntfy://topic"]},
        )
        NotificationStateStore(config.cache.database_path).record_failure(
            NotificationFailureCategory.QUOTA
        )
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=_ImmichDependency(
                    status="ready", reachable=True, resolved_api_version="v3"
                ),
            ),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        data = json.loads(response.body)
        assert response.status_code == 200
        assert data["status"] == "ready"
        assert data["notification_health"]["cooldown_active"] is True

    @pytest.mark.asyncio
    async def test_liveness_is_process_only(self):
        """Liveness must stay green without consulting config, Immich, or SQLite."""
        from immich_memories.ui.app import _liveness_handler

        config = MagicMock(name="get_config")
        immich = MagicMock(name="check_immich_dependency")
        automation = MagicMock(name="get_automation_status")
        last_run = MagicMock(name="get_last_successful_run")
        with (
            patch("immich_memories.ui.app.get_config", config),
            patch("immich_memories.ui.app._check_immich_dependency", immich),
            patch("immich_memories.ui.app._get_automation_status", automation),
            patch("immich_memories.ui.app._get_last_successful_run", last_run),
        ):
            response = await _liveness_handler(MagicMock())

        assert response.status_code == 200
        assert json.loads(response.body) == {
            "status": "alive",
            "version": immich_memories.__version__,
        }
        config.assert_not_called()
        immich.assert_not_called()
        automation.assert_not_called()
        last_run.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failing_boundary",
        ["automation", "last_run"],
    )
    async def test_health_database_failures_never_log_configured_secrets(
        self,
        caplog,
        failing_boundary,
    ):
        """Database diagnostics must be sanitized before response or logging boundaries."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = _config_with_every_secret_class()
        secrets = configured_secret_values(config)
        failure = RuntimeError("database failed: " + " / ".join(secrets))
        automation = MagicMock(return_value={})
        last_run = MagicMock(return_value=None)
        if failing_boundary == "automation":
            automation.side_effect = failure
        else:
            last_run.side_effect = failure

        caplog.set_level(logging.WARNING, logger="immich_memories.ui.app")
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=_ImmichDependency(
                    status="ready",
                    reachable=True,
                    resolved_api_version="v3",
                ),
            ),
            patch("immich_memories.ui.app._get_automation_status", automation),
            patch("immich_memories.ui.app._get_last_successful_run", last_run),
        ):
            response = await _readiness_handler(MagicMock())

        assert response.status_code == 200
        assert "Could not read" in caplog.text
        for secret in secrets:
            assert secret not in caplog.text
            assert secret not in bytes(response.body).decode()

    @pytest.mark.asyncio
    async def test_readiness_is_503_when_configuration_is_missing(self):
        """A default config cannot generate memories and must not be reported ready."""
        from immich_memories.ui.app import _readiness_handler

        with (
            patch("immich_memories.ui.app.get_config", return_value=Config()),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                side_effect=AssertionError("missing config should not contact Immich"),
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        data = json.loads(response.body)
        assert response.status_code == 503
        assert data["status"] == "degraded"
        assert data["configuration"] == "missing"
        assert data["immich"]["status"] == "missing_configuration"
        assert data["immich_reachable"] is False

    @pytest.mark.asyncio
    async def test_readiness_is_503_when_immich_is_unreachable(self):
        """An unreachable configured server must fail the readiness gate."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        dependency = _ImmichDependency(status="unreachable", reachable=False)
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=dependency,
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        assert response.status_code == 503
        assert json.loads(response.body)["immich"]["status"] == "unreachable"

    @pytest.mark.asyncio
    async def test_readiness_is_503_for_unsupported_immich(self):
        """Auto-detecting an unsupported major must fail the readiness gate."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        dependency = _ImmichDependency(status="unsupported_version", reachable=True)
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=dependency,
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        assert response.status_code == 503
        assert json.loads(response.body)["immich"]["status"] == "unsupported_version"

    @pytest.mark.asyncio
    async def test_ready_snapshot_exposes_policy_version_and_automation_without_secrets(self):
        """Operators get actionable state, while configured credentials never enter JSON."""
        from immich_memories.ui.app import _ImmichDependency, _readiness_handler

        secret = "never-serialize-this-key"  # noqa: S105
        config = Config(immich={"url": "http://immich.test", "api_key": secret})
        dependency = _ImmichDependency(
            status="ready",
            reachable=True,
            resolved_api_version="v3",
        )
        automation = {
            "last_attempt": {
                "outcome": "completed",
                "started_at": "2026-08-11T06:00:00Z",
                "error": f"legacy transport error: {secret}",
            },
            "last_completed_auto_run": {
                "run_id": "auto-42",
                "completed_at": "2026-08-11T06:15:00Z",
            },
        }
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=dependency,
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=automation),
            patch(
                "immich_memories.ui.app._get_last_successful_run",
                return_value="2026-08-11T06:15:00+00:00",
            ),
        ):
            response = await _readiness_handler(MagicMock())

        data = json.loads(response.body)
        assert response.status_code == 200
        assert data["status"] == "ready"
        assert data["version"] == immich_memories.__version__
        assert data["immich"] == {
            "status": "ready",
            "reachable": True,
            "api_version_policy": "auto",
            "resolved_api_version": "v3",
        }
        assert data["automation"]["last_attempt"] == {
            "outcome": "completed",
            "started_at": "2026-08-11T06:00:00Z",
            "error": "legacy transport error: ***",
        }
        assert (
            data["automation"]["last_completed_auto_run"] == automation["last_completed_auto_run"]
        )
        assert data["last_automation_attempt"] == data["automation"]["last_attempt"]
        assert data["last_successful_auto_run"] == automation["last_completed_auto_run"]
        assert secret not in bytes(response.body).decode()

    @pytest.mark.asyncio
    async def test_legacy_health_keeps_detailed_payload_and_http_200_when_degraded(self):
        """Compatibility clients retain details without inheriting readiness status codes."""
        from immich_memories.ui.app import _health_handler, _ImmichDependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        dependency = _ImmichDependency(status="unreachable", reachable=False)
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=dependency,
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _health_handler(MagicMock())

        data = json.loads(response.body)
        assert response.status_code == 200
        assert data["status"] == "degraded"
        assert data["immich"]["status"] == "unreachable"

    @pytest.mark.asyncio
    async def test_legacy_health_keeps_ok_status_when_dependency_is_healthy(self):
        """Compatibility health keeps its established healthy status label."""
        from immich_memories.ui.app import _health_handler, _ImmichDependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        dependency = _ImmichDependency(status="ready", reachable=True, resolved_api_version="v3")
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch(
                "immich_memories.ui.app._check_immich_dependency",
                new_callable=AsyncMock,
                return_value=dependency,
            ),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _health_handler(MagicMock())

        assert response.status_code == 200
        assert json.loads(response.body)["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_handler_returns_json(self):
        """Health handler should return valid JSON with expected keys."""
        from immich_memories.ui.app import _health_handler

        mock_request = MagicMock()

        # WHY: detailed health reads both disk configuration and SQLite status.
        with (
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
            patch("immich_memories.ui.app.get_config", return_value=Config()),
        ):
            response = await _health_handler(mock_request)

        data = json.loads(response.body)
        assert "status" in data
        assert "immich_reachable" in data
        assert data["version"] == immich_memories.__version__

    @pytest.mark.asyncio
    async def test_dependency_probe_resolves_version_and_authenticates(self):
        """A usable connection reports the selected API compatibility version."""
        from immich_memories.api.compatibility import ResolvedApiVersion
        from immich_memories.ui.app import _check_immich_dependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get_api_version.return_value = ResolvedApiVersion.V3
        client.get_current_user.return_value = MagicMock()
        with patch("immich_memories.api.immich.ImmichClient", return_value=client):
            result = await _check_immich_dependency(config)

        assert result.status == "ready"
        assert result.reachable is True
        assert result.resolved_api_version == "v3"
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dependency_probe_accepts_a_supported_v2_server(self):
        """A configured v2 server is just as ready as the existing v3 path."""
        from immich_memories.api.compatibility import ResolvedApiVersion
        from immich_memories.ui.app import _check_immich_dependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get_api_version.return_value = ResolvedApiVersion.V2
        with patch("immich_memories.api.immich.ImmichClient", return_value=client):
            result = await _check_immich_dependency(config)

        assert result.status == "ready"
        assert result.resolved_api_version == "v2"
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dependency_probe_reports_reachable_authentication_failure(self):
        """A rejected key means the server answered, but it is not ready for work."""
        from immich_memories.api.compatibility import ResolvedApiVersion
        from immich_memories.api.immich import ImmichAuthError
        from immich_memories.ui.app import _check_immich_dependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get_api_version.return_value = ResolvedApiVersion.V3
        client.get_current_user.side_effect = ImmichAuthError("Invalid API key")
        with patch("immich_memories.api.immich.ImmichClient", return_value=client):
            result = await _check_immich_dependency(config)

        assert result.status == "authentication_failed"
        assert result.reachable is True
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_readiness_timeout_returns_503_and_closes_cancelled_client(self):
        """A cancelled dependency probe releases its client and degrades readiness."""
        from immich_memories.ui.app import _readiness_handler

        async def cancelled_wait_for(operation, timeout):  # noqa: ARG001
            task = asyncio.create_task(operation)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            raise TimeoutError

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = AsyncMock()
        client.__aenter__.return_value = client
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch("immich_memories.api.immich.ImmichClient", return_value=client),
            patch("immich_memories.ui.app.asyncio.wait_for", new=cancelled_wait_for),
            patch("immich_memories.ui.app._get_automation_status", return_value=None),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
        ):
            response = await _readiness_handler(MagicMock())

        assert response.status_code == 503
        assert json.loads(response.body)["immich"]["status"] == "unreachable"
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dependency_probe_distinguishes_an_unsupported_server(self):
        """A responding unsupported major is different from a network outage."""
        from immich_memories.api.compatibility import UnsupportedImmichVersion
        from immich_memories.ui.app import _check_immich_dependency

        config = Config(immich={"url": "http://immich.test", "api_key": "health-secret"})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get_api_version.side_effect = UnsupportedImmichVersion("major 4")
        with patch("immich_memories.api.immich.ImmichClient", return_value=client):
            result = await _check_immich_dependency(config)

        assert result.status == "unsupported_version"
        assert result.reachable is True

    def test_get_last_successful_run_returns_none_when_no_runs(self):
        """Should return None when no completed runs exist."""
        from immich_memories.ui.app import _get_last_successful_run

        # WHY: RunDatabase reads from SQLite file (lazy import inside function)
        with patch("immich_memories.tracking.run_database.RunDatabase") as mock_db_cls:
            mock_db_cls.return_value.list_runs.return_value = []
            result = _get_last_successful_run(Config())

        assert result is None
