"""The HTTP trigger: one POST that runs the decision `auto run` would make."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from immich_memories.automation.runner import StartedAutoRun
from immich_memories.config_loader import Config
from immich_memories.security import configured_secret_values

TOKEN = "workflow-token"  # noqa: S105


def _config(tmp_path: Path, **server: object) -> Config:
    return Config(
        immich={"url": "http://immich.test", "api_key": "immich-key"},
        cache={"database": str(tmp_path / "runs.db"), "directory": str(tmp_path / "cache")},
        server={"trigger_token": TOKEN, **server},
    )


@contextmanager
def _serving(config: Config) -> Iterator[None]:
    """Answer requests from *config* rather than from whatever this machine has."""
    # WHY: replaces the on-disk config file the server reads on every request.
    with patch("immich_memories.ui.trigger_api.get_config", return_value=config):
        yield


class _RecordingWorker:
    """The background worker, replaced by one that only hands the lease back.

    Executing for real would run a generation subprocess for up to two hours;
    everything up to the handover — lease, attempt row, response — stays real.
    """

    def __init__(self) -> None:
        self.submitted: list[StartedAutoRun] = []

    def __call__(self, started: StartedAutoRun) -> None:
        self.submitted.append(started)
        started.lease.release()


@pytest.fixture
def worker() -> _RecordingWorker:
    return _RecordingWorker()


@pytest.fixture
def client(worker: _RecordingWorker) -> TestClient:
    """A bare app carrying only the trigger routes, wired to the fake worker."""
    from immich_memories.ui.trigger_api import register_trigger_routes

    app = FastAPI()
    register_trigger_routes(app, submit=worker)
    return TestClient(app, raise_server_exceptions=False)


class TestTriggerTokenIsASecret:
    def test_configured_token_joins_the_redacted_credential_inventory(self) -> None:
        """Anything that can start a run must never reach a log or /health verbatim."""
        config = Config(server={"trigger_token": "workflow-token-secret"})

        assert "workflow-token-secret" in configured_secret_values(config)

    def test_the_config_viewer_masks_it_like_every_other_secret(self) -> None:
        from immich_memories.ui.pages.settings_config import redact_config

        redacted = redact_config({"server": {"trigger_token": "workflow-token-secret"}})

        assert redacted["server"]["trigger_token"] == "***"  # noqa: S105


class TestTriggerIsOffUntilSomethingAuthenticatesIt:
    """This process holds an Immich API key; an anonymous caller cannot spend it."""

    @pytest.mark.parametrize(
        ("auth", "server", "expected"),
        [
            ({}, {}, False),
            ({}, {"trigger_token": "workflow-token"}, True),
            ({"enabled": True, "username": "u", "password": "p"}, {}, True),
            (
                {"enabled": True, "username": "u", "password": "p"},
                {"trigger_token": "workflow-token"},
                True,
            ),
        ],
    )
    def test_endpoint_requires_auth_or_a_token(
        self, auth: dict, server: dict, expected: bool
    ) -> None:
        from immich_memories.ui.trigger_api import trigger_enabled

        assert trigger_enabled(Config(auth=auth, server=server)) is expected


class TestTokenPresentation:
    @pytest.mark.parametrize(
        "headers",
        [
            {"x-api-key": "workflow-token"},
            {"X-API-Key": "workflow-token"},
            {"authorization": "Bearer workflow-token"},
            {"authorization": "bearer workflow-token"},
        ],
    )
    def test_either_header_carries_the_token(self, headers: dict) -> None:
        """Immich's own API speaks x-api-key; everything else speaks Bearer."""
        from starlette.datastructures import Headers

        from immich_memories.ui.auth import presented_trigger_token

        assert presented_trigger_token(Headers(headers)) == "workflow-token"

    @pytest.mark.parametrize(
        "headers", [{}, {"authorization": "Basic dXNlcjpwYXNz"}, {"x-api-key": ""}]
    )
    def test_nothing_else_counts_as_an_offer(self, headers: dict) -> None:
        from starlette.datastructures import Headers

        from immich_memories.ui.auth import presented_trigger_token

        assert presented_trigger_token(Headers(headers)) == ""

    @pytest.mark.parametrize(
        ("presented", "configured", "expected"),
        [
            ("workflow-token", "workflow-token", True),
            ("workflow-toke", "workflow-token", False),
            ("", "", False),
            ("anything", "", False),
        ],
    )
    def test_an_unset_token_authorizes_nobody(
        self, presented: str, configured: str, expected: bool
    ) -> None:
        """A blank config value must not turn a blank header into a valid caller."""
        from immich_memories.ui.auth import trigger_token_matches

        assert trigger_token_matches(presented, configured) is expected


