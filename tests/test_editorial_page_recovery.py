"""A stage's model call fails legibly, whether the answer or the connection broke.

The recorded page here is the verbatim answer a local 35B gave on 2026-09-14: three of
its six new episodes lost the opening quote on `title`. Nothing repairs that answer --
the model failed and the matrix must be able to say so -- but the failure now costs a
bounded number of calls and arrives as a named error with an artifact beside it.

The same day, a local server restarted mid-request and the run ended on `Error: ` with
nothing after it, because httpx raises a read error carrying an empty message.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_moment_inventory import inventory_event
from immich_memories.analysis.editorial_page_recovery import PAGE_READ_SCHEMA, PageReadFailure
from immich_memories.analysis.editorial_story_reading import read_period_story
from immich_memories.analysis.editorial_story_replies import read_episode_page

RECORDED_PAGE = (Path(__file__).parent / "fixtures" / "story_episodes_unquoted_key.txt").read_text()


class RecordingJudge:
    # WHY: stands in for StructureTextJudge, the one boundary that reaches a text model.
    # Unit tests answer from canned strings; no unit test may open a model connection.
    def __init__(self, reply):
        self._reply = reply
        self.asked: list[dict] = []
        self.failures: list[dict] = []

    def ask(self, stage, prompt, max_tokens=0, **_unused):
        self.asked.append({"stage": stage, "prompt": prompt, "max_tokens": max_tokens})
        return self._reply(stage, prompt)

    def record_failure(self, stage, record):
        self.failures.append({"stage": stage, "record": dict(record)})

    @property
    def stages(self):
        return [call["stage"] for call in self.asked]


def fragment(index):
    key = f"M{index:03d}"
    return {
        "reading": f"{key}/1",
        "capture_group": key,
        "taken": f"2024-06-19T09:{index:02d}:00",
        "known_people_in_group": "",
        "places": "",
        "episode_context": "",
        "observations": [f"a plain view number {index}"],
    }


def page_answer(readings, episode):
    return json.dumps(
        {
            "fragments": [{"reading": r, "episode": episode} for r in readings],
            "new_episodes": [
                {
                    "id": episode,
                    "title": "A readable occasion",
                    "account": "What the reader wrote about it.",
                    "role": "supporting",
                }
            ],
        }
    )


def test_the_recorded_page_is_not_repaired_into_a_reading():
    """The model produced invalid JSON; no lenient pass may turn that into an answer."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(RECORDED_PAGE)
    with pytest.raises(ValueError):
        read_episode_page(RECORDED_PAGE, offered={"M033/1"}, existing={})


def test_an_unreadable_page_is_retried_then_repaired_then_recorded():
    judge = RecordingJudge(lambda _stage, _prompt: RECORDED_PAGE)

    with pytest.raises(PageReadFailure) as failure:
        read_period_story(judge, evidence=[fragment(0)], contract="Test contract.", prior={})

    assert judge.stages == ["story-episodes-1", "story-episodes-1-retry", "story-episodes-1-repair"]
    assert "editorial source evidence unavailable" in str(failure.value)
    assert "story-episodes-1" in str(failure.value)
    assert "Expecting property name enclosed in double quotes" in str(failure.value)
    recorded = judge.failures[0]["record"]
    assert recorded["schema_version"] == PAGE_READ_SCHEMA
    assert recorded["stage"] == "story-episodes-1"
    assert recorded["attempt_count"] == 3
    assert recorded["failure_kind"] == "unreadable_json"
    assert "Expecting property name enclosed in double quotes" in recorded["error"]
    assert [row["raw"] for row in recorded["attempts"]] == [RECORDED_PAGE] * 3


def test_the_first_ask_keeps_the_original_prompt_and_budget():
    """Replay guard: only the rounds after the first may change a judgment key."""
    broken = RecordingJudge(lambda _stage, _prompt: RECORDED_PAGE)
    with pytest.raises(PageReadFailure):
        read_period_story(broken, evidence=[fragment(0)], contract="Test contract.", prior={})

    first, retry, repair = broken.asked
    assert first["max_tokens"] == 1400 + 220
    assert retry["max_tokens"] == repair["max_tokens"] == 2 * first["max_tokens"]
    assert retry["prompt"] == first["prompt"]
    assert repair["prompt"].startswith(first["prompt"])
    assert "Expecting property name enclosed in double quotes" in repair["prompt"]


