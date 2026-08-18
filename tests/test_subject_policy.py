"""Selection should be about people, not lawnmowers."""

from __future__ import annotations

from immich_memories.analysis.subject_policy import SubjectCategory, classify_subject


def test_an_immich_face_tag_settles_it() -> None:
    """Immich's own face tagging is authoritative and needs no LLM round trip."""
    assert (
        classify_subject(tagged_people=1, subjects=None, description="a red lawnmower on grass")
        is SubjectCategory.PEOPLE
    )


def test_an_untagged_clip_is_people_when_the_description_says_so() -> None:
    """Immich only tags faces it recognises; a stranger or a back of a head is
    still a person. These are verbatim descriptions from a real cache."""
    for text in (
        "Three children are playing with sand at the beach.",
        "A man and a baby are interacting playfully in a cozy indoor setting.",
        "A woman and a child are seated in the back of a bicycle-like vehicle.",
    ):
        assert (
            classify_subject(tagged_people=0, subjects=None, description=text)
            is SubjectCategory.PEOPLE
        ), text


def test_animals_landscapes_and_objects_are_told_apart() -> None:
    cases = {
        "A dog is running across the garden": SubjectCategory.ANIMAL,
        "Two cats sit on a windowsill": SubjectCategory.ANIMAL,
        "Sunset over the sea with mountains in the distance": SubjectCategory.LANDSCAPE,
        "A view of the valley from the top of the hill": SubjectCategory.LANDSCAPE,
        "A red lawnmower parked on the lawn": SubjectCategory.OBJECT,
        "A bicycle mounted on an indoor trainer": SubjectCategory.OBJECT,
        "A shelf holding plastic storage boxes": SubjectCategory.OBJECT,
    }
    for text, expected in cases.items():
        assert classify_subject(tagged_people=0, subjects=None, description=text) is expected, text


def test_people_outrank_whatever_else_is_in_frame() -> None:
    """A child playing with the dog is a memory about the child."""
    assert (
        classify_subject(
            tagged_people=0,
            subjects=None,
            description="A dog runs along the beach while two children chase it",
        )
        is SubjectCategory.PEOPLE
    )


def _cand(key, category, score):
    from immich_memories.analysis.subject_policy import SubjectCandidate

    return SubjectCandidate(key=key, category=category, score=score)


def test_a_high_scoring_object_still_loses_to_a_lower_scoring_person() -> None:
    """The lawnmower outscored people on motion and stability. Score is not
    the question -- an object is not a memory."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("lawnmower", SubjectCategory.OBJECT, 0.91),
            _cand("kid", SubjectCategory.PEOPLE, 0.42),
        ],
        max_animal=2,
        max_object=0,
    )

    assert outcome.kept_keys == ["kid"]
    assert outcome.dropped[SubjectCategory.OBJECT] == 1


def test_animals_are_rationed_to_the_best_few() -> None:
    """Once in a while is fine; a video of the neighbour's dog is not."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("person", SubjectCategory.PEOPLE, 0.5),
            _cand("dog-best", SubjectCategory.ANIMAL, 0.9),
            _cand("dog-mid", SubjectCategory.ANIMAL, 0.7),
            _cand("dog-worst", SubjectCategory.ANIMAL, 0.3),
        ],
        max_animal=2,
        max_object=0,
    )

    assert outcome.kept_keys == ["person", "dog-best", "dog-mid"]


def test_scenery_has_to_beat_the_median_person_clip() -> None:
    """'Super nice landscapes' means measurably better than an average
    people clip, not merely present."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("p1", SubjectCategory.PEOPLE, 0.4),
            _cand("p2", SubjectCategory.PEOPLE, 0.6),
            _cand("stunning-view", SubjectCategory.LANDSCAPE, 0.8),
            _cand("dull-view", SubjectCategory.LANDSCAPE, 0.2),
        ],
        max_animal=2,
        max_object=0,
    )

    assert outcome.kept_keys == ["p1", "p2", "stunning-view"]


def test_a_pool_with_no_people_still_produces_a_video() -> None:
    """A scenery holiday or an all-object month must not return nothing."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("thing-a", SubjectCategory.OBJECT, 0.7),
            _cand("thing-b", SubjectCategory.OBJECT, 0.3),
        ],
        max_animal=2,
        max_object=0,
    )

    assert outcome.kept_keys, "policy emptied the pool"


def test_a_clip_we_know_nothing_about_is_kept() -> None:
    """35-46% of a real pool has no cached description. Absence of evidence is
    not evidence of a lawnmower -- only classified clips get rationed."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("p1", SubjectCategory.PEOPLE, 0.8),
            _cand("p2", SubjectCategory.PEOPLE, 0.8),
            _cand("unanalysed", SubjectCategory.UNKNOWN, 0.1),
        ],
        max_animal=2,
        max_object=0,
    )

    assert "unanalysed" in outcome.kept_keys


def test_a_close_up_of_an_object_is_not_scenery() -> None:
    """'view' appears in 'a close-up view of a treadmill'. Real misclassifications
    from a live library: a bike hub, a treadmill, an office renovation."""
    for text in (
        "A close-up view of a modern treadmill against a wall",
        "This is a close-up photograph of the rear wheel hub and disc brake assembly",
        "A series of images showing an office renovation",
    ):
        assert (
            classify_subject(tagged_people=0, subjects=None, description=text)
            is not SubjectCategory.LANDSCAPE
        ), text


def test_an_explicit_category_from_the_model_is_used_directly() -> None:
    """Asking the VLM to pick from a closed set beats keyword-matching its prose."""
    assert (
        classify_subject(
            tagged_people=0,
            category="object",
            subjects=None,
            description="A close-up of a string trimmer lying on concrete blocks",
        )
        is SubjectCategory.OBJECT
    )


def test_face_tags_still_beat_an_explicit_category() -> None:
    """Immich recognised a face; the VLM saying 'object' does not overrule that."""
    assert (
        classify_subject(tagged_people=2, category="object", subjects=None, description=None)
        is SubjectCategory.PEOPLE
    )


def test_an_unrecognised_category_falls_back_to_the_description() -> None:
    """Models return junk. A bad label must not silently become OBJECT."""
    assert (
        classify_subject(
            tagged_people=0,
            category="Category.PEOPLE!!",
            subjects=None,
            description="Two children are digging in the sand",
        )
        is SubjectCategory.PEOPLE
    )
