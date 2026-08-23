"""Tests for ACE-Step backend — mock only the HTTP calls."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.audio.generators.ace_step_backend import (
    ACEStepBackend,
    ACEStepConfig,
    _detect_season,
    _mood_to_ace_prompt,
    _mood_to_structured_prompt,
)
from immich_memories.audio.generators.base import GenerationRequest

# ---------------------------------------------------------------------------
# _detect_season
# ---------------------------------------------------------------------------


# WHY: several tests below swap a fake "mlx"/"mlx.core" into sys.modules. Metal
# initialises once per process, so if the real extension is first imported *after*
# one of those fakes has been installed and removed, the re-import aborts the whole
# pytest run (SIGABRT) instead of failing a test. Loading it up front, before any
# patching, keeps the real module object in sys.modules for the fakes to shadow and
# restore. No-op when MLX is not installed, which is the case in CI.
with suppress(ImportError):
    import mlx.core  # noqa: F401


class TestDetectSeason:
    @pytest.mark.parametrize(
        "mood,expected",
        [
            ("holiday fun", "holiday"),
            ("festive cheer", "holiday"),
            ("winter vibes", "winter"),
            ("summer sunshine", "summer"),
            ("sunny days", "summer"),
            ("spring fresh", "spring"),
            ("fresh breezes", "spring"),
            ("autumn leaves", "autumn"),
            ("fall colors", "autumn"),
            ("cozy evening", "autumn"),
            ("happy upbeat", None),
            ("", None),
        ],
    )
    def test_detects_season_from_mood(self, mood, expected):
        assert _detect_season(mood) == expected


# ---------------------------------------------------------------------------
# _mood_to_ace_prompt
# ---------------------------------------------------------------------------


class TestMoodToAcePrompt:
    def test_returns_tags_and_lyrics(self):
        tags, lyrics = _mood_to_ace_prompt("happy")
        assert isinstance(tags, str)
        assert isinstance(lyrics, str)
        assert len(tags) > 5

    def test_instrumental_in_lyrics(self):
        _, lyrics = _mood_to_ace_prompt("energetic")
        assert "[Instrumental]" in lyrics

    def test_with_custom_prompt(self):
        tags, lyrics = _mood_to_ace_prompt("calm", prompt="gentle piano")
        assert isinstance(tags, str)


# ---------------------------------------------------------------------------
# _mood_to_structured_prompt
# ---------------------------------------------------------------------------


class TestMoodToStructuredPrompt:
    def test_returns_caption_result(self):
        result = _mood_to_structured_prompt("happy")
        assert hasattr(result, "caption")
        assert hasattr(result, "lyrics")
        assert hasattr(result, "bpm")
        assert hasattr(result, "key_scale")
        assert hasattr(result, "time_signature")

    def test_bpm_is_positive(self):
        result = _mood_to_structured_prompt("energetic")
        assert result.bpm > 0

    def test_with_scene_moods(self):
        result = _mood_to_structured_prompt("happy", scene_moods=["happy", "calm", "energetic"])
        assert result.caption != ""

    def test_with_memory_type(self):
        result = _mood_to_structured_prompt("happy", memory_type="trip")
        assert result.caption != ""


# ---------------------------------------------------------------------------
# ACEStepConfig
# ---------------------------------------------------------------------------


class TestACEStepConfig:
    def test_defaults(self):
        cfg = ACEStepConfig()
        assert cfg.mode == "api"
        assert cfg.api_url == "http://localhost:8000"
        assert cfg.model_variant == "turbo"
        assert cfg.timeout_seconds == 3600

    def test_custom_config(self):
        cfg = ACEStepConfig(mode="api", api_url="http://remote:9000", lm_model_size="0.6B")
        assert cfg.mode == "api"
        assert cfg.api_url == "http://remote:9000"
        assert cfg.lm_model_size == "0.6B"


# ---------------------------------------------------------------------------
# ACEStepBackend
# ---------------------------------------------------------------------------


class TestACEStepBackend:
    def test_name_shows_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))
        backend._effective_mode = "api"
        assert "api" in backend.name.lower()

    def test_name_default(self):
        backend = ACEStepBackend()
        # Before determining mode, shows configured mode
        assert "lib" in backend.name.lower() or "ACE-Step" in backend.name

    def test_get_effective_mode_api_fallback(self):
        """When lib isn't importable, falls back to api."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        # WHY: Mock import check because ace-step isn't installed in test env
        with patch(
            "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
            return_value=False,
        ):
            mode = backend._get_effective_mode()
        assert mode == "api"

    def test_get_effective_mode_caches(self):
        backend = ACEStepBackend()
        backend._effective_mode = "api"
        assert backend._get_effective_mode() == "api"


