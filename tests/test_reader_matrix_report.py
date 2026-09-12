"""Cross-reader comparisons count known occasion alternatives, not only exact pictures."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_alternative_from_reference_inventory_retains_occasion_without_asset_agreement(tmp_path):
    from reader_matrix_report import compare_attempts

    reference, current = tmp_path / "reference", tmp_path / "current"
    reference.mkdir()
    current.mkdir()
    audit = reference / "derived-decisions"
    audit.mkdir()
    (reference / "plan.private.json").write_text(
        json.dumps(
            {
                "carriers": [{"asset_id": "original", "favourite": True, "story_episode": "story"}],
                "content_seconds": 30,
                "story": {
                    "episodes": [
                        {
                            "episode": "story",
                            "title": "An occasion",
                            "weight": "major",
                            "granted": 1,
                            "day_episodes": ["day-1"],
                        }
                    ]
                },
                "shareability": {"verdicts": {"refused": {"verdict": "do_not_show"}}},
            }
        )
    )
    (audit / "moment-inventory-day-1.private.json").write_text(
        json.dumps(
            {
                "source_units": 2,
                "pages": [{"offered": ["original", "alternative"]}],
            }
        )
    )
    (current / "plan.private.json").write_text(
        json.dumps(
            {
                "carriers": [{"asset_id": "alternative"}, {"asset_id": "refused"}],
                "content_seconds": 30,
            }
        )
    )

    result = compare_attempts(reference, current)

    assert result["shared_assets"] == 0
    assert result["known_reference_occasions_retained"] == 1
    assert result["funded_reference_occasions"] == 1
    assert result["favourites_kept"] == 0
    assert result["reference_refused_assets_selected"] == ["refused"]
    assert result["occasion_mapping_complete"] is True
