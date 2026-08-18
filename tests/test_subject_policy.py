"""Selection should be about people, not lawnmowers."""

from __future__ import annotations

from immich_memories.analysis.subject_policy import SubjectCategory, classify_subject


def test_an_immich_face_tag_settles_it() -> None:
    """Immich's own face tagging is authoritative and needs no LLM round trip."""
    assert (
        classify_subject(tagged_people=1, description="a red lawnmower on grass")
        is SubjectCategory.PEOPLE
    )


def _cand(key, category, score, scale="motion"):
    from immich_memories.analysis.subject_policy import SubjectCandidate

    return SubjectCandidate(key=key, category=category, score=score, scale=scale)


def test_a_high_scoring_object_still_loses_to_a_lower_scoring_person() -> None:
    """The lawnmower outscored people on motion and stability. Score is not
    the question -- an object is not a memory."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("lawnmower", SubjectCategory.OBJECT, 0.91),
            _cand("kid", SubjectCategory.PEOPLE, 0.42),
        ],
        animal_ratio=0.10,
        object_ratio=0.0,
        expected_clips=20,
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
        animal_ratio=0.10,
        object_ratio=0.0,
        expected_clips=20,
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
        animal_ratio=0.10,
        object_ratio=0.0,
        expected_clips=20,
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
        animal_ratio=0.10,
        object_ratio=0.0,
        expected_clips=20,
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
        animal_ratio=0.10,
        object_ratio=0.0,
        expected_clips=20,
    )

    assert "unanalysed" in outcome.kept_keys


def test_an_explicit_category_from_the_model_is_used_directly() -> None:
    """Asking the VLM to pick from a closed set beats keyword-matching its prose."""
    assert (
        classify_subject(
            tagged_people=0,
            category="object",
            description="A close-up of a string trimmer lying on concrete blocks",
        )
        is SubjectCategory.OBJECT
    )


def test_face_tags_still_beat_an_explicit_category() -> None:
    """Immich recognised a face; the VLM saying 'object' does not overrule that."""
    assert (
        classify_subject(tagged_people=2, category="object", description=None)
        is SubjectCategory.PEOPLE
    )


def test_the_animal_quota_scales_with_the_length_of_the_video() -> None:
    """A 10-minute video should not get the same two-animal allowance as a 60s one."""
    from immich_memories.analysis.subject_policy import quota_for

    assert quota_for(0.10, expected_clips=15) == 2  # ~60s
    assert quota_for(0.10, expected_clips=150) == 15  # ~10min


def test_a_zero_ratio_means_none_at_all() -> None:
    """The lever for someone who never wants an animal in a memory."""
    from immich_memories.analysis.subject_policy import quota_for

    assert quota_for(0.0, expected_clips=150) == 0


def test_a_tiny_ratio_still_allows_one() -> None:
    """5% of a 15-clip video rounds to under one; the new car should still fit."""
    from immich_memories.analysis.subject_policy import quota_for

    assert quota_for(0.05, expected_clips=15) == 1


def test_a_genuinely_good_object_gets_in_but_a_dull_one_does_not() -> None:
    """Buying a new car is a memory. A lawnmower is not. Both are objects, so
    the slot exists but has to be earned against the people clips."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("p1", SubjectCategory.PEOPLE, 0.40),
            _cand("p2", SubjectCategory.PEOPLE, 0.60),
            _cand("new-car", SubjectCategory.OBJECT, 0.85),
            _cand("lawnmower", SubjectCategory.OBJECT, 0.45),
        ],
        animal_ratio=0.10,
        object_ratio=0.05,
        expected_clips=15,
    )

    assert "new-car" in outcome.kept_keys
    assert "lawnmower" not in outcome.kept_keys


def test_the_bar_is_computed_within_a_scale_not_across_two() -> None:
    """Measured on a real June pool: people video clips score a 0.70 median,
    photos a much lower one. Pooling both gave a 0.43 bar, and a string trimmer
    scoring 0.61 cleared it. Judged against its own scale it does not."""
    from immich_memories.analysis.subject_policy import apply_subject_quotas

    outcome = apply_subject_quotas(
        [
            _cand("clip-person-a", SubjectCategory.PEOPLE, 0.70, scale="motion"),
            _cand("clip-person-b", SubjectCategory.PEOPLE, 0.85, scale="motion"),
            _cand("trimmer", SubjectCategory.OBJECT, 0.61, scale="motion"),
            _cand("photo-person-a", SubjectCategory.PEOPLE, 0.28, scale="photo"),
            _cand("photo-person-b", SubjectCategory.PEOPLE, 0.43, scale="photo"),
        ],
        animal_ratio=0.10,
        object_ratio=0.05,
        expected_clips=15,
    )

    assert "trimmer" not in outcome.kept_keys
    assert "photo-person-a" in outcome.kept_keys


def test_a_screen_demo_is_its_own_category() -> None:
    """A clip of a smartwatch showing running data has a person in it and is
    still not a memory. The model is asked to say so directly."""
    assert (
        classify_subject(tagged_people=0, category="screen", description=None)
        is SubjectCategory.SCREEN
    )


def test_the_description_never_decides_the_category() -> None:
    """Keyword matching on prose classified figurines as animals, a smartwatch
    demo as people, and a treadmill as scenery. Only the model's own label and
    Immich's face tags decide; anything else is unknown, and unknown is kept."""
    for text in (
        "A collection of small animal figurines arranged in a winding line",
        "A person sitting still while wearing a smartwatch displaying running data",
        "Sunset over the sea with mountains in the distance",
    ):
        assert (
            classify_subject(tagged_people=0, category=None, description=text)
            is SubjectCategory.UNKNOWN
        ), text


def test_a_junk_label_is_unknown_not_a_guess() -> None:
    """Models return junk. An unrecognised label must not become a category."""
    assert (
        classify_subject(tagged_people=0, category="Category.PEOPLE!!", description=None)
        is SubjectCategory.UNKNOWN
    )
