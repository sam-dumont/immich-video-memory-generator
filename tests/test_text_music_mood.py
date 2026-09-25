"""Music reads the finished cut, without reopening any pictures."""

import json
import sqlite3
from contextlib import closing
from unittest.mock import patch

import httpx
import pytest

from immich_memories.config_loader import Config
from immich_memories.config_models_llm import LLMConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["nas", "gpu"])
async def test_music_uses_the_local_mood_on_tiers_without_an_llm(tmp_path, tier):
    from immich_memories.audio.text_mood import mood_for_cut

    config = Config(tier=tier, llm={"base_url": "http://reader.test", "model": "saved-reader"})
    config.cache.directory = str(tmp_path / "cache")
    (tmp_path / "plan.private.json").write_text(json.dumps({"story": {"thesis": "A fair"}}))
    # WHY: the HTTP boundary must stay unused even when an old endpoint remains configured.
    with patch("httpx.AsyncClient.post", side_effect=AssertionError("Unexpected LLM call")) as post:
        choice = await mood_for_cut(config, tmp_path, (), fallback_mood="playful")

    post.assert_not_called()
    assert choice.mood.primary_mood == "playful"


@pytest.mark.asyncio
async def test_cut_text_answers_once_and_is_reused_without_images(tmp_path):
    from immich_memories.audio.text_mood import mood_for_cut

    config = Config(
        tier="full",
        llm={"base_url": "http://localhost:11434", "model": "text-reader", "provider": "ollama"},
    )
    config.cache.directory = str(tmp_path / "cache")
    config.cache.cache_path.mkdir()
    from immich_memories.store.editorial_preparation import initialize

    with closing(
        sqlite3.connect(config.editorial.resolve_annotation_database(config.cache.cache_path))
    ) as db:
        initialize(db)
        db.executemany(
            "INSERT INTO descriptions VALUES (?,?,?,?,?)",
            [
                (
                    "kept",
                    config.editorial.description_model,
                    "Laughing on a carousel",
                    "model",
                    "now",
                ),
                ("kept", "old-producer", "Old description", "model", "now"),
                (
                    "dropped",
                    config.editorial.description_model,
                    "A dropped picture",
                    "model",
                    "now",
                ),
            ],
        )
        db.commit()
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "story": {
                    "thesis": "A noisy afternoon at the fair",
                    "episodes": [
                        {"episode": "fair", "title": "The carousel"},
                    ],
                },
                "carriers": [
                    {
                        "asset_id": "kept",
                        "story_episode": "fair",
                        "depicted_moment": "moment-001",
                        "seconds": 4,
                    }
                ],
            }
        )
    )
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={
            "response": '{"primary_mood":"playful","energy_level":"high",'
            '"tempo_suggestion":"fast","genre_suggestions":["pop"],"specific_style":null}',
            "done": True,
        },
    )
    # WHY: intercept only the external model's HTTP boundary; the bank is real SQLite.
    with patch("httpx.AsyncClient.post", return_value=response) as post:
        first = await mood_for_cut(config, attempt, ("kept",))
        second = await mood_for_cut(config, attempt, ("kept",))

    assert first == second
    assert first.mood.primary_mood == "playful"
    assert first.source == "cut_text"
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert "images" not in payload
    assert "A noisy afternoon at the fair" in payload["prompt"]
    assert "The carousel" in payload["prompt"]
    assert "Laughing on a carousel" in payload["prompt"]
    assert "Old description" not in payload["prompt"]
    assert "A dropped picture" not in payload["prompt"]
    assert "moment-001" not in payload["prompt"]