def test_a_retry_that_answers_readably_costs_no_repair_round():
    def reply(stage, _prompt):
        if stage == "story-episodes-1":
            return RECORDED_PAGE
        if stage == "story-episodes-1-retry":
            return page_answer(["M000/1"], "S0001")
        if stage.startswith("story-weighing"):
            return json.dumps({"about": [], "weights": {"K01": "major"}, "join": [], "retitle": {}})
        return json.dumps(
            {
                "thesis": "What this period was about.",
                "about": [],
                "stories": [{"title": "The only story", "episodes": ["S0001"], "purpose": "it"}],
                "uncertainties": [],
            }
        )

    judge = RecordingJudge(reply)
    story = read_period_story(judge, evidence=[fragment(0)], contract="Test contract.", prior={})

    assert judge.failures == []
    assert [e.title for e in story.episodes] == ["A readable occasion"]
    assert "story-episodes-1-repair" not in judge.stages


def test_a_transport_truncation_reaches_the_page_repair_without_another_identical_retry():
    from immich_memories.analysis.editorial_text_failures import TextCompletionFailure

    evidence = [fragment(i) for i in range(9)]
    readings = [row["reading"] for row in evidence]
    failure = TextCompletionFailure(
        [
            {
                "outcome": "incomplete",
                "raw": '{"fragments":[],"new_episodes":[',
                "error": "LLM returned incomplete content",
                "max_tokens": budget,
            }
            for budget in (3380, 6760)
        ]
    )

    def reply(stage, _prompt):
        if stage == "story-episodes-1":
            raise failure
        if stage == "story-episodes-1-repair":
            return page_answer(readings[:1], "S0001")
        if stage == "story-episodes-2":
            assert all(reading in _prompt for reading in readings[1:])
            return json.dumps(
                {
                    "fragments": [
                        {"reading": reading, "episode": "S0001"} for reading in readings[1:]
                    ],
                    "new_episodes": [],
                }
            )
        if stage.startswith("story-weighing"):
            return json.dumps({"about": [], "weights": {"K01": "major"}, "join": [], "retitle": {}})
        return json.dumps(
            {
                "thesis": "The day together.",
                "about": [],
                "stories": [{"title": "The day", "episodes": ["S0001"], "purpose": "it"}],
                "uncertainties": [],
            }
        )

    judge = RecordingJudge(reply)
    story = read_period_story(judge, evidence=evidence, contract="Test contract.", prior={})

    page_calls = [call for call in judge.asked if call["stage"].startswith("story-episodes")]
    assert [call["stage"] for call in page_calls] == [
        "story-episodes-1",
        "story-episodes-1-repair",
        "story-episodes-2",
    ]
    assert [call["max_tokens"] for call in page_calls] == [3380, 6760, 3160]
    assert page_calls[1]["prompt"].startswith(page_calls[0]["prompt"])
    assert "LLM returned incomplete content" in page_calls[1]["prompt"]
    assert {fact["reading"] for episode in story.episodes for fact in episode.facts} == set(
        readings
    )


def test_an_exhausted_transport_repair_stops_and_keeps_the_truncated_evidence():
    from immich_memories.analysis.editorial_text_failures import TextCompletionFailure

    raw = '{"fragments":['

    def reply(_stage, _prompt):
        raise TextCompletionFailure(
            [
                {
                    "outcome": "incomplete",
                    "raw": raw,
                    "error": "LLM returned incomplete content",
                    "max_tokens": budget,
                }
                for budget in (1620, 3240)
            ]
        )

    judge = RecordingJudge(reply)
    with pytest.raises(PageReadFailure) as failure:
        read_period_story(judge, evidence=[fragment(0)], contract="Test contract.", prior={})

    assert judge.stages == ["story-episodes-1", "story-episodes-1-repair"]
    record = failure.value.as_record()
    assert record["attempt_count"] == 2
    assert record["failure_kind"] == "incomplete_transport"
    assert [attempt["raw"] for attempt in record["attempts"]] == [raw, raw]


def test_a_grouping_answer_that_stays_unreadable_is_recorded_not_reraised():
    """Grouping already re-asks once; what was missing was the record and the named error."""

    def reply(stage, _prompt):
        if stage.startswith("story-episodes"):
            return page_answer(["M000/1"], "S0001")
        return "not JSON at all"

    judge = RecordingJudge(reply)
    with pytest.raises(PageReadFailure) as failure:
        read_period_story(judge, evidence=[fragment(0)], contract="Test contract.", prior={})

    assert judge.failures[0]["stage"] == "story-understanding-1"
    assert judge.failures[0]["record"]["attempt_count"] == 2
    assert "editorial source evidence unavailable" in str(failure.value)


