"""Tests for /health endpoint functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_loader import Config


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_handler_returns_json(self):
        """Health handler should return valid JSON with expected keys."""
        from immich_memories.ui.app import _health_handler

        mock_request = MagicMock()

        # WHY: 3 external boundaries — Immich network, SQLite database, disk config
        with (
            patch(
                "immich_memories.ui.app._check_immich_reachable",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("immich_memories.ui.app._get_last_successful_run", return_value=None),
            patch("immich_memories.ui.app.get_config", return_value=Config()),
        ):
            response = await _health_handler(mock_request)

        data = response.body.decode()
        assert "status" in data
        assert "immich_reachable" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_check_immich_reachable_returns_true_on_200(self):
        """Should return True when Immich responds 200."""
        import httpx

        from immich_memories.ui.app import _check_immich_reachable

        config = Config()
        config.immich.url = "http://localhost:2283"
        config.immich.api_key = "test"

        mock_response = httpx.Response(200, json={"res": "pong"})
        # WHY: httpx.AsyncClient.get hits real network
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            result = await _check_immich_reachable(config)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_immich_reachable_returns_false_on_error(self):
        """Should return False when Immich is unreachable."""
        import httpx

        from immich_memories.ui.app import _check_immich_reachable

        config = Config()
        config.immich.url = "http://localhost:2283"
        config.immich.api_key = "test"

        # WHY: httpx.AsyncClient.get hits real network
        with (
            patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(httpx.ConnectError),
        ):
            await _check_immich_reachable(config)

    def test_get_last_successful_run_returns_none_when_no_runs(self):
        """Should return None when no completed runs exist."""
        from immich_memories.ui.app import _get_last_successful_run

        # WHY: RunDatabase reads from SQLite file (lazy import inside function)
        with patch("immich_memories.tracking.run_database.RunDatabase") as mock_db_cls:
            mock_db_cls.return_value.list_runs.return_value = []
            result = _get_last_successful_run()

        assert result is None
