"""Strict parsing for the visual episode-scan envelope."""

from immich_memories.analysis.period_insight_answer import (
    read_episode_answer,
    read_episode_answers,
    read_period_answer,
)


def test_truncated_episode_answer_cannot_select_representatives() -> None:
    """An auto-closable fragment is still not a completed visual observation."""
    raw = (
        '{"schema_version":"episode-scan-v1","episode_id":"day-1",'
        '"page_id":"day-1-002","episode_reading":{"visual_summary":"the finish",'
        '"representative_tiles":[1,2]'
    )
    tile_map = {
        ("day-1", "day-1-002", 1): "asset-121",
        ("day-1", "day-1-002", 2): "asset-122",
    }

    assert (
        read_episode_answer(
            raw,
            episode_id="day-1",
            page_id="day-1-002",
            tile_map=tile_map,
        )
        is None
    )


def test_complete_final_episode_object_resolves_only_its_page_tiles() -> None:
    """A complete envelope maps displayed numbers through its qualified page."""
    raw = """The requested visual observation follows.
```json
 {"schema_version":"episode-scan-v1","episode_id":"day-1","page_id":"day-1-002",
 "episode_reading":{"visual_summary":"A rider reaches the finish and shows the medal.",
 "representative_tiles":[121,122],
 "representative_reason":"The effort and payoff summarize this page."}}
```"""
    tile_map = {
        ("day-1", "day-1-001", 121): "wrong-page-a",
        ("day-1", "day-1-001", 122): "wrong-page-b",
        ("day-1", "day-1-002", 121): "finish",
        ("day-1", "day-1-002", 122): "medal",
    }

    answer = read_episode_answer(
        raw,
        episode_id="day-1",
        page_id="day-1-002",
        tile_map=tile_map,
    )

    assert answer is not None
    assert answer.visual_summary == "A rider reaches the finish and shows the medal."
    assert answer.representative_asset_ids == ("finish", "medal")
    assert answer.representative_reason == "The effort and payoff summarize this page."


def test_complete_period_object_grounds_evidence_in_representative_pixels() -> None:
    """Period evidence names stable episodes and assets derived from its visible tiles."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":"Training, racing, and the people around both.",
        "evidence":[
          {"observation":"Effort becomes celebration.","representative_tiles":[1,2]}
        ],
        "tensions":["private effort versus public spectacle"],
        "recurring_threads":["movement", "companionship"],
        "unavailable_reason":null
      }
    }"""
    tile_map = {
        ("period-wall-001", 1): ("training", "effort"),
        ("period-wall-001", 2): ("race-day", "medal"),
    }

    answer = read_period_answer(raw, page_ids=("period-wall-001",), tile_map=tile_map)

    assert answer is not None
    assert answer.thesis == "Training, racing, and the people around both."
    assert answer.evidence[0].episode_ids == ("training", "race-day")
    assert answer.evidence[0].asset_ids == ("effort", "medal")


def test_period_answer_cannot_hide_a_missing_thesis_without_a_reason() -> None:
    """A null thesis is valid only when the answer says why insight was unavailable."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":null,
        "evidence":[],
        "tensions":[],
        "recurring_threads":[],
        "unavailable_reason":null
      }
    }"""

    assert read_period_answer(raw, page_ids=("period-wall-001",), tile_map={}) is None


def test_multi_page_period_evidence_requires_page_qualified_tile_references() -> None:
    """One holistic answer can cite multiple attached pages without number collisions."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":"Preparation becomes performance.",
        "evidence":[{"observation":"A rehearsal contrasts with the stage.",
          "representative_tiles":[
            {"page_id":"period-wall-001","tile":1},
            {"page_id":"period-wall-002","tile":121}
          ]}],
        "tensions":["private versus public"],
        "recurring_threads":["practice"],
        "unavailable_reason":null
      }
    }"""
    tile_map = {
        ("period-wall-001", 1): ("rehearsal", "warmup"),
        ("period-wall-002", 121): ("show", "stage"),
    }

    answer = read_period_answer(
        raw,
        page_ids=("period-wall-001", "period-wall-002"),
        tile_map=tile_map,
    )

    assert answer is not None
    assert answer.evidence[0].episode_ids == ("rehearsal", "show")
    assert answer.evidence[0].asset_ids == ("warmup", "stage")


