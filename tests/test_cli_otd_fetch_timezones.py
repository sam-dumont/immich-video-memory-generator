"""A fetched OTD asset must survive the ordinary per-window CLI report."""

from datetime import date, datetime
from types import SimpleNamespace

from immich_memories.cli._asset_fetch import fetch_videos
from immich_memories.cli._helpers import set_active_display
from immich_memories.memory_types.date_builders import build_on_this_day


def test_twenty_year_fetch_reports_mixed_timezone_assets_without_changing_windows():
    windows = build_on_this_day(date(2026, 9, 9), years_back=20)
    queried = []
    assets = [
        SimpleNamespace(
            id="recent", file_created_at=datetime.fromisoformat("2025-09-09T12:00:00Z")
        ),
        SimpleNamespace(id="older", file_created_at=datetime(2024, 9, 9, 12)),
        SimpleNamespace(
            id="edge", file_created_at=datetime.fromisoformat("2023-09-11T01:59:59+02:00")
        ),
    ]

    class Library:
        def get_videos_for_date_range(self, window):
            queried.append(window)
            return [asset for asset in assets if window.contains(asset.file_created_at)]

    class Display:
        def __init__(self):
            self.lines = []

        def print_message(self, message):
            self.lines.append(message)

        def add_task(self, _description, **_fields):
            return 0

        def update(self, _task_id, **_fields):
            pass

    display = Display()
    set_active_display(display)
    try:
        result = fetch_videos(
            client=Library(), progress=display, date_ranges=windows, person_ids=[]
        )
    finally:
        set_active_display(None)

    assert result == assets
    assert queried == windows
    assert len(queried) == 20
    assert all(window.start.tzinfo is None for window in queried)
    report = "\n".join(display.lines)
    for year in (2025, 2024, 2023):
        assert f"{year}-09-08..{year}-09-10: 1" in report
    assert "2006-09-08..2006-09-10: 0" in report
