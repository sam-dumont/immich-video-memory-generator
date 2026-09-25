"""Each prose seat asks for the JSON shape its own parser reads."""

from __future__ import annotations

import json
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest

from immich_memories.analysis.library_catalogue import bank_month_accounts
from immich_memories.store.library_catalogue import CatalogueStore
from tests.test_library_catalogue import FEBRUARY, Reader


class ShapedReader(Reader):
    """Answers like Reader, and keeps the shape each request asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.shapes: list[dict | None] = []

    def request_with_budget(self, prompt: str, *, max_tokens: int, response_format=None) -> str:
        self.shapes.append(response_format)
        return self(prompt)


def test_an_account_request_asks_for_an_account_under_every_offered_key(tmp_path):
    asked = ShapedReader()

    with closing(CatalogueStore(tmp_path / "annotations.sqlite")) as store:
        bank_month_accounts(FEBRUARY, store=store, requester=asked, producer="model-a")

    schema = asked.shapes[0]["json_schema"]["schema"]
    offered = asked.prompts[0].split("exact keys: ")[1].split(".\n")[0].split(", ")
    assert schema["properties"]["accounts"]["required"] == offered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("memory_type", "fields"), [("trip", {"trip_type", "map_mode"}), ("year", {"reason"})]
)
async def test_a_title_request_asks_for_the_fields_its_prompt_names(memory_type, fields):
    from immich_memories.config_models_llm import LLMConfig
    from immich_memories.titles.llm_titles import generate_title_with_llm

    config = LLMConfig(
        provider="openai-compatible", base_url="http://localhost:9999/v1", model="small"
    )
    # WHY: the LLM server is the external boundary this request reaches.
    with patch(
        "immich_memories.titles.llm_titles.query_llm",
        new_callable=AsyncMock,
        return_value=json.dumps({"title": "Crete"}),
    ) as ask:
        await generate_title_with_llm(
            memory_type, "en", "2019-07-04", "2019-07-14", 11, llm_config=config
        )

    schema = ask.call_args.kwargs["response_format"]["json_schema"]["schema"]
    assert fields <= set(schema["properties"])


def test_an_episode_request_asks_for_one_reading_per_offered_episode():
    from immich_memories.analysis.prose_shapes import episode_reading_shape

    shape = episode_reading_shape(3, lean=True)["json_schema"]["schema"]
    episodes = shape["properties"]["episodes"]

    assert (episodes["minItems"], episodes["maxItems"]) == (3, 3)
    assert "cull" not in episodes["items"]["properties"]
    assert (
        "cull"
        in episode_reading_shape(3, lean=False)["json_schema"]["schema"]["properties"]["episodes"][
            "items"
        ]["properties"]
    )
