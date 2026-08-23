"""Required hermetic browser smoke for the launch-critical generation path."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Page, expect

from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    HdrTransfer,
    OutputCodec,
)
from immich_memories.processing.output_contract import (
    InvalidOutputArtifact,
    OutputProbe,
    validate_output,
)
from immich_memories.tracking.run_database import RunDatabase
from tests.e2e.conftest import _build_launch_environment

pytestmark = pytest.mark.e2e


def test_launch_environment_strips_all_production_shortcuts(monkeypatch) -> None:
    """Personal provider settings must never escape into the fake-service app."""
    shortcut_names = {
        "IMMICH_URL",
        "IMMICH_API_KEY",
        "OPENAI_API_KEY",
        "MUSICGEN_ENABLED",
        "MUSICGEN_BASE_URL",
        "MUSICGEN_API_KEY",
        "ACE_STEP_ENABLED",
        "ACE_STEP_MODE",
        "ACE_STEP_API_URL",
    }
    for name in shortcut_names:
        monkeypatch.setenv(name, "https://personal.example.invalid/secret")

    environment = _build_launch_environment()

    assert shortcut_names.isdisjoint(environment)


def test_failed_output_validation_records_sanitized_probe_without_removing_video(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Failed validation must leave CI a safe probe and the original video."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"rendered-video")
    diagnostic_path = tmp_path / "output-probe.json"

    def fail_validation(path: Path, plan: EncodingPlan) -> None:
        raise InvalidOutputArtifact(f"invalid output at {path}")

    monkeypatch.setattr(
        "tests.e2e.test_launch_smoke.validate_output",
        fail_validation,
    )

    with pytest.raises(InvalidOutputArtifact, match="invalid output"):
        _validate_and_record_output(  # type: ignore[name-defined,arg-type]
            output_path,
            None,
            diagnostic_path,
        )

    assert output_path.read_bytes() == b"rendered-video"
    assert diagnostic_path.exists()
    assert json.loads(diagnostic_path.read_text()) == {
        "output": {
            "exists": True,
            "size_bytes": len(b"rendered-video"),
        },
        "validation": {
            "error_type": "InvalidOutputArtifact",
            "status": "failed",
        },
    }


def test_successful_output_validation_records_sanitized_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Successful validation must leave CI the normalized output evidence."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"rendered-video")
    diagnostic_path = tmp_path / "output-probe.json"
    probe = OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=1.25,
        size_bytes=len(b"rendered-video"),
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1280,
        height=720,
        decoded_frames=30,
    )

    monkeypatch.setattr(
        "tests.e2e.test_launch_smoke.validate_output",
        lambda _path, _plan: probe,
    )

    assert _validate_and_record_output(output_path, None, diagnostic_path) is probe  # type: ignore[arg-type]
    assert diagnostic_path.exists()
    assert json.loads(diagnostic_path.read_text()) == {
        "output": {
            "exists": True,
            "size_bytes": len(b"rendered-video"),
        },
        "probe": asdict(probe),
        "validation": {"status": "passed"},
    }


def _output_diagnostic(path: Path) -> dict[str, bool | int | None]:
    """Return only non-sensitive facts about the generated output."""
    exists = path.is_file()
    return {
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
    }


def _write_output_diagnostic(path: Path, diagnostic: dict[str, object]) -> None:
    """Persist deterministic, non-sensitive E2E artifact metadata."""
    path.write_text(json.dumps(diagnostic, indent=2, sort_keys=True))


def _validate_and_record_output(
    output_path: Path,
    plan: EncodingPlan,
    diagnostic_path: Path,
) -> OutputProbe:
    """Validate one E2E artifact while always preserving failure diagnostics."""
    try:
        probe = validate_output(output_path, plan)
    except Exception as exc:
        _write_output_diagnostic(
            diagnostic_path,
            {
                "output": _output_diagnostic(output_path),
                "validation": {
                    "error_type": type(exc).__name__,
                    "status": "failed",
                },
            },
        )
        raise
    _write_output_diagnostic(
        diagnostic_path,
        {
            "output": _output_diagnostic(output_path),
            "probe": asdict(probe),
            "validation": {"status": "passed"},
        },
    )
    return probe


def _choose(page: Page, label: str, option: str) -> None:
    """Choose one exact option from a NiceGUI/Quasar select."""
    select = page.get_by_role("combobox", name=label)
    select.click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(select).to_have_value(option)


def _drive_to_step4(page: Page, launch_app_url: str) -> None:
    """Walk the default v3 monthly flow up to the Step 4 'Generate Video' button."""
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_text("Immich Connection — Fake Immich User", exact=True)).to_be_visible(
        timeout=30_000
    )
    page.get_by_text("Monthly Highlights", exact=True).click()
    _choose(page, "Month", "June")
    include_photos = page.locator(".q-toggle").filter(has_text="Include Photos")
    expect(include_photos).to_have_attribute("aria-checked", "true")
    page.get_by_role("button", name="Next: Review Clips").click()

    expect(page.get_by_text("3 Videos, 3 Photos Found", exact=True)).to_be_visible(timeout=60_000)
    for filename in (
        "video-1.mp4",
        "video-2.mp4",
        "video-3.mp4",
        "photo-1.jpg",
        "photo-2.jpg",
        "photo-3.jpg",
    ):
        expect(page.get_by_text(filename, exact=True).first).to_be_visible()

    page.get_by_role("button", name="Generate Memories", exact=True).click()
    pipeline_summary = page.get_by_text(
        re.compile(
            r"^Pipeline complete! Planned [1-9]\d* clips from "
            r"[1-9]\d* eligible media items\.$"
        )
    )
    expect(pipeline_summary).to_be_visible(timeout=180_000)
    page.get_by_role("button", name="Review & Refine Selected Clips").click()
    expect(page.get_by_text(re.compile(r"^Final Duration: (?!0:00)\d+:[0-5]\d$"))).to_be_visible()
    page.get_by_role("button", name="Continue to Generation").click()

    _choose(page, "Resolution", "720p")
    _choose(page, "Output Format", "MP4 (H.264)")
    _choose(page, "Background music", "None")
    page.get_by_role("button", name="Next: Preview & Export").click()


def test_launch_flow_renders_real_video(
    page: Page,
    launch_app_url: str,
    launch_workspace,
) -> None:
    """The default v3 monthly flow must publish and record one real H.264 video."""
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
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

    _drive_to_step4(page, launch_app_url)
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
    probe = _validate_and_record_output(
        output_path,
        plan,
        launch_workspace.root / "output-probe.json",
    )
    assert probe.codec == "h264"
    assert (probe.width, probe.height) == (1280, 720)
    assert probe.duration_seconds > 0
    assert probe.size_bytes > 0

    database = RunDatabase(launch_workspace.database_path)
    completed = database.list_runs(status="completed", source="manual")
    assert len(completed) == 1
    assert Path(completed[0].output_path or "") == output_path
    assert database.list_runs(status="running") == []


def test_reload_during_generation_recovers_the_finished_video(
    page: Page,
    launch_app_url: str,
    launch_workspace,
) -> None:
    """A page reload mid-render must show the run is still going, then the result (#322)."""
    before = set(launch_workspace.output_dir.rglob("*.mp4"))
    _drive_to_step4(page, launch_app_url)
    page.get_by_role("button", name="Generate Video").click()
    expect(page.locator(".q-linear-progress").first).to_be_visible(timeout=30_000)

    # WHY: a reload is what a user does when the progress bar seems stuck; it also deletes
    # the NiceGUI client, so every later UI write from the running coroutine is dropped.
    page.reload(wait_until="domcontentloaded", timeout=30_000)

    expect(page.get_by_text(re.compile(r"is still running \(run "))).to_be_visible(timeout=30_000)
    expect(page.get_by_text(re.compile(r"^Saved to: "))).to_be_visible(timeout=600_000)

    outputs = set(launch_workspace.output_dir.rglob("*.mp4")) - before
    assert len(outputs) == 1
    completed = RunDatabase(launch_workspace.database_path).list_runs(
        status="completed", source="manual", order_by_completion=True
    )
    assert completed and Path(completed[0].output_path or "") == outputs.pop()
