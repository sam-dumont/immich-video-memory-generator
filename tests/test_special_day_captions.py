"""A prepared day is judged from caption text, with no second photo send."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from immich_memories.automation.special_day_scan import scan_year
from immich_memories.config_models_llm import LLMConfig


def test_prepared_scan_never_downloads_thumbnails_and_reuses_its_answer(tmp_path):
    assets = [
        SimpleNamespace(
            id=f"a-{h}-{m}",
            file_created_at=datetime(2021, 4, 13, h, m, tzinfo=UTC),
            exif_info=None,
            people=[],
        )
        for h in range(9, 17)
        for m in (0, 20, 40)
    ]
    captions = {a.id: "A group runs across a finish line" for a in assets}
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={
            "response": json.dumps(
                {
                    "special": True,
                    "title": "At the finish line",
                    "subtitle": "",
                    "what": "A race",
                    "window": None,
                }
            ),
            "done": True,
        },
    )

    def thumbnail_for(asset_id):
        raise AssertionError("Prepared days must not download pictures")

    config = LLMConfig(model="text-reader", provider="ollama")
    # WHY: intercept the external LLM HTTP request; the scan and bank run unchanged.
    with patch("httpx.AsyncClient.post", return_value=response) as post:
        found = scan_year(
            assets,
            llm_config=config,
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
            thumbnail_for=thumbnail_for,
        )
        repeated = scan_year(
            assets,
            llm_config=config,
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
            thumbnail_for=thumbnail_for,
        )
    assert found == repeated
    assert len(found) == 1
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert "images" not in payload
    assert "finish line" in payload["prompt"]
    assert "2021-04-13T09:" in payload["prompt"]
    assert "special-day-captions-v1" in payload["prompt"]
