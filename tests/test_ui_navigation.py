"""The public page builders keep task links and the current location consistent."""

import pytest
from nicegui import Client, app, ui

from immich_memories.ui import app as memory_app
from immich_memories.ui.state import remove_session


@pytest.mark.parametrize(
    ("path", "build", "active"),
    [
        ("/", memory_app.index_page, "/"),
        ("/step2", memory_app.step2_page, "/step2"),
        ("/step3", memory_app.step3_page, None),
        ("/step4", memory_app.step4_page, None),
        ("/settings/config", memory_app.config_page, "/settings/config"),
        ("/settings/cache", memory_app.cache_page, "/settings/config"),
        ("/runs", memory_app.runs_page, "/runs"),
        ("/suggestions", memory_app.suggestions_page, "/suggestions"),
        ("/settings/people", memory_app.people_page, "/settings/config"),
    ],
)
def test_page_navigation_preserves_destinations_and_current_location(
    monkeypatch, path, build, active
):
    # WHY: the HTTP session is the external boundary; all page elements and builders are real.
    session = {}
    monkeypatch.setattr(type(app.storage), "user", property(lambda _storage: session))
    client = Client(ui.page(path))
    try:
        with client:
            build()
        links = [element for element in client.elements.values() if isinstance(element, ui.link)]
        main = [link for link in links if "im-nav-item" in link.classes]
        assert [link.props["href"] for link in main] == [
            "/",
            "/suggestions",
            "/runs",
            "/step2",
            "/settings/config",
        ]
        assert [link.props["href"] for link in main if "im-nav-active" in link.classes] == (
            [active] if active else []
        )
        if path.startswith("/settings/"):
            assert {"/settings/config", "/settings/people", "/settings/cache"} <= {
                link.props["href"] for link in links
            }
    finally:
        client.delete()
        if "session_id" in session:
            remove_session(session["session_id"])


def test_a_run_row_with_a_hand_typed_attempt_id_still_opens(monkeypatch, tmp_path):
    """`generate --automation-attempt-id` takes any string; the page must survive one."""
    from datetime import datetime

    from immich_memories.config_loader import Config, get_config, set_config
    from immich_memories.tracking import RunDatabase
    from immich_memories.tracking.models import RunMetadata
    from immich_memories.ui.pages.runs import render_runs

    config = Config(
        cache={"database": str(tmp_path / "runs.db"), "directory": str(tmp_path / "cache")}
    )
    record = RunMetadata(
        run_id="hand-typed",
        created_at=datetime(2026, 7, 2, 9, 0),
        status="failed",
        source="auto",
    )
    record.automation_attempt_id = "last-nights-run"
    RunDatabase(config.cache.database_path).save_run(record)
    previous = get_config()
    set_config(config)
    # WHY: the HTTP session is the external boundary; the database and page are real.
    monkeypatch.setattr(type(app.storage), "user", property(lambda _storage: {}))
    client = Client(ui.page("/runs"))
    try:
        with client:
            render_runs(run_id="hand-typed")
        texts = [
            element.text for element in client.elements.values() if isinstance(element, ui.label)
        ]
        assert any("No child output" in text for text in texts)
    finally:
        client.delete()
        set_config(previous)