class TestACEStepBackendV15Library:
    @staticmethod
    def _fake_v15_modules(tmp_path: Path, captured: dict):
        package_root = tmp_path / "ace-step-1.5"
        package_dir = package_root / "acestep"
        package_dir.mkdir(parents=True)
        package_init = package_dir / "__init__.py"
        package_init.write_text("")

        package = ModuleType("acestep")
        package.__file__ = str(package_init)
        package.__path__ = [str(package_dir)]

        handler_module = ModuleType("acestep.handler")

        class FakeHandler:
            def initialize_service(self, **kwargs):
                captured["dit_init"] = kwargs
                return "DiT ready", True

        handler_module.AceStepHandler = FakeHandler

        llm_module = ModuleType("acestep.llm_inference")

        class FakeLLMHandler:
            llm_initialized = False

            def initialize(self, **kwargs):
                captured["lm_init"] = kwargs
                self.llm_initialized = True
                return "LM ready", True

        llm_module.LLMHandler = FakeLLMHandler

        downloader_module = ModuleType("acestep.model_downloader")

        def ensure_lm_model(**kwargs):
            captured["lm_download"] = kwargs
            return True, "LM available"

        downloader_module.ensure_lm_model = ensure_lm_model

        inference_module = ModuleType("acestep.inference")

        class FakeGenerationParams:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeGenerationConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def generate_music(dit_handler, llm_handler, params, config, save_dir):
            captured["generation"] = {
                "dit_handler": dit_handler,
                "llm_handler": llm_handler,
                "params": params,
                "config": config,
                "save_dir": save_dir,
            }
            generated = Path(save_dir) / "upstream-output.wav"
            generated.write_bytes(b"RIFF" + b"audio" * 300)
            return SimpleNamespace(
                success=True,
                error=None,
                audios=[{"path": str(generated), "sample_rate": 48_000}],
            )

        inference_module.GenerationParams = FakeGenerationParams
        inference_module.GenerationConfig = FakeGenerationConfig
        inference_module.generate_music = generate_music

        return {
            "acestep": package,
            "acestep.handler": handler_module,
            "acestep.llm_inference": llm_module,
            "acestep.model_downloader": downloader_module,
            "acestep.inference": inference_module,
        }, package_root

    def test_v15_helper_raises_when_dit_initialization_fails(self, tmp_path):
        from immich_memories.audio.generators.ace_step_backend import (
            _initialize_dit_handler,
        )

        handler = MagicMock()
        handler.initialize_service.return_value = ("weights rejected", False)

        with pytest.raises(RuntimeError, match="DiT initialization failed: weights rejected"):
            _initialize_dit_handler(
                lambda: handler,
                project_root=tmp_path,
                dit_model="acestep-v15-xl-turbo",
                device="mps",
                offload=False,
                use_mlx_dit=True,
            )

    def test_v15_helper_raises_when_lm_download_fails(self, tmp_path):
        from immich_memories.audio.generators.ace_step_backend import (
            _initialize_lm_handler,
        )

        downloader = MagicMock(return_value=(False, "checkpoint unavailable"))

        with pytest.raises(RuntimeError, match="LM download failed: checkpoint unavailable"):
            _initialize_lm_handler(
                MagicMock,
                downloader,
                checkpoint_dir=tmp_path,
                lm_model="acestep-5Hz-lm-4B",
                lm_backend="mlx",
                device="mps",
                offload=False,
            )

    def test_v15_helper_skips_lm_handler_when_planner_is_disabled(self, tmp_path):
        from immich_memories.audio.generators.ace_step_backend import (
            _initialize_lm_handler,
        )

        handler_type = MagicMock()
        downloader = MagicMock()

        result = _initialize_lm_handler(
            handler_type,
            downloader,
            checkpoint_dir=tmp_path,
            lm_model=None,
            lm_backend="mlx",
            device="mps",
            offload=False,
        )

        assert result is None
        handler_type.assert_not_called()
        downloader.assert_not_called()

    def test_v15_library_initializes_mps_and_mlx_handlers(self, tmp_path):
        captured = {}
        modules, package_root = self._fake_v15_modules(tmp_path, captured)
        checkpoint_root = tmp_path / "checkpoints"
        backend = ACEStepBackend(
            ACEStepConfig(
                mode="lib",
                model_variant="base",
                lm_model_size="1.7B",
                use_lm=True,
            )
        )

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(checkpoint_root)}),
            patch("platform.system", return_value="Darwin"),
        ):
            backend._init_pipeline()

        assert captured["dit_init"] == {
            "project_root": str(package_root),
            "config_path": "acestep-v15-base",
            "device": "mps",
            "use_flash_attention": False,
            "compile_model": False,
            "offload_to_cpu": False,
            "offload_dit_to_cpu": False,
            "quantization": None,
            "use_mlx_dit": True,
        }
        assert captured["lm_download"] == {
            "model_name": "acestep-5Hz-lm-1.7B",
            "checkpoints_dir": checkpoint_root,
        }
        assert captured["lm_init"] == {
            "checkpoint_dir": str(checkpoint_root),
            "lm_model_path": "acestep-5Hz-lm-1.7B",
            "backend": "mlx",
            "device": "mps",
            "offload_to_cpu": False,
            "dtype": None,
        }

    def test_v15_library_declines_a_profile_this_host_cannot_hold(self, tmp_path):
        """Jetsam must not be the thing that decides; the music chain can absorb a raise."""
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        backend = ACEStepBackend(
            ACEStepConfig(
                mode="lib", model_variant="acestep-v15-xl-turbo", lm_model_size="4B", use_lm=True
            )
        )

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "checkpoints")}),
            patch("platform.system", return_value="Darwin"),
            # WHY: available_memory_bytes reads this host's real vm_stat
            patch(
                "immich_memories.audio.generators.memory_budget.available_memory_bytes",
                return_value=4 * 1024**3,
            ),
            pytest.raises(RuntimeError, match="needs at least 29 GB"),
        ):
            backend._init_pipeline()

        assert "dit_init" not in captured, "the guard must fire before any weights load"

    def test_v15_library_loads_normally_when_memory_is_plentiful(self, tmp_path):
        """The guard is a floor, not a new step: a host with room behaves as before."""
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        backend = ACEStepBackend(
            ACEStepConfig(
                mode="lib", model_variant="acestep-v15-xl-turbo", lm_model_size="4B", use_lm=True
            )
        )

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "checkpoints")}),
            patch("platform.system", return_value="Darwin"),
            # WHY: available_memory_bytes reads this host's real vm_stat
            patch(
                "immich_memories.audio.generators.memory_budget.available_memory_bytes",
                return_value=200 * 1024**3,
            ),
        ):
            backend._init_pipeline()

        assert captured["dit_init"]["config_path"] == "acestep-v15-xl-turbo"

    @staticmethod
    def _fake_mlx_modules(captured: dict) -> dict:
        # WHY: replaces the real MLX runtime — the test asserts allocator policy
        # calls, not GPU work.
        mlx_package = ModuleType("mlx")
        mlx_core = ModuleType("mlx.core")

        def set_cache_limit(limit: int) -> int:
            captured.setdefault("cache_limits", []).append(limit)
            return 0

        mlx_core.set_cache_limit = set_cache_limit
        mlx_core.clear_cache = lambda: captured.setdefault("clear_cache_calls", []).append(True)
        mlx_core.synchronize = lambda: captured.setdefault("synchronize_calls", []).append(True)
        mlx_core.eval = lambda *_args: None
        mlx_core.float32, mlx_core.bfloat16, mlx_core.floating = "float32", "bfloat16", "floating"
        mlx_core.issubdtype = lambda dtype, _category: dtype in ("float32", "bfloat16")

        class FakeArray:
            def __init__(self, dtype):
                self.dtype = dtype

            def astype(self, dtype):
                return FakeArray(dtype)

        mlx_core.array = FakeArray
        mlx_utils = ModuleType("mlx.utils")

        def tree_map(fn, tree):
            return {k: fn(v) for k, v in tree.items()}

        mlx_utils.tree_map = tree_map
        mlx_package.core = mlx_core
        mlx_package.utils = mlx_utils
        return {"mlx": mlx_package, "mlx.core": mlx_core, "mlx.utils": mlx_utils}

    def test_v15_library_bounds_mlx_memory_before_handler_init(self, tmp_path):
        """The MLX cache limit must be in place before ACE-Step builds handlers.

        ACE-Step caches its GPU config on first handler construction, so limits
        applied afterwards are ignored. The decode chunk is deliberately left
        alone: ACE-Step sizes it from unified memory, and clamping it to the
        small-machine value audibly smeared the output.
        """
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        modules.update(self._fake_mlx_modules(captured))
        base_handler = modules["acestep.handler"].AceStepHandler

        class ObservingHandler(base_handler):
            def __init__(self):
                self.mlx_vae_chunk_size = 2048  # upstream default on >64 GB Macs
                captured["env_at_handler_init"] = {
                    "chunk": os.environ.get("ACESTEP_MLX_VAE_CHUNK"),
                    "tqdm": os.environ.get("ACESTEP_DISABLE_TQDM"),
                    "cache_limits": list(captured.get("cache_limits", [])),
                }

        modules["acestep.handler"].AceStepHandler = ObservingHandler
        backend = ACEStepBackend(
            ACEStepConfig(mode="lib", model_variant="acestep-v15-xl-turbo", use_lm=False)
        )
        env = {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "checkpoints")}

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, env, clear=False),
            patch("platform.system", return_value="Darwin"),
        ):
            for key in ("ACESTEP_MLX_VAE_CHUNK", "ACESTEP_DISABLE_TQDM"):
                os.environ.pop(key, None)
            backend._init_pipeline()
            handler = backend._pipeline.dit_handler

        seen = captured["env_at_handler_init"]
        assert seen["chunk"] is None, "ACE-Step must choose the decode chunk itself"
        assert seen["tqdm"] == "1"
        assert seen["cache_limits"] == [4 * 1024**3]
        assert handler.mlx_vae_chunk_size == 2048

    @pytest.mark.parametrize("upstream_fails", [False, True])
    def test_v15_library_releases_mlx_cache_after_generation(self, tmp_path, upstream_fails):
        """Cached Metal buffers go back to the OS after every generation, even a failed one."""
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        modules.update(self._fake_mlx_modules(captured))
        if upstream_fails:

            def failing_generate(**_kwargs):
                raise RuntimeError("metal blew up")

            modules["acestep.inference"].generate_music = failing_generate
        backend = ACEStepBackend(ACEStepConfig(mode="lib", model_variant="turbo", use_lm=False))
        backend._effective_mode = "lib"
        request = GenerationRequest(
            prompt="calm", duration_seconds=10, output_dir=tmp_path / "music"
        )

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "ckpt")}),
            patch("platform.system", return_value="Darwin"),
        ):
            if upstream_fails:
                with pytest.raises(RuntimeError, match="metal blew up"):
                    asyncio.run(backend.generate(request))
            else:
                asyncio.run(backend.generate(request))

        assert captured.get("clear_cache_calls") == [True]
        assert captured.get("synchronize_calls") == [True]

    def test_v15_library_exit_returns_gpu_memory_to_the_os(self, tmp_path):
        """Leaving the context drops the runtime AND empties torch/MLX caches.

        Dropping the models alone leaves ~26 GB parked in torch's MPS caching
        allocator, so the UI would sit at 27 GB between generations.
        """
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        modules.update(self._fake_mlx_modules(captured))
        backend = ACEStepBackend(ACEStepConfig(mode="lib", model_variant="turbo", use_lm=False))
        backend._effective_mode = "lib"

        async def _use_backend():
            async with backend:
                backend._init_pipeline()
                assert backend._pipeline is not None

        # WHY: replaces the torch MPS allocator boundary (torch is an optional
        # GPU extra, absent in CI) — we assert the release call, not real GPU work.
        fake_torch = ModuleType("torch")
        fake_torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True))
        fake_torch.mps = SimpleNamespace(
            empty_cache=lambda: captured.setdefault("torch_empty_cache", []).append(True)
        )
        modules["torch"] = fake_torch

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "ckpt")}),
            patch("platform.system", return_value="Darwin"),
        ):
            asyncio.run(_use_backend())

        assert backend._pipeline is None
        assert captured.get("torch_empty_cache") == [True]
        assert captured.get("clear_cache_calls") == [True]
        assert captured.get("synchronize_calls") == [True]

    def test_v15_library_casts_mlx_decoder_to_bf16_after_dit_init(self, tmp_path):
        """ACE-Step converts the MLX DiT from its fp32 torch copy; we cast it to bf16."""
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        modules.update(self._fake_mlx_modules(captured))
        fake_array = modules["mlx.core"].array
        base_handler = modules["acestep.handler"].AceStepHandler

        class FakeDecoder:
            def __init__(self):
                self._params = {"weight": fake_array("float32"), "step": 3}

            def parameters(self):
                return dict(self._params)

            def update(self, params):
                self._params.update(params)

        class HandlerWithMlxDecoder(base_handler):
            def __init__(self):
                self.mlx_decoder = FakeDecoder()

        modules["acestep.handler"].AceStepHandler = HandlerWithMlxDecoder
        backend = ACEStepBackend(ACEStepConfig(mode="lib", model_variant="turbo", use_lm=False))

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(tmp_path / "ckpt")}),
            patch("platform.system", return_value="Darwin"),
        ):
            os.environ.pop("IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32", None)
            backend._init_pipeline()
            params = backend._pipeline.dit_handler.mlx_decoder.parameters()

        assert params["weight"].dtype == "bfloat16"
        assert params["step"] == 3  # non-array leaves untouched

    def test_invalid_vae_chunk_override_defers_to_ace_step(self, tmp_path):
        from immich_memories.audio.generators.ace_step_backend import _bound_mlx_memory

        # WHY: replaces MLX, absent off Apple Silicon, and the process environment.
        with (
            patch.dict(sys.modules, self._fake_mlx_modules({})),
            patch.dict(os.environ, {"ACESTEP_MLX_VAE_CHUNK": "not-a-number"}),
        ):
            assert _bound_mlx_memory() is None

    def test_explicit_vae_chunk_override_is_honoured(self, tmp_path):
        from immich_memories.audio.generators.ace_step_backend import _bound_mlx_memory

        # WHY: replaces MLX, absent off Apple Silicon, and the process environment.
        with (
            patch.dict(sys.modules, self._fake_mlx_modules({})),
            patch.dict(os.environ, {"ACESTEP_MLX_VAE_CHUNK": "512"}),
        ):
            assert _bound_mlx_memory() == 512

    @pytest.mark.parametrize(
        ("variant", "expected_model", "expected_steps", "expected_shift"),
        [
            ("turbo", "acestep-v15-turbo", 8, 3.0),
            ("base", "acestep-v15-base", 50, 1.0),
        ],
    )
    def test_v15_library_generates_stable_wav_result(
        self,
        tmp_path,
        variant,
        expected_model,
        expected_steps,
        expected_shift,
    ):
        captured = {}
        modules, _ = self._fake_v15_modules(tmp_path, captured)
        # WHY: _init_pipeline calls _bound_mlx_memory, which imports mlx.core. With
        # ACE-Step actually installed that pulls in the real Metal runtime and aborts
        # the pytest process; the sibling tests in this class fake it for the same
        # reason. This test asserts generation wiring, not GPU behaviour.
        modules.update(self._fake_mlx_modules(captured))
        checkpoint_root = tmp_path / "checkpoints"
        backend = ACEStepBackend(ACEStepConfig(mode="lib", model_variant=variant, use_lm=False))
        backend._effective_mode = "lib"
        request = GenerationRequest(
            prompt="warm family memories",
            duration_seconds=30,
            output_dir=tmp_path / "music",
            variation_index=2,
            memory_type="person_spotlight",
        )

        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"ACESTEP_CHECKPOINTS_DIR": str(checkpoint_root)}),
            patch("platform.system", return_value="Darwin"),
        ):
            result = asyncio.run(backend.generate(request))

        params = captured["generation"]["params"]
        generation_config = captured["generation"]["config"]
        assert captured["dit_init"]["config_path"] == expected_model
        assert params.duration == 30.0
        assert params.instrumental is True
        assert params.lyrics == "[Instrumental]"
        assert params.inference_steps == expected_steps
        assert params.shift == expected_shift
        assert params.thinking is False
        assert generation_config.batch_size == 1
        assert generation_config.audio_format == "wav"
        assert result.audio_path == request.output_dir / "ace_step_v2.wav"
        assert result.audio_path.exists()
        assert result.metadata["infer_step"] == expected_steps


