"""The export page's recovery poll reloads once and then stops, instead of ticking forever."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.ui.pages.step4_export import _poll_recovered_run


class _Timer:
    def __init__(self) -> None:
        self.active = True

    def deactivate(self) -> None:
        self.active = False


def _poll(status: str | None) -> tuple[bool, _Timer, int]:
    timer = _Timer()
    recovered = None if status is None else SimpleNamespace(status=status)
    # WHY: the run table and the browser are the two boundaries of this function;
    # the poll's own decision is what is under test.
    with (
        patch("immich_memories.ui.pages.step4_recovery.recover_active_run", return_value=recovered),
        patch("immich_memories.ui.pages.step4_export.ui.navigate.reload") as reload,
    ):
        stopped = _poll_recovered_run(state=object(), timer=timer)
    return stopped, timer, reload.call_count


def test_a_run_still_running_keeps_the_poll_alive_without_reloading() -> None:
    stopped, timer, reloads = _poll("running")
    assert not stopped
    assert timer.active
    assert reloads == 0


def test_a_finished_run_reloads_the_page_once_and_stops_the_timer() -> None:
    stopped, timer, reloads = _poll("completed")
    assert stopped
    assert not timer.active
    assert reloads == 1


def test_a_run_that_vanished_stops_the_timer_too() -> None:
    stopped, timer, reloads = _poll(None)
    assert stopped
    assert not timer.active
    assert reloads == 1
