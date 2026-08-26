"""Strict parsing for the visual episode-scan envelope."""

import json

import pytest

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    InsightEvidence,
    PeriodInsight,
)
from immich_memories.analysis.period_insight_answer import (
    EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
    EpisodePageReading,
    PeriodInsightAnswer,
    read_episode_answer,
    read_episode_answers,
    read_period_answer,
)


@pytest.mark.parametrize(
    "unsafe", ('quote"mark', "back\\slash", "line\nbreak", "caf\N{LATIN SMALL LETTER E WITH ACUTE}")
)
@pytest.mark.parametrize("field", ("visual_summary", "representative_reason"))
def test_episode_reading_constructor_rejects_text_outside_the_safe_wire_alphabet(
    field: str,
    unsafe: str,
) -> None:
    """Direct episode readings cannot bypass the response estimator's alphabet."""
    values = {
        "visual_summary": "A visible finish.",
        "representative_reason": "The finish summarizes the page.",
    }
    values[field] = unsafe

    with pytest.raises(ValueError, match="episode page reading"):
        EpisodePageReading(
            "episode",
            "page",
            values["visual_summary"],
            ("asset",),
            values["representative_reason"],
        )


def test_invalid_episode_text_fails_open_without_erasing_pass_one_siblings() -> None:
    """Unsafe episode prose does not poison independently valid record and Cull arrays."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    raw = json.dumps(
        {
            "schema_version": "episode-scan-v3",
            "pack": 1,
            "episode_readings": [
                {
                    "episode": 1,
                    "page": 1,
                    "visual_summary": "A caf\N{LATIN SMALL LETTER E WITH ACUTE} finish.",
                    "representative_tiles": [1],
                    "representative_reason": "The finish identifies the page.",
                }
            ],
            "record_shots": [
                {"tile": 1, "function": "result proof", "reason": "Records the result."}
            ],
            "cull_rejects": [
                {
                    "tile": 2,
                    "defect": "unusable_exposure",
                    "evidence": "detail_lost_to_darkness",
                }
            ],
        }
    )

    readings = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("episode", "page"),),
        observation_map={(1, 1): ("episode", "page")},
        tile_map={(1, 1, 1): "record", (1, 1, 2): "dark"},
    )
    pass_one = read_cull_namespaces(
        raw,
        pack_alias=1,
        tile_map={1: "record", 2: "dark"},
    )

    assert readings is not None
    assert readings.readings == ()
    assert readings.invalid_observations == (("episode", "page"),)
    assert pass_one is not None
    assert tuple(mark.asset_id for mark in pass_one.record_shots) == ("record",)
    assert tuple(decision.asset_id for decision in pass_one.cull_rejects) == ("dark",)


def test_episode_safe_text_keeps_apostrophes_and_basic_punctuation() -> None:
    """Useful terse episode prose remains valid inside the canonical alphabet."""
    reading = EpisodePageReading(
        "episode",
        "page",
        "Rider's finish: medal #1!",
        ("asset",),
        "Effort -> payoff; both are visible.",
    )

    assert reading.visual_summary == "Rider's finish: medal #1!"


def _provenance() -> DecisionProvenance:
    return DecisionProvenance(
        pass_name="period-insight",  # noqa: S106 - test-only pass identity
        pass_version="1",  # noqa: S106 - test-only pass version
        schema_version="1",
        model_identity="generated-test",
        input_ids=("asset",),
        sheet_hashes=("hash",),
        request_key="request",
        cache_hit=False,
    )


@pytest.mark.parametrize(
    ("observation", "episode_ids", "asset_ids"),
    (
        (" ", ("episode",), ("asset",)),
        ("Visible change.", (), ("asset",)),
        ("Visible change.", ("",), ("asset",)),
        ("Visible change.", ("episode",), ()),
        ("Visible change.", ("episode",), (" ",)),
    ),
)
def test_insight_evidence_constructor_rejects_unqualified_visual_references(
    observation: str,
    episode_ids: tuple[str, ...],
    asset_ids: tuple[str, ...],
) -> None:
    """Direct callers cannot create evidence the strict parser would reject."""
    with pytest.raises(ValueError, match="insight evidence"):
        InsightEvidence(observation, episode_ids, asset_ids)


def test_honest_unavailable_period_records_need_no_invented_evidence() -> None:
    """No-thesis records stay constructible when their unavailable reason is explicit."""
    insight = PeriodInsight(
        thesis=None,
        evidence=(),
        tensions=(),
        recurring_threads=(),
        unavailable_reason="The complete wall was unavailable.",
        revision=0,
        provenance=_provenance(),
    )
    answer = PeriodInsightAnswer(
        thesis=None,
        evidence=(),
        tensions=(),
        recurring_threads=(),
        unavailable_reason="The complete wall was unavailable.",
    )

    assert insight.evidence == answer.evidence == ()


def test_truncated_episode_answer_cannot_select_representatives() -> None:
    """An auto-closable fragment is still not a completed visual observation."""
    raw = (
        '{"schema_version":"episode-scan-v3","episode":1,'
        '"page":1,"episode_reading":{"visual_summary":"the finish",'
        '"representative_tiles":[1,2]'
    )
    tile_map = {(1, 1, 1): "asset-121", (1, 1, 2): "asset-122"}

    assert (
        read_episode_answer(
            raw,
            episode_alias=1,
            page_alias=1,
            observation_map={(1, 1): ("day-1", "day-1-002")},
            tile_map=tile_map,
        )
        is None
    )


def test_complete_final_episode_object_resolves_only_its_page_tiles() -> None:
    """Compact wire aliases map back through the qualified stable episode page."""
    raw = """The requested visual observation follows.
