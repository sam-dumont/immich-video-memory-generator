"""The browser previews music chosen from the saved cut's text."""

import json
import os
import re
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_preview_uses_text_mood_and_displays_a_playable_track(page, tmp_path, unused_tcp_port):
    url = f"http://127.0.0.1:{unused_tcp_port}"
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTEST_CURRENT_TEST" and not key.startswith("NICEGUI_")
    }
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.e2e.music_preview_app",
                str(tmp_path),
                str(unused_tcp_port),
            ],
            stdout=log,
            stderr=log,
            env=env,
        )
        try:
            for _ in range(100):
                try:
                    if httpx.get(url, timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None, (tmp_path / "server.log").read_text()
                time.sleep(0.2)
            page.goto(url)
            page.get_by_role("button", name="Generate Music").click()
            expect(page.get_by_text("Music generated!", exact=True)).to_be_visible(timeout=30_000)
            expect(page.get_by_text(re.compile(r"^Mood: .*playful"))).to_be_visible()
            expect(page.locator("audio")).to_be_visible()
            request = json.loads((tmp_path / "model-request.json").read_text())
            assert "images" not in request
            assert "carousel" in request["prompt"]
            scenes = json.loads((tmp_path / "music-request.json").read_text())
            assert "playful" in scenes[0]["mood"]
            page.screenshot(path=str(tmp_path / "music-preview.png"))
        finally:
            process.terminate()
            process.wait(timeout=15)
