"""A disposable preview page with real text banking and a synthetic audio backend."""

import json
import sys
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from nicegui import ui

from immich_memories.audio.generators.base import GenerationResult, MusicGenerator
from immich_memories.audio.music_pipeline import MusicPipeline
from immich_memories.config_loader import Config
from immich_memories.ui.pages import _step3_music_preview as preview
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


def serve(root: Path, port: int) -> None:
    config = Config()
    config.cache.directory = str(root / "cache")
    config.llm.provider = "ollama"
    config.llm.model = "text-reader"
    config.ace_step.enabled = True
    (root / "plan.private.json").write_text(
        json.dumps(
            {
                "story": {"thesis": "A noisy afternoon on the carousel"},
                "carriers": [{"asset_id": "kept", "seconds": 4}],
            }
        )
    )
    state = AppState(
        config=config,
        editorial_attempt_dir=root,
        clips=[make_clip("kept")],
        selected_clip_ids={"kept"},
    )

    async def model_post(_self, _url, **kwargs):
        (root / "model-request.json").write_text(json.dumps(kwargs["json"]))
        return httpx.Response(
            200,
            request=httpx.Request("POST", "http://localhost"),
            json={
                "response": json.dumps(
                    {
                        "primary_mood": "playful",
                        "energy_level": "high",
                        "tempo_suggestion": "fast",
                        "genre_suggestions": ["pop"],
                    }
                ),
                "done": True,
            },
        )

    class ToneGenerator(MusicGenerator):
        @property
        def name(self):
            return "synthetic audio"

        async def is_available(self):
            return True

        async def generate(self, request, progress_callback=None):
            (root / "music-request.json").write_text(json.dumps(request.scenes))
            track = request.output_dir / "preview.wav"
            with wave.open(str(track), "wb") as output:
                output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                output.writeframes(b"\x00\x00" * 8000)
            return GenerationResult(audio_path=track)

    # WHY: keep the browser's state disposable and replace only remote model/audio services.
    with (
        patch.object(preview, "get_app_state", return_value=state),
        patch("httpx.AsyncClient.post", new=model_post),
        patch(
            "immich_memories.audio.music_pipeline.create_pipeline",
            side_effect=lambda *_args, **_kwargs: MusicPipeline([ToneGenerator()]),
        ),
    ):

        @ui.page("/")
        def page():
            preview.render_music_preview_section({})

        ui.run(host="127.0.0.1", port=port, reload=False, show=False)


if __name__ == "__main__":
    serve(Path(sys.argv[1]), int(sys.argv[2]))
