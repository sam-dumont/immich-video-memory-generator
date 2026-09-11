"""A shared capture group remains one physical slot across story and recovery paths."""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import make_source
from tests.test_editorial_story_recovery_context import RecoveryJudge


@pytest.mark.parametrize("weak", [False, True])
def test_fragmented_capture_group_cannot_be_added_again_by_another_story(tmp_path, weak):
    # More than one reading page splits this one capture group across story accounts.
    # All physical frames still lie within the existing five-minute spacing window.
    source = make_source(tmp_path, occasions=1, pictures=26)
    start = source.assets["o0-p0"].file_created_at
    assets = {
        key: value.model_copy(
            update={
                "file_created_at": start + timedelta(seconds=index * 6),
                "is_favorite": True,
            }
        )
        for index, (key, value) in enumerate(source.assets.items())
    }
    plan = run(replace(source, assets=assets), RecoveryJudge(weak=weak))
    assert len(plan["carriers"]) == 1
    assert plan["carriers"][0]["favourite"]