```json
 {"schema_version":"episode-scan-v3","episode":7,"page":2,
 "episode_reading":{"visual_summary":"A rider reaches the finish and shows the medal.",
 "representative_tiles":[121,122],
 "representative_reason":"The effort and payoff summarize this page."}}
```"""
    tile_map = {
        (7, 1, 121): "wrong-page-a",
        (7, 1, 122): "wrong-page-b",
        (7, 2, 121): "finish",
        (7, 2, 122): "medal",
    }

    answer = read_episode_answer(
        raw,
        episode_alias=7,
        page_alias=2,
        observation_map={(7, 2): ("day-1", "day-1-002")},
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


def test_period_answer_rejects_thesis_with_an_unavailable_reason() -> None:
    """A response cannot claim a grounded thesis and claim that thesis was unavailable."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":"A claimed visual arc.",
        "evidence":[{"observation":"Visible change.","representative_tiles":[1]}],
        "tensions":[],
        "recurring_threads":[],
        "unavailable_reason":"The evidence was incomplete."
      }
    }"""

    assert (
        read_period_answer(
            raw,
            page_ids=("period-wall-001",),
            tile_map={("period-wall-001", 1): ("episode", "asset")},
        )
        is None
    )


def test_period_answer_rejects_a_thesis_without_visual_evidence() -> None:
    """A thesis needs at least one page-qualified visual observation."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":"An unsupported visual arc.",
        "evidence":[],
        "tensions":[],
        "recurring_threads":[],
        "unavailable_reason":null
      }
    }"""

    assert (
        read_period_answer(
            raw,
            page_ids=("period-wall-001",),
            tile_map={("period-wall-001", 1): ("episode", "asset")},
        )
        is None
    )


def test_period_answer_rejects_evidence_with_blank_stable_identifiers() -> None:
    """A page-qualified tile is not grounded when its stable identity is blank."""
    raw = """{
      "schema_version":"period-insight-v1",
      "period_insight":{
        "thesis":"An unsupported visual arc.",
        "evidence":[{"observation":"Visible change.","representative_tiles":[1]}],
        "tensions":[],
        "recurring_threads":[],
        "unavailable_reason":null
      }
    }"""

    assert (
        read_period_answer(
            raw,
            page_ids=("period-wall-001",),
            tile_map={("period-wall-001", 1): (" ", "asset")},
        )
        is None
    )


@pytest.mark.parametrize(
    ("evidence", "unavailable_reason"),
    (
        (
            (InsightEvidence("Visible change.", ("episode",), ("asset",)),),
            "The same thesis was unavailable.",
        ),
        ((), None),
    ),
)
def test_period_insight_constructor_cannot_bypass_grounded_exclusive_state(
    evidence: tuple[InsightEvidence, ...], unavailable_reason: str | None
) -> None:
    """Direct construction rejects contradictory or visually unsupported theses."""
    with pytest.raises(ValueError, match="period insight"):
        PeriodInsight(
            thesis="A claimed visual arc.",
            evidence=evidence,
            tensions=(),
            recurring_threads=(),
            unavailable_reason=unavailable_reason,
            revision=0,
            provenance=_provenance(),
        )


def test_period_answer_constructor_cannot_bypass_grounded_exclusive_state() -> None:
    """The parsed-answer record independently rejects an unsupported thesis."""
    with pytest.raises(ValueError, match="period insight answer"):
        PeriodInsightAnswer(
            thesis="A claimed visual arc.",
            evidence=(),
            tensions=(),
            recurring_threads=(),
            unavailable_reason=None,
        )


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
      "schema_version":"episode-scan-v3",
      "pack":1,
      "episode_readings":[
        {"episode":1,"page":1,
         "visual_summary":"Preparation in a quiet room {before the crowd}.",
         "representative_tiles":[1],"representative_reason":"The setup is visible."},
        {"episode":2,"page":1,
         "visual_summary":"A public performance.",
         "representative_tiles":[2],"representative_reason":"The stage is the visible peak."}
      ],
      "record_shots":"not-yet-valid",
      "cull_rejects":[{"tile":true}]
    }"""
    expected = (("morning", "pack-1-001"), ("evening", "pack-1-001"))
    observation_map = {
        (1, 1): ("morning", "pack-1-001"),
        (2, 1): ("evening", "pack-1-001"),
    }
    tile_map = {(1, 1, 1): "warmup", (2, 1, 2): "stage"}

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=expected,
        observation_map=observation_map,
        tile_map=tile_map,
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("morning", "evening")
    assert tuple(reading.representative_asset_ids for reading in answer.readings) == (
        ("warmup",),
        ("stage",),
    )
    assert answer.invalid_observations == ()


