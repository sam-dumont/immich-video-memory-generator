"""Typed picture facts, asked once of a local reader at ingest and banked per source."""

from __future__ import annotations

import http.server
import io
import json
import sqlite3
import threading
from contextlib import closing

import pytest
from PIL import Image

from immich_memories.analysis.editorial_preparation_picture_facts import (
    NOULS,
    PICTURE_FACTS_PRODUCER,
    QUESTIONS,
    PictureFactsSource,
    missing_picture_facts,
    picture_facts_on,
    plain_picture_facts,
    prepare_picture_facts,
    question_set_hash,
    reader_asker,
)
from immich_memories.store.picture_facts import (
    DESCRIBED,
    REFUSED,
    PictureFacts,
    initialize_picture_facts,
    remember_picture_facts,
    settled_picture_facts,
)


def test_a_banked_row_answers_only_for_the_source_it_was_read_from(tmp_path):
    with closing(sqlite3.connect(tmp_path / "facts.sqlite")) as connection:
        initialize_picture_facts(connection)
        remember_picture_facts(
            connection,
            asset_id="a1",
            producer="picture-facts-v1@openjev/abc",
            source_digest="digest-one",
            facts=PictureFacts(DESCRIBED, {"screen": 0.98}),
        )

        same = settled_picture_facts(
            connection, {"a1": "digest-one"}, "picture-facts-v1@openjev/abc"
        )
        edited = settled_picture_facts(
            connection, {"a1": "digest-two"}, "picture-facts-v1@openjev/abc"
        )
        reworded = settled_picture_facts(
            connection, {"a1": "digest-one"}, "picture-facts-v1@openjev/xyz"
        )

    assert same["a1"].answers == {"screen": 0.98}
    assert edited == {}
    assert reworded == {}


def test_rewording_a_question_invalidates_every_row_it_answered():
    assert f"picture-facts-v1@openjev/{question_set_hash(QUESTIONS)}" == PICTURE_FACTS_PRODUCER

    reworded = dict(QUESTIONS)
    reworded["screen"] = {**QUESTIONS["screen"], "instructions": "Is this a screen?"}
    renamed_option = dict(QUESTIONS)
    renamed_option["what"] = {
        **QUESTIONS["what"],
        "criteria": {**QUESTIONS["what"]["criteria"], "lone_everyday_object": "a thing"},
    }

    assert question_set_hash(reworded) != question_set_hash(QUESTIONS)
    assert question_set_hash(renamed_option) != question_set_hash(QUESTIONS)


class _ReaderEndpoint:
    """A local typed-decision endpoint that records the requests it was sent."""

    def __init__(self, *, status: int = 200, answers: dict | None = None) -> None:
        self.requests: list[dict] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({"path": self.path, **payload})
                if status != 200:
                    self.send_error(status, "no")
                    return
                body = json.dumps({"answers": answers or _answers(), "usage": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _answers(**overrides) -> dict:
    answers = {name: {"noul": 0.02} for name in NOULS}
    answers["what"] = {
        "choice": "people_moment",
        "probabilities": {"people_moment": 0.8, "place_or_scenery": 0.2},
    }
    answers["adult_coverage"] = {"choice": "clothed", "probabilities": {"clothed": 0.9}}
    answers["child_coverage"] = {"choice": "no_child", "probabilities": {"no_child": 0.9}}
    return answers | overrides


def _preview(asset_id: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 900), (30, 90, 160)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def endpoint():
    server = _ReaderEndpoint()
    yield server
    server.close()


def test_one_request_per_picture_carries_the_tile_and_the_frozen_questions(tmp_path, endpoint):
    sources = (PictureFactsSource("a1", "digest-one"), PictureFactsSource("a2", "digest-two"))
    with closing(sqlite3.connect(tmp_path / "facts.sqlite")) as connection:
        outcome = prepare_picture_facts(
            connection=connection,
            sources=sources,
            preview_for=_preview,
            ask=reader_asker(endpoint.base_url, timeout=10),
            concurrency=1,
            check_cancelled=lambda: None,
            progress=lambda *_: None,
        )
        banked = settled_picture_facts(
            connection, {"a1": "digest-one", "a2": "digest-two"}, PICTURE_FACTS_PRODUCER
        )

    assert (outcome.described, outcome.refused, outcome.requests) == (2, 0, 2)
    assert [request["path"] for request in endpoint.requests] == ["/v1/systemone"] * 2
    assert all(len(request["images"]) == 1 for request in endpoint.requests)
    assert endpoint.requests[0]["questions"] == QUESTIONS
    assert banked["a1"].noul("worth") == 0.02
    assert banked["a1"].choice("what") == "people_moment"


def test_a_picture_already_answered_is_never_asked_again(tmp_path, endpoint):
    sources = (PictureFactsSource("a1", "digest-one"),)
    with closing(sqlite3.connect(tmp_path / "facts.sqlite")) as connection:
        for _ in range(2):
            prepare_picture_facts(
                connection=connection,
                sources=missing_picture_facts(connection, sources),
                preview_for=_preview,
                ask=reader_asker(endpoint.base_url, timeout=10),
                concurrency=1,
                check_cancelled=lambda: None,
                progress=lambda *_: None,
            )

    assert len(endpoint.requests) == 1


def test_a_reader_that_refuses_settles_the_row_rather_than_failing_the_run(tmp_path):
    refusing = _ReaderEndpoint(status=503)
    sources = (PictureFactsSource("a1", "digest-one"),)
    try:
        with closing(sqlite3.connect(tmp_path / "facts.sqlite")) as connection:
            outcome = prepare_picture_facts(
                connection=connection,
                sources=sources,
                preview_for=_preview,
                ask=reader_asker(refusing.base_url, timeout=10),
                concurrency=1,
                check_cancelled=lambda: None,
                progress=lambda *_: None,
            )
            banked = settled_picture_facts(connection, {"a1": "digest-one"}, PICTURE_FACTS_PRODUCER)
    finally:
        refusing.close()

    assert (outcome.described, outcome.refused, outcome.failures) == (0, 1, {})
    assert banked["a1"].status == REFUSED
    assert plain_picture_facts(banked["a1"]) == ""


def test_the_segment_names_what_was_seen_and_stays_quiet_about_the_rest():
    watched = PictureFacts(
        DESCRIBED,
        {
            "screen": 0.98,
            "overlay_graphics": 0.03,
            "face_extreme_closeup": 0.01,
            "bathing_now": 0.0,
            "breastfeeding_now": 0.0,
            "medical_procedure": 0.0,
            "private_record": 0.02,
            "worth": 0.12,
            "what": {"choice": "screen_or_document", "probabilities": {}},
            "adult_coverage": {"choice": "no_adult", "probabilities": {}},
            "child_coverage": {"choice": "nappy_or_underwear_only", "probabilities": {}},
        },
    )

    assert plain_picture_facts(watched) == (
        "picture: screen 0.98; worth 0.12; what=screen_or_document; child=nappy_or_underwear_only"
    )


def test_the_segment_is_read_back_off_a_line_and_nothing_else_on_it_is():
    line = (
        "2026-08-25 12:00 | A picture of a room. | picture: screen 0.98; worth 0.12; "
        "what=screen_or_document; child=nappy_or_underwear_only | DARK"
    )

    assert picture_facts_on(line) == {
        "screen": 0.98,
        "worth": 0.12,
        "what": "screen_or_document",
        "child": "nappy_or_underwear_only",
    }
    assert picture_facts_on("2026-08-25 12:00 | A picture of a room. | DARK") == {}
