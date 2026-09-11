"""Story questions and selections must not change with Python's hash seed."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import run, semantic_plan
from tests.test_editorial_story_first_planner import make_source


def _snapshot(directory: Path, *, majority: bool) -> dict:
    source = make_source(directory, occasions=6 if majority else 4, pictures=2)
    text = source.wall_bytes.decode().replace(
        "@table\tplaces\t0\tid\tname",
        "@table\tplaces\t2\tid\tname\n"
        '"L01"\t"Schaerbeek, Brussels Capital, Belgium"\n'
        '"L02"\t"Jette, Brussels Capital, Belgium"',
    )
    # The judge places odd and even capture groups in separate episodes. Each
    # episode sees L01 first; its later L02 has an equal count, or a majority.
    places = ["L01", "L01", "L02", "L02"] + (["L02", "L02"] if majority else [])
    rows = "\n".join(f'"M{index:03d}"\t"{place}"' for index, place in enumerate(places, 1))
    text = text.replace(
        "@table\tmoment_places\t0\tmoment\tplace",
        f"@table\tmoment_places\t{len(places)}\tmoment\tplace\n{rows}",
    )
    judge = ControlledStoryJudge()
    plan = run(replace(source, wall_bytes=text.encode()), judge)
    reading = json.loads(
        (source.artifact_dir / "derived-decisions/period-story.private.json").read_text()
    )
    return {
        "plan": semantic_plan(plan),
        "requests": [(call["stage"], call["prompt"]) for call in judge.calls],
        "places": {key: row.get("place") for key, row in reading["audit"]["hints"].items()},
    }


@pytest.mark.parametrize("majority", [False, True], ids=["equal-count-first-source", "majority"])
def test_story_questions_and_selection_keep_source_order_across_hash_seeds(tmp_path, majority):
    snapshots = []
    script = (
        "import json,runpy,sys; from pathlib import Path; "
        "fixture=runpy.run_path(sys.argv[1]); "
        "print(json.dumps(fixture['_snapshot'](Path(sys.argv[2]), majority=bool(int(sys.argv[3])))))"
    )
    # These seeds choose different winners from the former unordered set.
    for seed in (0, 2):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(Path(__file__).resolve()),
                str(tmp_path / str(seed)),
                str(int(majority)),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
        )
        snapshots.append(json.loads(completed.stdout))
    assert snapshots[0] == snapshots[1]
    expected = (
        "L02:Jette, Brussels Capital, Belgium"
        if majority
        else "L01:Schaerbeek, Brussels Capital, Belgium"
    )
    assert snapshots[0]["places"] == {"S0001": expected, "S0002": expected}
    assert snapshots[0]["plan"]["carriers"]