@pytest.mark.parametrize(
    ("unusable_summary", "unusable_reason"),
    (
        ("Visible\tA", "visible A"),
        ("Visible A", 'he said "visible"'),
        ("Visible A", 42),
        ("Visible A", "   "),
    ),
)
def test_unusable_episode_prose_invalidates_only_that_packed_reading(
    unusable_summary: object, unusable_reason: object
) -> None:
    """Text that cannot be trusted or shown poisons its own reading and no other.

    Length is not in this list: an over-long reason is fitted to its bound
    instead, because the reading is a decision and the prose only explains it.
    """
    raw = json.dumps(
        {
            "schema_version": "episode-scan-v3",
            "pack": 1,
            "episode_readings": [
                {
                    "episode": 1,
                    "page": 1,
                    "visual_summary": unusable_summary,
                    "representative_tiles": [1],
                    "representative_reason": unusable_reason,
                },
                {
                    "episode": 2,
                    "page": 1,
                    "visual_summary": "Visible B",
                    "representative_tiles": [2],
                    "representative_reason": "visible B",
                },
            ],
        }
    )

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("a", "stable-page"), ("b", "stable-page")),
        observation_map={
            (1, 1): ("a", "stable-page"),
            (2, 1): ("b", "stable-page"),
        },
        tile_map={(1, 1, 1): "asset-a", (2, 1, 2): "asset-b"},
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("b",)
    assert answer.invalid_observations == (("a", "stable-page"),)


def test_episode_namespace_keeps_four_reasoned_representatives_without_a_topic_cap() -> None:
    """Representative count emerges from the pixels; only the global wall bounds it."""
    raw = """{
      "schema_version":"episode-scan-v3","pack":1,
      "episode_readings":[{"episode":1,"page":1,
        "visual_summary":"Four frames.","representative_tiles":[1,2,3,4],
        "representative_reason":"All four differ."}]
    }"""
    tile_map = {(1, 1, number): f"asset-{number}" for number in range(1, 5)}

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("episode", "pack-1-001"),),
        observation_map={(1, 1): ("episode", "pack-1-001")},
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