class TestPostEnqueuesOneDecision:
    def test_accepted_call_answers_with_the_attempt_it_started(
        self, client: TestClient, worker: _RecordingWorker, tmp_path: Path
    ) -> None:
        """A workflow gets an id back immediately; the generation is still ahead of it."""
        from immich_memories.automation.models import AutoOutcome
        from immich_memories.automation.state_store import AutomationStateStore

        config = _config(tmp_path)
        with _serving(config):
            response = client.post("/api/trigger", headers={"x-api-key": TOKEN})

        body = response.json()
        assert response.status_code == 202
        assert body["status"] == "accepted"
        assert body["status_url"] == f"/api/trigger/{body['attempt_id']}"
        assert [started.attempt.id for started in worker.submitted] == [body["attempt_id"]]

        stored = AutomationStateStore(config.cache.database_path).get_attempt(body["attempt_id"])
        assert stored is not None
        assert stored.outcome is AutoOutcome.RUNNING
        assert stored.reason == "http trigger"

    def test_a_second_call_while_a_run_is_active_reports_the_active_one(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Single-flight is the existing lease, so a CLI run blocks a trigger too."""
        from immich_memories.automation.runner import AutoRunner

        config = _config(tmp_path)
        active = AutoRunner(config).start_one(reason="daily wake")
        try:
            with _serving(config):
                response = client.post("/api/trigger", headers={"x-api-key": TOKEN})
        finally:
            active.lease.release()

        assert response.status_code == 409
        assert response.json()["attempt_id"] == active.attempt.id

    @pytest.mark.parametrize(
        "headers", [{}, {"x-api-key": "wrong-token"}, {"authorization": "Bearer wrong-token"}]
    )
    def test_a_caller_without_the_token_starts_nothing(
        self, client: TestClient, worker: _RecordingWorker, tmp_path: Path, headers: dict
    ) -> None:
        config = _config(tmp_path)
        with _serving(config):
            response = client.post("/api/trigger", headers=headers)

        assert response.status_code == 401
        assert worker.submitted == []

    def test_the_routes_are_absent_when_nothing_authenticates_them(
        self, client: TestClient, worker: _RecordingWorker, tmp_path: Path
    ) -> None:
        """No auth and no token: this process holds an Immich key, so there is no endpoint."""
        config = _config(tmp_path, trigger_token="")
        with _serving(config):
            response = client.post("/api/trigger")

        assert response.status_code == 404
        assert worker.submitted == []


class TestStatusReportsWhatTheRunIsDoing:
    def _get(self, client: TestClient, config: Config, attempt_id: str):
        with _serving(config):
            return client.get(f"/api/trigger/{attempt_id}", headers={"x-api-key": TOKEN})

    def test_an_id_nobody_started_is_a_404(self, client: TestClient, tmp_path: Path) -> None:
        response = self._get(client, _config(tmp_path), "never-started")

        assert response.status_code == 404

    def test_a_running_attempt_reports_the_phase_the_generation_reached(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """The generation subprocess writes its phase to this row, so polling shows progress."""
        from immich_memories.automation.state_store import AutomationStateStore
        from immich_memories.operations.phases import OperationalPhase, PhaseEvent

        config = _config(tmp_path)
        store = AutomationStateStore(config.cache.database_path)
        attempt = store.start_attempt(reason="http trigger")
        store.update_phase(attempt.id, PhaseEvent(OperationalPhase.ANALYSIS, 3, 10, "scoring", 0.3))

        body = self._get(client, config, attempt.id).json()

        assert body["state"] == "running"
        assert body["phase"] == OperationalPhase.ANALYSIS.value
        assert body["run"] is None

    def test_a_completed_attempt_carries_its_record_from_the_run_database(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from datetime import UTC, datetime

        from immich_memories.automation.models import AutoOutcome
        from immich_memories.automation.state_store import AutomationStateStore
        from immich_memories.tracking.models import RunMetadata
        from immich_memories.tracking.run_database import RunDatabase

        config = _config(tmp_path)
        RunDatabase(db_path=config.cache.database_path).save_run(
            RunMetadata(
                run_id="20260824_120000_abcd",
                created_at=datetime.now(tz=UTC),
                completed_at=datetime.now(tz=UTC),
                status="completed",
                source="auto",
                output_duration_seconds=182.5,
            )
        )
        store = AutomationStateStore(config.cache.database_path)
        attempt = store.start_attempt(reason="http trigger")
        store.finish_attempt(
            attempt.id,
            AutoOutcome.COMPLETED,
            "generation completed",
            run_id="20260824_120000_abcd",
        )

        body = self._get(client, config, attempt.id).json()

        assert body["state"] == "completed"
        assert body["run"]["run_id"] == "20260824_120000_abcd"
        assert body["run"]["status"] == "completed"
        assert body["run"]["output_duration_seconds"] == 182.5

    def test_status_needs_the_same_token_the_post_did(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        with _serving(config):
            response = client.get("/api/trigger/anything")

        assert response.status_code == 401


class TestTheAuthMiddlewareKnowsAboutHeadlessCallers:
    @pytest.mark.asyncio
    async def test_a_valid_token_is_not_sent_to_the_login_page(self, tmp_path: Path) -> None:
        """A workflow has no session cookie; reading NiceGUI's user storage for it fails."""
        from unittest.mock import AsyncMock, MagicMock

        from starlette.datastructures import Headers

        from immich_memories.ui.app import _auth_middleware

        config = _config(tmp_path)
        config.auth.enabled = True
        config.auth.username = "operator"
        config.auth.password = "operator-password"  # noqa: S105
        request = MagicMock()
        request.url.path = "/api/trigger"
        request.headers = Headers({"x-api-key": TOKEN})
        response = MagicMock(name="trigger_response")
        call_next = AsyncMock(return_value=response)

        # WHY: replaces the on-disk config the middleware loads for every request.
        with patch("immich_memories.ui.app.get_config", return_value=config):
            actual = await _auth_middleware(request, call_next)

        assert actual is response

    def test_an_api_caller_without_credentials_gets_a_status_it_can_act_on(self) -> None:
        from immich_memories.ui.app import _unauthenticated_response

        assert _unauthenticated_response("/api/trigger").status_code == 401

    def test_a_browser_still_goes_to_the_login_page(self) -> None:
        from immich_memories.ui.app import _unauthenticated_response

        response = _unauthenticated_response("/step2")

        assert response.status_code == 307
        assert response.headers["location"] == "/login"

    def test_the_server_users_actually_run_serves_the_trigger_route(self, tmp_path: Path) -> None:
        """A disabled trigger says so; a route that was never registered would not."""
        from immich_memories.ui.app import app as server

        config = _config(tmp_path, trigger_token="")
        # WHY: the same on-disk config boundary, reached by middleware and route alike.
        with (
            patch("immich_memories.ui.app.get_config", return_value=config),
            patch("immich_memories.ui.trigger_api.get_config", return_value=config),
        ):
            response = TestClient(server, raise_server_exceptions=False).post("/api/trigger")

        assert response.status_code == 404
        assert response.json()["detail"] == "trigger API is not enabled"