class TestMlxDecoderPrecision:
    """The MLX DiT copy runs at ACE-Step's reference GPU precision (bf16), not fp32."""

    @staticmethod
    def _handler_with_fp32_decoder():
        mx = pytest.importorskip("mlx.core")
        nn = pytest.importorskip("mlx.nn")
        decoder = nn.Linear(4, 4)
        mx.eval(decoder.parameters())
        assert decoder.weight.dtype == mx.float32
        return SimpleNamespace(mlx_decoder=decoder), mx

    def test_mlx_decoder_is_cast_to_bf16(self):
        from immich_memories.audio.generators.ace_step_backend import _cast_mlx_decoder_to_bf16

        handler, mx = self._handler_with_fp32_decoder()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32", None)
            _cast_mlx_decoder_to_bf16(handler)
        assert handler.mlx_decoder.weight.dtype == mx.bfloat16
        assert handler.mlx_decoder.bias.dtype == mx.bfloat16

    def test_fp32_escape_hatch_keeps_decoder_untouched(self):
        from immich_memories.audio.generators.ace_step_backend import _cast_mlx_decoder_to_bf16

        handler, mx = self._handler_with_fp32_decoder()
        with patch.dict(os.environ, {"IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32": "1"}):
            _cast_mlx_decoder_to_bf16(handler)
        assert handler.mlx_decoder.weight.dtype == mx.float32

    def test_handler_without_mlx_decoder_is_a_no_op(self):
        from immich_memories.audio.generators.ace_step_backend import _cast_mlx_decoder_to_bf16

        _cast_mlx_decoder_to_bf16(SimpleNamespace(mlx_decoder=None))
        _cast_mlx_decoder_to_bf16(SimpleNamespace())


