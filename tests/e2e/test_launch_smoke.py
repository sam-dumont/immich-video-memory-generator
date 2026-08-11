"""Required hermetic browser smoke for the launch-critical generation path."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Page, expect

from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    HdrTransfer,
    OutputCodec,
)
from immich_memories.processing.output_contract import validate_output
from immich_memories.tracking.run_database import RunDatabase

pytestmark = pytest.mark.e2e


def _choose(page: Page, label: str, option: str) -> None:
    """Choose one exact option from a NiceGUI/Quasar select."""
    select = page.get_by_role("combobox", name=label)
    select.click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(select).to_have_value(option)


def test_launch_flow_renders_real_video(
    page: Page,
    launch_app_url: str,
    launch_workspace,
) -> None:
    """The default v3 monthly flow must publish and record one real H.264 video."""
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)

    expect(page.get_by_text("Immich Connection — Fake Immich User", exact=True)).to_be_visible(
        timeout=30_000
    )
    readiness = page.request.get(f"{launch_app_url}/health/ready")
    assert readiness.status == 200
    assert readiness.json()["immich"] == {
        "status": "ready",
        "reachable": True,
        "api_version_policy": "auto",
        "resolved_api_version": "v3",
    }
    launch_config = yaml.safe_load(launch_workspace.config_path.read_text())
    assert launch_config["advanced"]["hardware"]["enabled"] is False
    assert launch_config["advanced"]["musicgen"]["enabled"] is False
    assert launch_config["advanced"]["ace_step"]["enabled"] is False

    page.get_by_text("Monthly Highlights", exact=True).click()
    _choose(page, "Month", "June")
    include_photos = page.locator(".q-toggle").filter(has_text="Include Photos")
    expect(include_photos).to_have_attribute("aria-checked", "true")
    page.get_by_role("button", name="Next: Review Clips").click()

    expect(page.get_by_text("2 Videos, 2 Photos Found", exact=True)).to_be_visible(timeout=60_000)
    for filename in ("video-1.mp4", "video-2.mp4", "photo-1.jpg", "photo-2.jpg"):
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()

    page.get_by_role("button", name="Generate Memories", exact=True).click()
    pipeline_summary = page.get_by_text(
        re.compile(r"^Pipeline complete! Selected [1-9]\d* clips from [1-9]\d* analyzed\.$")
    )
    expect(pipeline_summary).to_be_visible(timeout=180_000)
    page.get_by_role("button", name="Review & Refine Selected Clips").click()
    expect(page.get_by_text(re.compile(r"^Final Duration: 0:0[1-9]$"))).to_be_visible()
    page.get_by_role("button", name="Continue to Generation").click()

    _choose(page, "Resolution", "720p")
    _choose(page, "Output Format", "MP4 (H.264)")
    _choose(page, "Background music", "None")
    page.get_by_role("button", name="Next: Preview & Export").click()
    page.get_by_role("button", name="Generate Video").click()

    expect(page.get_by_text("Your memory video is ready!", exact=True)).to_be_visible(
        timeout=600_000
    )

    outputs = sorted(launch_workspace.output_dir.rglob("*.mp4"))
    assert len(outputs) == 1
    output_path = outputs[0]
    plan = EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=(),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )
    probe = validate_output(output_path, plan)
    assert probe.codec == "h264"
    assert (probe.width, probe.height) == (1280, 720)
    assert probe.duration_seconds > 0
    assert probe.size_bytes > 0

    database = RunDatabase(launch_workspace.database_path)
    completed = database.list_runs(status="completed", source="manual")
    assert len(completed) == 1
    assert Path(completed[0].output_path or "") == output_path
    assert database.list_runs(status="running") == []
