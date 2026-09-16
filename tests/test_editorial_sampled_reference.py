"""One conserved relation per source pair, carrying its nomination's own distance."""

from immich_memories.analysis.editorial_sampled_reference import sampled_source_relation
from immich_memories.analysis.selection_same_picture import SamePicturePairDecision

AVAILABLE = {"a": {"status": "available"}, "b": {"status": "available"}}


def recording_port(asked):
    # WHY: the real port is the image gateway; each call is a pair of vision arrangements.
    def port(pairs, _records, corroborating_distances=None):
        asked.append((pairs, corroborating_distances))
        ((earlier, later),) = pairs
        return (SamePicturePairDecision(earlier, later, True),), {"scope": "controlled pixels"}

    return port


def test_a_hash_nominated_pair_hands_its_distance_to_the_port():
    asked = []
    confirm = sampled_source_relation(recording_port(asked), picture_records=AVAILABLE)
    outcome = confirm("b", "a", 4)
    assert outcome["same"] is True and outcome["corroborating_distance"] == 4
    assert asked == [((("a", "b"),), (4,))]
    assert confirm("a", "b", 4) == outcome and len(asked) == 1  # conserved, not re-asked


def test_a_description_only_pair_is_confirmed_without_a_distance():
    asked = []
    confirm = sampled_source_relation(recording_port(asked), picture_records=AVAILABLE)
    outcome = confirm("b", "a")
    assert outcome["same"] is True and outcome["corroborating_distance"] is None
    assert asked == [((("a", "b"),), (None,))]