class TestACEStepBackendAPIAvailability:
    def test_check_api_healthy(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is True

    def test_check_api_unhealthy(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"status": "error"}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is False

    def test_check_api_connection_error(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(backend._check_api())
        assert result is False


class TestACEStepDiagnosticMessages:
    """Verify that ACE-Step logs helpful messages when unavailable."""

    def test_lib_missing_logs_install_instructions(self, caplog):
        """When lib mode fails, log should include pip install instructions."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib", api_url="http://fake:9999"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock import check and HTTP client — real ace-step not installed,
        # and we don't want real network calls
        with (
            patch(
                "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
                return_value=False,
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            caplog.at_level("WARNING"),
        ):
            result = asyncio.run(backend.is_available())

        assert result is False
        log_text = caplog.text
        assert "pip install" in log_text or "not installed" in log_text

    def test_both_fail_logs_comprehensive_message(self, caplog):
        """When both lib and API fail, log should mention both failure modes."""
        backend = ACEStepBackend(ACEStepConfig(mode="lib", api_url="http://fake:9999"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
                return_value=False,
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            caplog.at_level("WARNING"),
        ):
            result = asyncio.run(backend.is_available())

        assert result is False
        log_text = caplog.text
        # Should mention both the library AND the API URL
        assert "unreachable" in log_text or "api" in log_text.lower()

    def test_api_error_logs_status_code(self, caplog):
        """When API returns non-200, log should show the status code."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:9999"))

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), caplog.at_level("WARNING"):
            result = asyncio.run(backend._check_api())

        assert result is False
        assert "503" in caplog.text

    def test_api_connection_error_logs_exception(self, caplog):
        """When API is unreachable, log should include the exception details."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:9999"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), caplog.at_level("WARNING"):
            result = asyncio.run(backend._check_api())

        assert result is False
        assert "unreachable" in caplog.text or "Connection refused" in caplog.text


class TestACEStepBackendAPIGeneration:
    def test_generate_api_builds_correct_payload(self):
        """The API payload includes all required musical parameters."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "test-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                duration_seconds=30,
                output_dir=Path("/tmp/test_ace_backend"),
            )
            asyncio.run(backend._generate_api(request))

        assert "caption" in captured_payload
        assert "lyrics" in captured_payload
        assert captured_payload["instrumental"] is True
        assert "bpm" in captured_payload
        assert "keyscale" in captured_payload
        assert "timesignature" in captured_payload
        assert captured_payload["duration"] == 30

    def test_generate_api_multi_scene(self):
        """Multi-scene request sums durations and uses scene moods."""
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "multi-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                scenes=[
                    {"mood": "happy", "duration": 20},
                    {"mood": "calm", "duration": 15},
                ],
                duration_seconds=60,
                output_dir=Path("/tmp/test_ace_multi"),
            )
            asyncio.run(backend._generate_api(request))

        assert captured_payload["duration"] == 35  # 20 + 15

    def test_generate_api_caps_duration_at_300(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        captured_payload = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                captured_payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "cap-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="epic",
                duration_seconds=600,
                output_dir=Path("/tmp/test_ace_cap"),
            )
            asyncio.run(backend._generate_api(request))

        assert captured_payload["duration"] == 300  # capped

    def test_generate_api_sends_auth_header(self):
        """API key should be sent as Bearer token."""
        backend = ACEStepBackend(
            ACEStepConfig(
                mode="api",
                api_url="http://fake:8000",
                extra_args={"api_key": "secret-key"},
            )
        )
        backend._effective_mode = "api"

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "auth-123"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise RuntimeError(f"Unexpected URL: {url}")

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        init_kwargs = {}

        def capture_client_init(**kwargs):
            init_kwargs.update(kwargs)
            return mock_client

        # WHY: Mock httpx.AsyncClient to avoid real HTTP calls
        with (
            patch("httpx.AsyncClient", side_effect=capture_client_init),
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
        ):
            request = GenerationRequest(
                prompt="happy",
                duration_seconds=30,
                output_dir=Path("/tmp/test_ace_auth"),
            )
            asyncio.run(backend._generate_api(request))

        assert init_kwargs.get("headers", {}).get("Authorization") == "Bearer secret-key"