def test_the_production_judge_leaves_the_failure_beside_the_calls(tmp_path, monkeypatch):
    """What a matrix cell reads: which stage, which parse error, how many calls it cost."""
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    asked: list[int] = []

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def fake_query(_prompt, _config, **kwargs):
        asked.append(kwargs["max_tokens"])
        return RECORDED_PAGE

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")

    with pytest.raises(PageReadFailure):
        read_period_story(judge, evidence=[fragment(0)], contract="Test contract.", prior={})

    assert asked == [1620, 3240, 3240]
    recorded = sorted((out / "calls").glob("*-json-failure-*.private.json"))
    assert [path.name for path in recorded] == ["03-json-failure-story-episodes-1.private.json"]
    payload = json.loads(recorded[0].read_text())
    assert payload["stage"] == "story-episodes-1"
    assert payload["attempt_count"] == 3
    assert "Expecting property name enclosed in double quotes" in payload["error"]
    assert [row["stage"] for row in payload["attempts"]] == [
        "story-episodes-1",
        "story-episodes-1-retry",
        "story-episodes-1-repair",
    ]
    assert recorded[0].stat().st_mode & 0o077 == 0


def test_a_connection_that_dies_mid_call_is_named_and_recorded(tmp_path, monkeypatch):
    """The server restart case: an exception whose own message is empty must still say what."""
    import httpx

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.analysis.editorial_text_failures import StageCallFailure
    from immich_memories.config_models_llm import LLMConfig

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def dead_connection(_prompt, _config, **_kwargs):
        raise httpx.ReadError("")

    monkeypatch.setattr(gateway, "query_llm", dead_connection)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")

    with pytest.raises(StageCallFailure) as failure:
        judge.ask("story-weighing-reversed", "Weigh these stories.", max_tokens=1200)

    message = str(failure.value)
    assert message.strip() != ""
    assert "story-weighing-reversed" in message
    assert "httpx.ReadError" in message
    assert "at call 1" in message
    assert re.search(r"failed after \d+[ms]", message)
    payload = json.loads(
        (out / "calls" / "01-story-weighing-reversed.failure.private.json").read_text()
    )
    assert payload["error_type"] == "httpx.ReadError"
    assert payload["stage"] == "story-weighing-reversed"
    assert payload["call"] == 1
    assert judge.calls[-1]["warning"] == message


def test_a_refused_text_call_names_the_provider_and_what_had_already_answered(
    tmp_path, monkeypatch
):
    """A text stage cannot go on without its answer, so it must say why and how far it got."""
    from types import SimpleNamespace

    import httpx

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.analysis.editorial_text_failures import StageCallFailure
    from immich_memories.config_models_llm import LLMConfig

    request = httpx.Request("POST", "http://text.test/v1/chat/completions")
    answered = 0

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def refusing(_prompt, _config, **_kwargs):
        if answered:
            raise httpx.HTTPStatusError(
                "Client error '400 Bad Request' for url 'http://text.test/v1/chat/completions' "
                "- provider said code invalid_request_error, message 'rejected as malformed'",
                request=request,
                response=httpx.Response(400, request=request),
            )
        return "an answer"

    monkeypatch.setattr(gateway, "query_llm", refusing)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")
    judge.ask("story-episodes-1", "Read this page.", max_tokens=300)
    answered = 1

    with pytest.raises(StageCallFailure) as failure:
        judge.ask("story-weighing-source", "Weigh these stories.", max_tokens=1200)

    message = str(failure.value)
    assert "story-weighing-source" in message
    assert "rejected as malformed" in message
    assert "1 already answered" in message
    payload = json.loads(
        (out / "calls" / "02-story-weighing-source.failure.private.json").read_text()
    )
    assert payload["status_code"] == 400
    assert "rejected as malformed" in payload["provider_message"]
    assert payload["images_attached"] is False
    assert payload["answered_calls"] == 1


def test_a_call_duration_reads_the_way_a_run_log_does():
    from immich_memories.analysis.editorial_text_failures import elapsed_label

    assert elapsed_label(45.2) == "45s"
    assert elapsed_label(542.0) == "9m2s"


def _unit(index):
    return {
        "asset_id": f"a{index}",
        "kind": "photo",
        "taken": f"2024-06-19T09:{index:02d}:00",
        "favourite": False,
    }


def test_the_moment_inventory_recovers_boundedly_too():
    judge = RecordingJudge(lambda _stage, _prompt: "{not json at all")

    with pytest.raises(PageReadFailure) as failure:
        inventory_event(
            judge,
            event="F01",
            units=[_unit(1)],
            context="A day.",
            line=lambda unit: f"a view of {unit['asset_id']}",
            record=lambda _payload: None,
        )

    assert judge.stages == [
        "moment-inventory-F01-1",
        "moment-inventory-F01-1-retry",
        "moment-inventory-F01-1-repair",
    ]
    assert judge.failures[0]["record"]["attempt_count"] == 3
    assert judge.failures[0]["record"]["failure_kind"] == "unreadable_json"
    assert "editorial source evidence unavailable" in str(failure.value)