def test_one_episode_scan_pack_returns_independent_page_qualified_readings() -> None:
    """Many complete small episodes share one physical answer without sharing tile scope."""
    raw = """{
      "schema_version":"episode-scan-v1",
      "pack_id":"pack-1",
      "episode_readings":[
        {"episode_id":"morning","page_id":"pack-1-001",
         "visual_summary":"Preparation in a quiet room {before the crowd}.",
         "representative_tiles":[1],"representative_reason":"The setup is visible."},
        {"episode_id":"evening","page_id":"pack-1-001",
         "visual_summary":"A public performance.",
         "representative_tiles":[2],"representative_reason":"The stage is the visible peak."}
      ],
      "record_shots":"not-yet-valid",
      "cull_rejects":[{"tile":true}]
    }"""
    expected = (("morning", "pack-1-001"), ("evening", "pack-1-001"))
    tile_map = {
        ("morning", "pack-1-001", 1): "warmup",
        ("evening", "pack-1-001", 2): "stage",
    }

    answer = read_episode_answers(
        raw,
        pack_id="pack-1",
        expected_observations=expected,
        tile_map=tile_map,
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("morning", "evening")
    assert tuple(reading.representative_asset_ids for reading in answer.readings) == (
        ("warmup",),
        ("stage",),
    )
    assert answer.invalid_observations == ()


def test_episode_namespace_keeps_four_reasoned_representatives_without_a_topic_cap() -> None:
    """Representative count emerges from the pixels; only the global wall bounds it."""
    raw = """{
      "schema_version":"episode-scan-v1","pack_id":"pack-1",
      "episode_readings":[{"episode_id":"episode","page_id":"pack-1-001",
        "visual_summary":"Four frames.","representative_tiles":[1,2,3,4],
        "representative_reason":"All four differ."}]
    }"""
    tile_map = {("episode", "pack-1-001", number): f"asset-{number}" for number in range(1, 5)}

    answer = read_episode_answers(
        raw,
        pack_id="pack-1",
        expected_observations=(("episode", "pack-1-001"),),
        tile_map=tile_map,
    )

    assert answer is not None
    assert answer.readings[0].representative_asset_ids == (
        "asset-1",
        "asset-2",
        "asset-3",
        "asset-4",
    )
    assert answer.invalid_observations == ()


def test_invalid_episode_namespace_keeps_valid_pack_siblings() -> None:
    """An unknown tile invalidates only that episode reading, not the physical answer."""
    raw = """{
      "schema_version":"episode-scan-v1","pack_id":"pack-1",
      "episode_readings":[
        {"episode_id":"a","page_id":"page","visual_summary":"A",
         "representative_tiles":[1],"representative_reason":"visible A"},
        {"episode_id":"b","page_id":"page","visual_summary":"B",
         "representative_tiles":[99],"representative_reason":"invented B"},
        {"episode_id":"c","page_id":"page","visual_summary":"C",
         "representative_tiles":[3],"representative_reason":"visible C"}
      ]
    }"""

    answer = read_episode_answers(
        raw,
        pack_id="pack-1",
        expected_observations=(("a", "page"), ("b", "page"), ("c", "page")),
        tile_map={
            ("a", "page", 1): "asset-a",
            ("b", "page", 2): "asset-b",
            ("c", "page", 3): "asset-c",
        },
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("a", "c")
    assert answer.invalid_observations == (("b", "page"),)


def test_duplicate_and_missing_episode_entries_do_not_poison_valid_sibling() -> None:
    """Completeness is checked per expected episode namespace inside one pack."""
    duplicate = (
        '{"episode_id":"a","page_id":"page","visual_summary":"A",'
        '"representative_tiles":[1],"representative_reason":"visible A"}'
    )
    raw = (
        '{"schema_version":"episode-scan-v1","pack_id":"pack-1",'
        f'"episode_readings":[{duplicate},{duplicate},'
        '{"episode_id":"c","page_id":"page","visual_summary":"C",'
        '"representative_tiles":[3],"representative_reason":"visible C"}]}'
    )

    answer = read_episode_answers(
        raw,
        pack_id="pack-1",
        expected_observations=(("a", "page"), ("b", "page"), ("c", "page")),
        tile_map={
            ("a", "page", 1): "asset-a",
            ("b", "page", 2): "asset-b",
            ("c", "page", 3): "asset-c",
        },
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("c",)
    assert answer.invalid_observations == (("a", "page"), ("b", "page"))


def test_boolean_tile_ids_are_not_accepted_as_json_integers() -> None:
    """Python's bool-is-int quirk cannot resolve true to displayed tile one."""
    raw = """{
      "schema_version":"episode-scan-v1","pack_id":"pack-1",
      "episode_readings":[{"episode_id":"a","page_id":"page",
        "visual_summary":"A claimed frame.","representative_tiles":[true],
        "representative_reason":"Claimed visible."}]
    }"""

    answer = read_episode_answers(
        raw,
        pack_id="pack-1",
        expected_observations=(("a", "page"),),
        tile_map={("a", "page", 1): "asset-a"},
    )

    assert answer is not None
    assert answer.readings == ()
    assert answer.invalid_observations == (("a", "page"),)


def test_singular_and_packed_episode_parsers_share_strict_namespace_validation() -> None:
    """The packed transport delegates each member through the singular validation path."""
    singular_raw = """{
      "schema_version":"episode-scan-v1","episode_id":"a","page_id":"page",
      "episode_reading":{"visual_summary":"Claimed frame.",
        "representative_tiles":[1,1],"representative_reason":"Claimed visible."}
    }"""
    packed_raw = """{
      "schema_version":"episode-scan-v1","pack_id":"pack-1",
      "episode_readings":[{"episode_id":"a","page_id":"page",
        "visual_summary":"Claimed frame.","representative_tiles":[1,1],
        "representative_reason":"Claimed visible."}]
    }"""
    tile_map = {("a", "page", 1): "asset-a"}

    assert (
        read_episode_answer(
            singular_raw,
            episode_id="a",
            page_id="page",
            tile_map=tile_map,
        )
        is None
    )
    packed = read_episode_answers(
        packed_raw,
        pack_id="pack-1",
        expected_observations=(("a", "page"),),
        tile_map=tile_map,
    )
    assert packed is not None
    assert packed.readings == ()
    assert packed.invalid_observations == (("a", "page"),)