class TestACEStepProgressReporting:
    def test_early_phase_llm_reasoning(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(3.0, callback)
        callback.assert_called_once()
        args = callback.call_args[0]
        assert "LLM" in args[0]
        assert args[1] <= 15

    def test_mid_phase_generating(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(15.0, callback)
        args = callback.call_args[0]
        assert "Generating" in args[0] or "diffusion" in args[0]

    def test_late_phase_decoding(self):
        callback = MagicMock()
        ACEStepBackend._report_estimated_progress(40.0, callback)
        args = callback.call_args[0]
        assert "Decoding" in args[0]

    def test_no_callback_no_error(self):
        ACEStepBackend._report_estimated_progress(10.0, None)


class TestACEStepHealthCheck:
    def test_health_check_api_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        backend._effective_mode = "api"

        # WHY: Mock _check_api because it makes real HTTP calls
        with patch.object(backend, "_check_api", new_callable=AsyncMock, return_value=True):
            info = asyncio.run(backend.health_check())

        assert info["backend"] == "ACE-Step (api)"
        assert info["effective_mode"] == "api"
        assert info["available"] is True
        assert info["api_url"] == "http://fake:8000"

    def test_health_check_lib_mode(self):
        backend = ACEStepBackend(ACEStepConfig(mode="lib"))
        backend._effective_mode = "lib"

        # WHY: Mock _is_ace_step_importable because ace-step isn't installed
        with patch(
            "immich_memories.audio.generators.ace_step_backend._is_ace_step_importable",
            return_value=False,
        ):
            info = asyncio.run(backend.health_check())

        assert info["effective_mode"] == "lib"
        assert info["available"] is False