def test_a_page_that_reads_but_misses_pictures_is_recorded_as_a_different_failure():
    """The matrix must tell "wrote nonsense" from "read the page and skipped sources"."""
    judge = RecordingJudge(lambda _stage, _prompt: json.dumps({"moments": []}))

    with pytest.raises(PageReadFailure):
        inventory_event(
            judge,
            event="F01",
            units=[_unit(1)],
            context="A day.",
            line=lambda unit: f"a view of {unit['asset_id']}",
            record=lambda _payload: None,
        )

    recorded = judge.failures[0]["record"]
    assert recorded["failure_kind"] == "contract_not_met"
    assert "coverage is incomplete" in recorded["error"]
    assert {row["failure_kind"] for row in recorded["attempts"]} == {"contract_not_met"}


def test_a_throttled_text_reader_waits_instead_of_ending_the_run(tmp_path, monkeypatch):
    """A hosted reader shedding load for a minute must not cost the run its stage."""
    import asyncio

    import httpx

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    request = httpx.Request("POST", "http://text.test/v1/chat/completions")
    waits: list[float] = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    throttled = [True]

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def shedding_load(_prompt, _config, **_kwargs):
        if throttled:
            throttled.pop()
            raise httpx.HTTPStatusError(
                "Client error '429' for url 'http://text.test/v1/chat/completions' - provider "
                "said code rate_limit_exceeded, message 'slow down'",
                request=request,
                response=httpx.Response(429, request=request, headers={"retry-after": "11"}),
            )
        return page_answer(["M000/1"], "S0001")

    monkeypatch.setattr(gateway, "query_llm", shedding_load)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")

    assert judge.ask("story-episodes-1", "Read this page.", max_tokens=300)

    # The provider's own Retry-After, spread by the jitter that keeps overlapping
    # readers from retrying in lockstep, and never shorter than it asked for.
    assert len(waits) == 1 and 11.0 <= waits[0] <= 16.5
    assert not list((out / "calls").glob("*failure*"))


def test_one_503_does_not_end_a_run_that_has_answered_187_calls(tmp_path, monkeypatch):
    """Measured 2026-09-14: the glm February rerun died at planner call 188 on one 503."""
    import asyncio

    import httpx

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    request = httpx.Request("POST", "http://text.test/v1/chat/completions")
    waits: list[float] = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    down = [True]

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def hiccup(_prompt, _config, **_kwargs):
        if down:
            down.pop()
            raise httpx.HTTPStatusError(
                "Server error '503 Service Unavailable' for url "
                "'http://text.test/v1/chat/completions' - provider said code provider_error, "
                "message 'The model provider encountered an error. Please try again.'",
                request=request,
                response=httpx.Response(503, request=request),
            )
        return page_answer(["M000/1"], "S0001")

    monkeypatch.setattr(gateway, "query_llm", hiccup)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")

    assert judge.ask("story-episodes-1", "Read this page.", max_tokens=300)

    assert len(waits) == 1 and 2.0 <= waits[0] <= 3.0
    assert not list((out / "calls").glob("*failure*"))


def test_a_provider_that_stays_down_names_itself_on_the_text_leg(tmp_path, monkeypatch):
    """Still fatal in the end, but recorded as the weather, not as a refused payload."""
    import asyncio

    import httpx

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.analysis.editorial_text_failures import StageCallFailure
    from immich_memories.config_models_llm import LLMConfig

    request = httpx.Request("POST", "http://text.test/v1/chat/completions")

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def always_down(_prompt, _config, **_kwargs):
        raise httpx.HTTPStatusError(
            "Server error '503 Service Unavailable' for url "
            "'http://text.test/v1/chat/completions' - provider said code provider_error, "
            "message 'The model provider encountered an error. Please try again.'",
            request=request,
            response=httpx.Response(503, request=request),
        )

    monkeypatch.setattr(gateway, "query_llm", always_down)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")

    with pytest.raises(StageCallFailure) as failure:
        judge.ask("story-episodes-1", "Read this page.", max_tokens=300)

    assert "encountered an error" in str(failure.value)
    payload = json.loads((out / "calls" / "01-story-episodes-1.failure.private.json").read_text())
    assert payload["reason"] == "provider_unavailable"
    assert payload["status_code"] == 503
