"""A failed scheduled job has to say why it failed.

The daemon reported `result.stderr[-500:] if result.stderr else "no stderr"`,
and the child writes its logs to **stdout** -- `setup_logging` installs a
StreamHandler on stdout and `print_error` goes through Rich, also stdout. So the
one stream the daemon read was reliably empty, and every failure was recorded as
"no stderr" while the actual error sat in the stream it discarded.

The automation runner already fixed this for its own child; the daemon kept the
original bug.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.scheduling.daemon import describe_process_failure


def test_the_error_is_taken_from_stdout_when_stderr_is_empty():
    """The exact shape of a real failure: everything on stdout."""
    described = describe_process_failure(stdout="[ERROR] Immich API error: 404", stderr="")

    assert "Immich API error: 404" in described
    assert described != "no stderr"


def test_stderr_is_still_used_when_present():
    described = describe_process_failure(stdout="", stderr="Traceback: boom")

    assert "Traceback: boom" in described


def test_both_streams_are_reported_when_both_have_content():
    """Whichever one carries the cause, the operator gets it."""
    described = describe_process_failure(stdout="[ERROR] no candidates", stderr="warning: x")

    assert "no candidates" in described
    assert "warning: x" in described


def test_a_silent_failure_says_so_without_pretending():
    described = describe_process_failure(stdout="", stderr="")

    assert described
    assert "no output" in described.lower()


def test_the_tail_is_bounded():
    """A runaway child must not put megabytes into the notification."""
    described = describe_process_failure(stdout="x" * 10_000, stderr="")

    assert len(described) < 2_000


class TestTheChildsLogHasSomewhereToGo:
    """The daemon keeps only a 500-character tail. Without a log file the child's
    full log exists nowhere, so a failure that needs more context than the tail
    cannot be investigated at all.
    """

    def test_the_child_is_given_a_log_file(self, tmp_path, monkeypatch):
        import subprocess

        from immich_memories.scheduling import daemon

        captured = {}

        def fake_run(cmd, **kwargs):  # noqa: ARG001
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        # WHY: spawns a real generation; the point of the test is the env it gets.
        monkeypatch.setattr(daemon.subprocess, "run", fake_run)
        monkeypatch.setattr(daemon, "_notify_if_configured", lambda **_kwargs: None)
        monkeypatch.setattr(
            daemon, "resolve_schedule_params", lambda *_a: {"memory_type": "monthly_highlights"}
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

        job = MagicMock()
        job.schedule.name = "nightly"

        daemon.execute_job(job, timeout_seconds=60)

        env = captured["env"]
        assert env is not None, "the child inherited the daemon's environment unchanged"
        assert str(tmp_path) in env["IMMICH_MEMORIES_LOG_FILE"]
        assert env["IMMICH_MEMORIES_LOG_FILE"].endswith("generate-nightly.log")

    def test_a_schedule_name_cannot_escape_the_log_directory(self, tmp_path, monkeypatch):
        """Schedule names are user-supplied and end up in a path."""
        from immich_memories.scheduling.daemon import _child_log_path

        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

        path = _child_log_path("../../etc/evil")

        assert tmp_path in path.parents


class TestTheMachineReadableResult:
    """`auto run --json` is what a wrapper script reads. It reported
    `outcome: failed` with no field carrying the cause, so an automated consumer
    could see that something broke and never what.
    """

    @staticmethod
    def _payload(**kwargs):
        import json

        from immich_memories.automation.models import AutoOutcome, AutoRunResult
        from immich_memories.cli.auto_cmd import _auto_result_to_json

        return json.loads(
            _auto_result_to_json(
                AutoRunResult(outcome=AutoOutcome.FAILED, reason="generation failed", **kwargs),
                {"version": "0.0.0"},
            )
        )

    def test_a_failure_carries_its_error(self):
        payload = self._payload(error="Immich API error: 404")

        assert payload["error"] == "Immich API error: 404"

    def test_a_result_without_an_error_says_null_rather_than_omitting_it(self):
        """A consumer should not have to tell 'no error' from 'field missing'."""
        payload = self._payload()

        assert "error" in payload
        assert payload["error"] is None
