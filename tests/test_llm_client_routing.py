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


class TestContentAnalyzerRouting:
    def test_ollama_content_analysis_reaches_the_generate_route(self, tmp_path):
        from immich_memories.analysis._content_providers import OllamaContentAnalyzer

        analyzer = OllamaContentAnalyzer(model="moondream", base_url="http://ollama:11434/")
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the request is what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))

        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post", return_value=_ollama_response('{"description": "a beach"}')
        ) as post:
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert post.call_args[0][0] == "http://ollama:11434/api/generate"
        payload = post.call_args[1]["json"]
        assert len(payload["images"]) == 2
        # Ollama's default 2048-token window does not hold the prompt plus frames.
        assert payload["options"]["num_ctx"] == 4096
        assert result.description == "a beach"

    def test_openai_content_analysis_reaches_the_chat_completions_route(self, tmp_path):
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(
            model="qwen-vl", base_url="http://vlm:8080/v1/", api_key="sk-test", image_detail="high"
        )
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the request is what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))

        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post", return_value=_openai_response('{"description": "a beach"}')
        ) as post:
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert post.call_args[0][0] == "http://vlm:8080/v1/chat/completions"
        parts = post.call_args[1]["json"]["messages"][0]["content"]
        assert [part["image_url"]["detail"] for part in parts if part["type"] == "image_url"] == [
            "high",
            "high",
        ]
        assert result.description == "a beach"


class TestContentAnalysisEmptyAnswers:
    """A model that answers with nothing used to cost the clip its description."""

    def test_null_content_is_retried_rather_than_dropped(self, tmp_path):
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(model="qwen-vl", base_url="http://vlm:8080/v1")
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the answers are what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))

        # A quantized model answering null once and then properly.
        # WHY: the LLM server is the external boundary this request reaches.
        with patch(
            "httpx.AsyncClient.post",
            side_effect=[_openai_response(None), _openai_response('{"description": "a beach"}')],
        ) as post:
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert post.call_count == 2
        assert result.description == "a beach"

    def test_a_model_that_never_answers_is_reported_as_unavailable(self, tmp_path):
        from immich_memories.analysis._content_providers import OllamaContentAnalyzer

        analyzer = OllamaContentAnalyzer(model="moondream", base_url="http://ollama:11434")
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the answers are what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))

        # A small vision model returning an empty string is how it gives up.
        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_ollama_response("")) as post:
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert post.call_count == 3
        assert result.description == "(analysis unavailable)"
        assert result.confidence == 0.2

    def test_a_model_that_only_ever_answers_null_costs_one_clip_not_the_run(self, tmp_path):
        """The shared client gives up after three nulls; the segment must survive that."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(model="qwen-vl", base_url="http://vlm:8080/v1")
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the answers are what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=_openai_response(None)) as post:
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert post.call_count == 3
        assert result.description == "(analysis unavailable)"
        assert analyzer.available


class TestContentAnalysisRejections:
    def test_a_rejected_request_turns_content_analysis_off_for_the_run(self, tmp_path):
        """A missing model is permanent — every later segment would pay for it again."""
        import httpx

        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = OpenAICompatibleContentAnalyzer(model="old-vlm", base_url="http://vlm:8080/v1")
        # WHY: frame extraction shells out to FFmpeg/OpenCV; the rejection is what is under test.
        analyzer.extract_frames = MagicMock(return_value=_fake_frames(tmp_path))
        rejection = httpx.Response(
            404,
            json={"error": {"message": "The model old-vlm was not found"}},
            request=httpx.Request("POST", "http://vlm:8080/v1/chat/completions"),
        )

        # WHY: the LLM server is the external boundary this request reaches.
        with patch("httpx.AsyncClient.post", return_value=rejection):
            result = analyzer.analyze_segment(tmp_path / "clip.mov", 0.0, 3.0)

        assert result.confidence == 0.0
        assert not analyzer.available


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
