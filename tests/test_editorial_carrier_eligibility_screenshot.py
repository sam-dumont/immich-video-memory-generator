from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources


def test_phone_screen_resolution_is_a_screenshot_either_orientation():
    lines = {
        "a": "2023-06-04 15:22+00:00 | A crowded outdoor scene | resolution:2532x1170",
        "b": "2023-06-04 15:22+00:00 | A crowded outdoor scene | resolution:1170x2532",
        "c": "2023-06-04 15:22+00:00 | A crowded outdoor scene | resolution:4032x3024",
        "d": "2023-06-04 15:22+00:00 | A crowded outdoor scene",
    }
    excluded = excluded_carrier_sources(lines)
    assert excluded == {"a": "screenshot-resolution", "b": "screenshot-resolution"}


def test_a_face_close_up_by_the_detectors_composition_fact_never_carries():
    lines = {
        "a": "2023-06-23 12:26+00:00 | A man with a beard looks at the camera | picture observations: composition: A single close-up photograph focusing on the face. visible_records: no",
        "b": "2023-06-23 12:10+00:00 | A person poses indoors | picture observations: subject_action: A close-up of a person's face looking directly at the camera. composition: one photograph",
        "c": "2023-06-18 09:06+00:00 | Three cyclists with medals | picture observations: composition: A single photograph showing three men in the foreground.",
        "d": "2023-06-17 15:44+00:00 | Brick ceiling | picture observations: composition: A close-up view of a brick architectural structure.",
    }
    excluded = excluded_carrier_sources(lines)
    assert excluded == {"a": "face-close-up", "b": "face-close-up"}


def test_a_portrait_phone_video_is_never_a_screenshot():
    lines = {
        "v": "2024-02-25 08:02+00:00 | VIDEO 42s raw | A baby lies on a green blanket | resolution:1080x1920 | STARRED by the photographer",
        "l": "2024-02-25 08:02+00:00 | LIVE PHOTO (renders as a still) | A baby lies on a green blanket | resolution:1080x1920",
        "s": "2024-02-23 17:03+00:00 | A baby is held by a man | resolution:1080x1920",
    }
    assert excluded_carrier_sources(lines) == {"s": "screenshot-resolution"}