def test_bundled_selection_uses_the_cut_mood_and_leaves_clip_facts_alone(tmp_path):
    from immich_memories.generate_music import resolve_music
    from immich_memories.processing.assembly_config import AssemblyClip

    config = Config(
        tier="full",
        llm={"base_url": "http://localhost:11434", "model": "text-reader", "provider": "ollama"},
    )
    config.cache.directory = str(tmp_path / "cache")
    config.ace_step.enabled = config.musicgen.enabled = False
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "plan.private.json").write_text(
        json.dumps(
            {
                "story": {"thesis": "An afternoon on the carousel"},
                "carriers": [{"asset_id": "kept", "seconds": 4}],
            }
        )
    )
    library = tmp_path / "library"
    (library / "happy").mkdir(parents=True)
    (library / "happy" / "fair.opus").touch()
    (library / "sad").mkdir()
    (library / "sad" / "sad.opus").touch()
    clip = AssemblyClip(
        path=tmp_path / "unopened.jpg", asset_id="kept", duration=4, llm_emotion="sad"
    )
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={
            "response": '{"primary_mood":"playful","energy_level":"high",'
            '"tempo_suggestion":"fast","genre_suggestions":["pop"],"specific_style":null}',
            "done": True,
        },
    )
    # WHY: the HTTP model and FFmpeg mastering are external services; selection runs for real.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        patch(
            "immich_memories.audio.mastering.master_music_track", side_effect=lambda src, _dest: src
        ),
    ):
        selected = resolve_music(
            config,
            None,
            False,
            [clip],
            tmp_path / "output",
            None,
            bundled_library=library,
            transition_overlap=0,
            editorial_attempt_dir=attempt,
        )
    assert selected.path == library / "happy" / "fair.opus"
    assert clip.llm_emotion == "sad"
    record = json.loads((attempt / "music-mood.private.json").read_text())
    assert record["source"] == "cut_text"
    assert record["mood"]["primary_mood"] == "playful"


@pytest.mark.asyncio
async def test_standalone_mixer_does_not_contact_a_vision_model_by_default(tmp_path):
    from immich_memories.audio.mixer_class import AudioMixer

    video = tmp_path / "input.mp4"
    video.write_bytes(b"original film")
    output = tmp_path / "output.mp4"
    # WHY: FFprobe and HTTP are external boundaries; an empty music directory needs no encoder.
    with (
        patch("immich_memories.audio.mixer.get_video_duration", return_value=10),
        patch("subprocess.run", side_effect=AssertionError("No implicit frame extraction")),
        patch("httpx.AsyncClient.post", side_effect=AssertionError("No implicit vision request")),
    ):
        await AudioMixer(cache_dir=tmp_path / "music").add_music_to_video(video, output)
    assert output.read_bytes() == video.read_bytes()


@pytest.mark.asyncio
async def test_an_invalid_mood_is_not_banked_or_retried_with_pictures(tmp_path):
    from immich_memories.audio.text_mood import mood_for_cut

    config = Config(
        tier="full",
        llm=LLMConfig(base_url="http://localhost:11434", provider="ollama", model="reader"),
    )
    config.cache.directory = str(tmp_path / "cache")
    (tmp_path / "plan.private.json").write_text(json.dumps({"story": {"thesis": "A fair"}}))
    answers = [
        {
            "primary_mood": "invented",
            "energy_level": "high",
            "tempo_suggestion": "fast",
            "genre_suggestions": ["pop"],
            "specific_style": None,
        },
        {
            "primary_mood": "playful",
            "energy_level": "high",
            "tempo_suggestion": "fast",
            "genre_suggestions": ["pop"],
            "specific_style": None,
        },
    ]
    replies = [
        httpx.Response(
            200,
            request=httpx.Request("POST", "http://localhost"),
            json={"response": json.dumps(answer), "done": True},
        )
        for answer in answers
    ]
    # WHY: the model first returns a syntactically valid but unsupported mood.
    with patch("httpx.AsyncClient.post", side_effect=replies) as post:
        failed = await mood_for_cut(config, tmp_path, ())
        recovered = await mood_for_cut(config, tmp_path, ())
        assert all("images" not in call.kwargs["json"] for call in post.call_args_list)
    assert failed.source == "default_text_unavailable"
    assert recovered.source == "cut_text"
    assert recovered.mood.primary_mood == "playful"
