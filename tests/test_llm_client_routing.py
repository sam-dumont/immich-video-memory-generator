"""The content and mood analyzers ask the model through the one shared client.

Both used to build their own HTTP requests. These tests assert what reaches the
server — the URL, the payload shape, and what happens when a model answers with
nothing — through the httpx boundary rather than through either analyzer's
internals, so they survive the request building moving out of them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _ollama_response(text: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"response": text})
    response.raise_for_status = lambda: None
    return response


def _openai_response(content: str | None) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    )
    response.raise_for_status = lambda: None
    return response


def _fake_frames(tmp_path, count: int = 2) -> list:
    frames = []
    for index in range(count):
        frame = tmp_path / f"frame_{index}.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        frames.append(frame)
    return frames


class TestMoodAnalyzerRouting:
    @pytest.mark.asyncio
    async def test_ollama_mood_analysis_reaches_the_generate_route(self, tmp_path):
        from immich_memories.audio.mood_analyzer_backends import OllamaMoodAnalyzer

        analyzer = OllamaMoodAnalyzer(model="llava", base_url="http://ollama:11434/")

        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post", return_value=_ollama_response('{"primary_mood": "happy"}')
        ) as post:
            mood = await analyzer.analyze_frames(_fake_frames(tmp_path))

        assert post.call_args[0][0] == "http://ollama:11434/api/generate"
        assert len(post.call_args[1]["json"]["images"]) == 2
        assert mood.primary_mood == "happy"

    @pytest.mark.asyncio
    async def test_openai_mood_analysis_retries_null_content(self, tmp_path):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(model="qwen-vl", base_url="http://vlm:8080/v1")

        # A quantized model answering null once and then properly.
        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post",
            side_effect=[_openai_response(None), _openai_response('{"primary_mood": "happy"}')],
        ) as post:
            mood = await analyzer.analyze_frames(_fake_frames(tmp_path))

        assert post.call_args[0][0] == "http://vlm:8080/v1/chat/completions"
        assert post.call_count == 2
        assert mood.primary_mood == "happy"