def test_unknown_episode_alias_keeps_valid_pack_siblings() -> None:
    """An unknown alias leaves its expected episode invalid without poisoning siblings."""
    raw = """{
      "schema_version":"episode-scan-v3","pack":1,
      "episode_readings":[
        {"episode":1,"page":1,"visual_summary":"A",
         "representative_tiles":[1],"representative_reason":"visible A"},
        {"episode":99,"page":1,"visual_summary":"B",
         "representative_tiles":[2],"representative_reason":"invented B"},
        {"episode":3,"page":1,"visual_summary":"C",
         "representative_tiles":[3],"representative_reason":"visible C"}
      ]
    }"""

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("a", "page"), ("b", "page"), ("c", "page")),
        observation_map={
            (1, 1): ("a", "page"),
            (2, 1): ("b", "page"),
            (3, 1): ("c", "page"),
        },
        tile_map={
            (1, 1, 1): "asset-a",
            (2, 1, 2): "asset-b",
            (3, 1, 3): "asset-c",
        },
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("a", "c")
    assert answer.invalid_observations == (("b", "page"),)


def test_duplicate_and_missing_episode_entries_do_not_poison_valid_sibling() -> None:
    """Completeness is checked per expected episode namespace inside one pack."""
    duplicate = (
        '{"episode":1,"page":1,"visual_summary":"A",'
        '"representative_tiles":[1],"representative_reason":"visible A"}'
    )
    raw = (
        '{"schema_version":"episode-scan-v3","pack":1,'
        f'"episode_readings":[{duplicate},{duplicate},'
        '{"episode":3,"page":1,"visual_summary":"C",'
        '"representative_tiles":[3],"representative_reason":"visible C"}]}'
    )

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("a", "page"), ("b", "page"), ("c", "page")),
        observation_map={
            (1, 1): ("a", "page"),
            (2, 1): ("b", "page"),
            (3, 1): ("c", "page"),
        },
        tile_map={
            (1, 1, 1): "asset-a",
            (2, 1, 2): "asset-b",
            (3, 1, 3): "asset-c",
        },
    )

    assert answer is not None
    assert tuple(reading.episode_id for reading in answer.readings) == ("c",)
    assert answer.invalid_observations == (("a", "page"), ("b", "page"))


def test_boolean_tile_ids_are_not_accepted_as_json_integers() -> None:
    """Python's bool-is-int quirk cannot resolve true to displayed tile one."""
    raw = """{
      "schema_version":"episode-scan-v3","pack":1,
      "episode_readings":[{"episode":1,"page":1,
        "visual_summary":"A claimed frame.","representative_tiles":[true],
        "representative_reason":"Claimed visible."}]
    }"""

    answer = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("a", "page"),),
        observation_map={(1, 1): ("a", "page")},
        tile_map={(1, 1, 1): "asset-a"},
    )

    assert answer is not None
    assert answer.readings == ()
    assert answer.invalid_observations == (("a", "page"),)


def test_singular_and_packed_episode_parsers_share_strict_namespace_validation() -> None:
    """The packed transport delegates each member through the singular validation path."""
    singular_raw = """{
      "schema_version":"episode-scan-v3","episode":1,"page":1,
      "episode_reading":{"visual_summary":"Claimed frame.",
        "representative_tiles":[1,1],"representative_reason":"Claimed visible."}
    }"""
    packed_raw = """{
      "schema_version":"episode-scan-v3","pack":1,
      "episode_readings":[{"episode":1,"page":1,
        "visual_summary":"Claimed frame.","representative_tiles":[1,1],
        "representative_reason":"Claimed visible."}]
    }"""
    observation_map = {(1, 1): ("a", "page")}
    tile_map = {(1, 1, 1): "asset-a"}

    assert (
        read_episode_answer(
            singular_raw,
            episode_alias=1,
            page_alias=1,
            observation_map=observation_map,
            tile_map=tile_map,
        )
        is None
    )
    packed = read_episode_answers(
        packed_raw,
        pack_alias=1,
        expected_observations=(("a", "page"),),
        observation_map=observation_map,
        tile_map=tile_map,
    )
    assert packed is not None
    assert packed.readings == ()
    assert packed.invalid_observations == (("a", "page"),)


