"""Tests for Apprise notification support."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.automation.notifications import (
    _build_body,
    _build_title,
    notify_job_complete,
    send_test_notification,
)


class TestNotifyJobComplete:
    def test_returns_false_with_no_urls(self) -> None:
        assert notify_job_complete(memory_type="monthly", status="completed", urls=None) is False
        assert notify_job_complete(memory_type="monthly", status="completed", urls=[]) is False

    def test_returns_false_if_apprise_not_installed(self) -> None:
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "apprise":
                raise ImportError("no apprise")
            return real_import(name, *args, **kwargs)

        # WHY: simulates environment where apprise package is not installed
        with patch("builtins.__import__", side_effect=mock_import):
            result = notify_job_complete(
                memory_type="monthly",
                status="completed",
                urls=["slack://token"],
            )

        assert result is False

    def test_sends_notification_on_success(self) -> None:
        mock_apprise = MagicMock()
        mock_instance = MagicMock()
        mock_instance.notify.return_value = True
        mock_apprise.Apprise.return_value = mock_instance

        # WHY: would send real notifications to external services
        with patch.dict("sys.modules", {"apprise": mock_apprise}):
            result = notify_job_complete(
                memory_type="monthly_highlights",
                status="completed",
                duration_seconds=120.0,
                output_path="/tmp/output.mp4",
                urls=["slack://token-a/token-b/token-c"],
            )

        assert result is True
        mock_instance.add.assert_called_once_with("slack://token-a/token-b/token-c")
        call_kwargs = mock_instance.notify.call_args
        assert "Memory Generated" in call_kwargs.kwargs["title"]

    def test_sends_notification_on_failure(self) -> None:
        mock_apprise = MagicMock()
        mock_instance = MagicMock()
        mock_instance.notify.return_value = True
        mock_apprise.Apprise.return_value = mock_instance

        # WHY: would send real notifications to external services
        with patch.dict("sys.modules", {"apprise": mock_apprise}):
            result = notify_job_complete(
                memory_type="year_in_review",
                status="failed",
                error="FFmpeg crashed",
                urls=["discord://webhook"],
            )

        assert result is True
        call_kwargs = mock_instance.notify.call_args
        assert "Generation Failed" in call_kwargs.kwargs["title"]
        assert "FFmpeg crashed" in call_kwargs.kwargs["body"]

    def test_returns_false_when_apprise_notify_fails(self) -> None:
        mock_apprise = MagicMock()
        mock_instance = MagicMock()
        mock_instance.notify.return_value = False
        mock_apprise.Apprise.return_value = mock_instance

        # WHY: would send real notifications to external services
        with patch.dict("sys.modules", {"apprise": mock_apprise}):
            result = notify_job_complete(
                memory_type="monthly",
                status="completed",
                urls=["bad://url"],
            )

        assert result is False

    def test_handles_notify_exception(self) -> None:
        mock_apprise = MagicMock()
        mock_instance = MagicMock()
        mock_instance.notify.side_effect = RuntimeError("connection refused")
        mock_apprise.Apprise.return_value = mock_instance

        # WHY: would send real notifications to external services
        with patch.dict("sys.modules", {"apprise": mock_apprise}):
            result = notify_job_complete(
                memory_type="monthly",
                status="completed",
                urls=["slack://token"],
            )

        assert result is False

    def test_adds_multiple_urls(self) -> None:
        mock_apprise = MagicMock()
        mock_instance = MagicMock()
        mock_instance.notify.return_value = True
        mock_apprise.Apprise.return_value = mock_instance

        urls = ["slack://token", "discord://webhook", "mailto://user:pass@gmail.com"]

        # WHY: would send real notifications to external services
        with patch.dict("sys.modules", {"apprise": mock_apprise}):
            notify_job_complete(
                memory_type="monthly",
                status="completed",
                urls=urls,
            )

        assert mock_instance.add.call_count == 3


class TestBuildTitle:
    def test_completed_title(self) -> None:
        assert _build_title("monthly_highlights", "completed") == (
            "Memory Generated: Monthly Highlights"
        )

    def test_failed_title(self) -> None:
        assert _build_title("year_in_review", "failed") == ("Generation Failed: Year In Review")

    def test_underscores_replaced_with_spaces(self) -> None:
        title = _build_title("person_spotlight", "completed")
        assert "Person Spotlight" in title


class TestBuildBody:
    def test_includes_all_fields_for_completed(self) -> None:
        body = _build_body(
            memory_type="monthly_highlights",
            status="completed",
            duration_seconds=125.5,
            output_path="/videos/memory.mp4",
            error=None,
        )
        assert "Type: monthly_highlights" in body
        assert "Processing time: 2m 05s" in body
        assert "Output: /videos/memory.mp4" in body
        assert "Error" not in body

    def test_includes_error_for_failed(self) -> None:
        body = _build_body(
            memory_type="monthly_highlights",
            status="failed",
            duration_seconds=10.0,
            output_path=None,
            error="FFmpeg exit code 1",
        )
        assert "Error: FFmpeg exit code 1" in body
        assert "Output" not in body

    def test_truncates_long_errors(self) -> None:
        long_error = "x" * 500
        body = _build_body(
            memory_type="monthly",
            status="failed",
            duration_seconds=0,
            output_path=None,
            error=long_error,
        )
        # Error is truncated to 200 chars
        error_line = [line for line in body.split("\n") if line.startswith("Error:")][0]
        assert len(error_line) < 210

    def test_omits_duration_when_zero(self) -> None:
        body = _build_body(
            memory_type="test",
            status="completed",
            duration_seconds=0,
            output_path=None,
            error=None,
        )
        assert "Processing time" not in body

    def test_omits_output_for_failed_status(self) -> None:
        body = _build_body(
            memory_type="test",
            status="failed",
            duration_seconds=0,
            output_path="/some/path.mp4",
            error="crash",
        )
        assert "Output" not in body


class TestSendTestNotification:
    def test_delegates_to_notify_job_complete(self) -> None:
        # WHY: would send real notifications to external services
        with patch(
            "immich_memories.automation.notifications.notify_job_complete",
            return_value=True,
        ) as mock_notify:
            result = send_test_notification(urls=["slack://token"])

        assert result is True
        mock_notify.assert_called_once_with(
            memory_type="test",
            status="completed",
            urls=["slack://token"],
        )
