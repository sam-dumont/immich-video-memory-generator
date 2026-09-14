"""Keep automation discovery and its real CLI child inside the fixture library."""

import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from immich_memories.automation.models import ProcessResult

# A test drops this file to make the next child fail the way a real generation
# fails: its run record already opened, then a non-zero exit.
FAIL_MARKER = "fail-next-child"


class FixtureDate(date):
    @classmethod
    def today(cls):
        return cls(2024, 7, 1)


def _child_that_opened_its_run_then_failed(command: list[str]) -> ProcessResult:
    from immich_memories.config import get_config
    from immich_memories.tracking import RunDatabase
    from immich_memories.tracking.models import RunMetadata

    values = dict(arg.split("=", 1) for arg in command if arg.startswith("--") and "=" in arg)
    RunDatabase(get_config().cache.database_path).save_run(
        RunMetadata(
            run_id="fixture-failed-child",
            created_at=datetime.now(),
            status="failed",
            source="auto",
            memory_key=values.get("--memory-key"),
            automation_attempt_id=values["--automation-attempt-id"],
            warnings=["The fixture provider refused the render"],
        )
    )
    return ProcessResult(1, "starting the fixture render\n", "the fixture provider refused\n")


def install_fake_automation(config_path: Path, state_dir: Path) -> None:
    """Use a fixed calendar and fixture geocoder; only the editorial model is scripted."""
    from immich_memories.analysis import trip_detection
    from immich_memories.automation import candidate_discovery, runner
    from tests.e2e.test_demo_assets import _TRIP_CLI_BOOTSTRAP

    candidate_discovery.date = FixtureDate
    # WHY: naming the fixture's lake must not contact the public Nominatim service.
    trip_detection.reverse_geocode = lambda *_args, **_kwargs: "Annecy, France"

    def execute(command):
        marker = state_dir / FAIL_MARKER
        if marker.is_file():
            marker.unlink()
            return _child_that_opened_its_run_then_failed(command)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _TRIP_CLI_BOOTSTRAP,
                str(config_path),
                str(state_dir),
                *command[1:],
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return ProcessResult(result.returncode, result.stdout, result.stderr)

    # WHY: keep the real generate command and rendering, while installing the fixture editor in its child.
    runner._execute_generate = execute