def test_the_period_prompt_shows_the_exact_shape_its_parser_accepts() -> None:
    """Naming fields in prose leaves their nesting and their types to the model.

    Measured against the local model: it put thesis and evidence at the top
    level instead of under period_insight, and returned tensions and
    recurring_threads as objects where the parser demands plain strings. A good
    thesis was discarded three times over.
    """
    from immich_memories.analysis.period_insight import period_response_shape
    from immich_memories.analysis.period_insight_answer import read_period_answer

    answer = read_period_answer(
        period_response_shape(tile=1),
        page_ids=("page-1",),
        tile_map={("page-1", 1): ("episode-1", "asset-1")},
    )

    assert answer is not None
    assert answer.thesis
    assert answer.tensions
    assert answer.recurring_threads


def test_an_overlong_reason_trims_instead_of_discarding_its_episode() -> None:
    """The prose is display text; the reading it explains is a decision.

    Measured against the local model: the same prompt on the same images
    produced 91/84/76-character reasons on one run and 103/105/104 on the next.
    At a 96-character bound that is a coin flip on whether the whole period
    thesis exists, and nothing downstream can tell it was ever attempted.
    """
    overlong = "T" + "x" * 200
    raw = json.dumps(
        {
            "schema_version": "episode-scan-v3",
            "pack": 1,
            "episode_readings": [
                {
                    "episode": 1,
                    "page": 1,
                    "visual_summary": "A finish line.",
                    "representative_tiles": [1],
                    "representative_reason": overlong,
                }
            ],
            "record_shots": [],
            "cull_rejects": [],
        }
    )

    readings = read_episode_answers(
        raw,
        pack_alias=1,
        expected_observations=(("episode", "page"),),
        observation_map={(1, 1): ("episode", "page")},
        tile_map={(1, 1, 1): "asset"},
    )

    assert readings is not None
    assert readings.invalid_observations == ()
    assert len(readings.readings) == 1
    reading = readings.readings[0]
    assert reading.representative_asset_ids == ("asset",)
    assert len(reading.representative_reason) <= EPISODE_REPRESENTATIVE_REASON_MAX_CHARS
    assert reading.representative_reason.startswith("Tx")


def test_a_thesis_declined_without_a_stated_reason_is_still_an_answer() -> None:
    """Declining is the decision; explaining the decline is prose.

    Measured: the model set thesis to null and left unavailable_reason null
    beside it. The parser demanded exactly one of the two and discarded the
    evidence, tensions and threads that had all parsed correctly.
    """
    answer = read_period_answer(
        json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": None,
                    "evidence": [
                        {"observation": "What tile 1 shows.", "representative_tiles": [1]}
                    ],
                    "tensions": ["A stated tension."],
                    "recurring_threads": ["A stated thread."],
                    "unavailable_reason": None,
                },
            }
        ),
        page_ids=("page-1",),
        tile_map={("page-1", 1): ("episode-1", "asset-1")},
    )

    assert answer is not None
    assert answer.thesis is None
    assert answer.unavailable_reason
    assert answer.tensions == ("A stated tension.",)


def test_a_thesis_and_a_reason_not_to_have_one_stay_contradictory() -> None:
    """Claiming both remains unreadable: that is a contradiction, not an omission."""
    answer = read_period_answer(
        json.dumps(
            {
                "schema_version": "period-insight-v1",
                "period_insight": {
                    "thesis": "The period was about a move.",
                    "evidence": [
                        {"observation": "What tile 1 shows.", "representative_tiles": [1]}
                    ],
                    "tensions": [],
                    "recurring_threads": [],
                    "unavailable_reason": "there was not enough to say",
                },
            }
        ),
        page_ids=("page-1",),
        tile_map={("page-1", 1): ("episode-1", "asset-1")},
    )

    assert answer is None
