"""Tests for photo scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis.asset_merit import ranking_key
from immich_memories.api.models import Person
from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.scoring import (
    LookFailed,
    PhotoLook,
    _photo_look_version,
    score_photo,
    score_photo_with_llm,
)
from tests.conftest import make_asset


def _photo(
    asset_id: str = "p1",
    *,
    is_favorite: bool = False,
    exif_make: str | None = "Apple",
    people: list[Person] | None = None,
):
    """Create a photo Asset for scoring tests."""
    asset = make_asset(
        asset_id,
        is_favorite=is_favorite,
        exif_make=exif_make,
        duration=None,
    )
    if people:
        asset.people = people
    return asset


class TestPhotoScoring:
    """Tests for photo score calculation."""

    def test_default_score_range(self):
        """Score is between 0 and 1."""
        config = PhotoConfig()
        score = score_photo(_photo(), config)
        assert 0.0 <= score <= 1.0

    def test_favorite_outranks_without_scoring_higher(self):
        """A favourite outranks, but is not scored -- it orders.

        Scored as well it was worth 0.25, exactly what detected faces were
        worth, so three strangers tied with the owner's mark. It now sits in
        the sort key that video has always used (asset_merit.ranking_key).
        """
        config = PhotoConfig()
        normal = _photo("p1", is_favorite=False)
        fav = _photo("p2", is_favorite=True)

        assert score_photo(fav, config) == score_photo(normal, config)
        assert ranking_key(fav, score_photo(fav, config)) > ranking_key(
            normal, score_photo(normal, config)
        )

    def test_faces_boost(self):
        """Photos with faces score higher."""
        config = PhotoConfig()
        no_faces = score_photo(_photo("p1"), config)
        person = Person(id="person-1", name="Alice")
        with_faces = score_photo(_photo("p2", people=[person]), config)
        assert with_faces > no_faces

    def test_camera_original_boost(self):
        """Photos from real cameras score higher than screenshots."""
        config = PhotoConfig()
        camera = score_photo(_photo("p1", exif_make="Apple"), config)
        screenshot = score_photo(_photo("p2", exif_make=None), config)
        assert camera > screenshot

    def test_parse_photo_look_averages_complete_numeric_fields(self) -> None:
        from immich_memories.photos.scoring import _parse_photo_look

        look = _parse_photo_look('{"interest": 0.8, "quality": 0.6, "emotion": "joy"}')

        assert look.score == pytest.approx(0.7)

    def test_parse_photo_look_rejects_missing_required_field(self) -> None:
        from immich_memories.photos.scoring import _parse_photo_look

        assert _parse_photo_look('{"message": "analysis unavailable"}') == LookFailed("unparseable")

    def test_an_answer_that_never_closed_its_json_is_truncated_not_unparseable(self) -> None:
        """The two permanent kinds are worth the same to the ledger, not to a human.

        An answer cut off mid-object ran out of tokens; one that came back
        whole and useless is a different problem with a different fix.
        """
        from immich_memories.photos.scoring import _parse_photo_look

        cut_off = _parse_photo_look('{"description": "A child on a beach, holding a')
        whole = _parse_photo_look("I looked at the photo and saw a child.")

        assert cut_off == LookFailed("truncated")
        assert whole == LookFailed("unparseable")

    def test_the_look_keeps_what_the_model_saw(self) -> None:
        """The model was asked only for two numbers and its answer averaged.

        The holistic review reads a clip's description, and a photo never had
        one — so it was handed a bare line, told never to drop a clip for
        missing information, and every photograph in the library was immune to
        the only content judgment in the pipeline.
        """
        from immich_memories.photos.scoring import _parse_photo_look

        look = _parse_photo_look(
            '{"description": "A whiteboard covered in sticky notes", '
            '"category": "object", "subjects": ["whiteboard", "notes"], '
            '"emotion": "focused", "interest": 0.3, "quality": 0.7}'
        )

        assert look.score == pytest.approx(0.5)
        assert look.payload["description"] == "A whiteboard covered in sticky notes"
        assert look.payload["category"] == "object"
        assert look.payload["subjects"] == ["whiteboard", "notes"]
        assert look.payload["emotion"] == "focused"
        assert look.payload["interestingness"] == pytest.approx(0.3)

    def test_a_model_that_answers_with_the_numbers_alone_still_scores(self) -> None:
        """Small models drop fields. Losing the words must not lose the score."""
        from immich_memories.photos.scoring import _parse_photo_look

        look = _parse_photo_look('{"interest": 0.8, "quality": 0.6}')

        assert look.score == pytest.approx(0.7)
        assert look.payload["description"] is None

    @pytest.mark.parametrize(
        "value",
        [True, "0.5", None, float("nan"), float("inf"), -0.1, 1.1],
    )
    def test_photo_score_value_rejects_nonfinite_nonnumeric_or_out_of_range_values(
        self, value
    ) -> None:
        from immich_memories.photos.scoring import _photo_score_value

        assert _photo_score_value(value) is None

    def test_a_picture_that_cannot_be_read_is_transient_not_a_bad_answer(
        self, tmp_path: Path
    ) -> None:
        """The model never saw it, so it never said anything unparseable.

        Classing a local read failure as a bad answer would bank it, and two
        runs later the asset would be written off over a disk hiccup.
        """
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _query_photo_llm

        config = Config(llm={"model": "qwen-vl"}, content_analysis={"enabled": True})

        assert _query_photo_llm(tmp_path / "never-written.jpg", config) == LookFailed(
            "download_failed"
        )

    def test_permanent_llm_failure_is_not_cached_or_retried(self, tmp_path: Path) -> None:
        """A provider failure stays distinguishable while opening the run circuit."""
        import httpx

        from immich_memories.analysis.provider_health import ProviderCircuit
        from immich_memories.config_loader import Config

        photo_a = tmp_path / "a.jpg"
        photo_b = tmp_path / "b.jpg"
        photo_a.write_bytes(b"a")
        photo_b.write_bytes(b"b")
        config = Config(
            llm={"base_url": "http://localhost:9999/v1", "model": "removed-vlm"},
            content_analysis={"enabled": True},
        )
        circuit = ProviderCircuit()
        response = httpx.Response(
            404,
            json={"error": {"message": "model removed-vlm not found"}},
            request=httpx.Request("POST", "http://localhost:9999/v1/chat/completions"),
        )

        # WHY: the VLM server is the network boundary; this is its 404 for a removed model.
        with patch("httpx.AsyncClient.post", return_value=response) as request:
            first = score_photo_with_llm(
                photo_a, 0.42, PhotoConfig(), config, provider_circuit=circuit
            )
            second = score_photo_with_llm(
                photo_b, 0.42, PhotoConfig(), config, provider_circuit=circuit
            )

        assert first == LookFailed("provider_down")
        assert second == LookFailed("provider_down")
        assert request.call_count == 1


class TestCacheFirstScoring:
    """Tests for _enhance_with_llm cache-first scoring (lines 126-198)."""

    @staticmethod
    def _app_config():
        from immich_memories.config_loader import Config

        return Config(
            llm={"model": "qwen-test"},
            content_analysis={"enabled": True},
        )

    def _make_scored(self, count: int = 3) -> list[tuple]:
        """Build a list of (Asset, metadata_score) tuples."""

        return [(_photo(f"asset-{i}"), 0.5 + i * 0.1) for i in range(count)]

    def test_cache_hit_returns_cached_score_no_llm(self):
        """When score is cached, return it without calling LLM."""
        from immich_memories.photos.scoring import _enhance_with_llm

        scored = [(_photo("cached-1"), 0.4)]

        mock_cache = MagicMock()
        mock_cache.get_asset_scores_batch.return_value = {
            "cached-1": {"combined_score": 0.88},
        }

        with (
            # WHY: the model is the external boundary this test reaches.
            patch(
                "immich_memories.photos.scoring._get_score_cache",
                return_value=mock_cache,
            ),
            # WHY: the model is the external boundary this test reaches.
            patch(
                "immich_memories.photos.scoring._llm_score_photo",
            ) as mock_llm,
        ):
            result, _payloads = _enhance_with_llm(
                scored,
                PhotoConfig(),
                Path("/tmp"),
                lambda *_args: None,
                db_path=Path("/tmp/scores.db"),
                app_config=self._app_config(),
            )

        assert len(result) == 1
        assert result[0][1] == 0.88
        mock_llm.assert_not_called()

    def test_a_version_bump_re_looks_once_and_leaves_the_old_answer_banked(
        self, tmp_path: Path
    ) -> None:
        from immich_memories.cache.asset_score_cache import AssetScoreCache
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        db_path = tmp_path / "scores.db"
        VideoAnalysisCache(db_path)
        score_cache = AssetScoreCache(db_path)
        old_version = _photo_look_version("qwen-3.5")
        score_cache.save_asset_score("photo-1", "photo", 0.5, 0.91, model_version=old_version)
        app_config = Config(
            llm={"model": "qwen-3.6"},
            content_analysis={"enabled": True},
        )

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.photos.scoring._llm_score_photo",
            return_value=PhotoLook(score=0.77, payload={"description": "a photograph"}),
        ) as mock_look:
            for _ in range(2):
                result, _payloads = _enhance_with_llm(
                    [(_photo("photo-1"), 0.5)],
                    PhotoConfig(),
                    tmp_path,
                    lambda *_args: None,
                    db_path=db_path,
                    app_config=app_config,
                )

        new_version = _photo_look_version("qwen-3.6")
        banked = score_cache.get_asset_scores_batch(["photo-1"], model_version=new_version)
        stranded = score_cache.get_asset_scores_batch(["photo-1"], model_version=old_version)

        assert mock_look.call_count == 1
        assert result[0][1] == 0.77
        assert banked["photo-1"]["combined_score"] == 0.77
        assert stranded["photo-1"]["combined_score"] == 0.91

    def _failing_runs(
        self, tmp_path: Path, failure: LookFailed, runs: int
    ) -> tuple[int, str, Path]:
        """Score one photo `runs` times against a model that always fails."""
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        db_path = tmp_path / "scores.db"
        VideoAnalysisCache(db_path)
        app_config = Config(llm={"model": "qwen-3.6"}, content_analysis={"enabled": True})

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.photos.scoring._llm_score_photo",
            return_value=failure,
        ) as mock_look:
            for _ in range(runs):
                _enhance_with_llm(
                    [(_photo("photo-1"), 0.5)],
                    PhotoConfig(),
                    tmp_path,
                    lambda *_args: None,
                    db_path=db_path,
                    app_config=app_config,
                )

        return mock_look.call_count, _photo_look_version("qwen-3.6"), db_path

    def test_a_truncated_look_is_re_asked_once_more_then_left_alone(self, tmp_path: Path) -> None:
        calls, _version, _db = self._failing_runs(tmp_path, LookFailed("truncated"), runs=4)

        assert calls == 2

    def test_an_unparseable_look_is_remembered_by_its_kind(self, tmp_path: Path) -> None:
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        _calls, version, db_path = self._failing_runs(tmp_path, LookFailed("unparseable"), runs=1)
        cache = AssetScoreCache(db_path)

        assert cache.failed_looks(["photo-1"], model_version=version)["photo-1"]["kind"] == (
            "unparseable"
        )
        # A verdict is not a score: nothing must read back as the model's answer.
        assert cache.get_asset_scores_batch(["photo-1"], model_version=version) == {}

    @pytest.mark.parametrize("kind", ["provider_down", "download_failed"])
    def test_a_transient_failure_is_never_banked_and_is_asked_again(
        self, tmp_path: Path, kind: str
    ) -> None:
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        calls, version, db_path = self._failing_runs(tmp_path, LookFailed(kind), runs=4)

        assert calls == 4
        assert AssetScoreCache(db_path).failed_looks(["photo-1"], model_version=version) == {}

    def test_a_version_bump_re_asks_a_look_that_had_been_given_up_on(self, tmp_path: Path) -> None:
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        _calls, _version, db_path = self._failing_runs(tmp_path, LookFailed("truncated"), runs=3)
        bumped = Config(llm={"model": "qwen-4.0"}, content_analysis={"enabled": True})
        VideoAnalysisCache(db_path)

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.photos.scoring._llm_score_photo",
            return_value=PhotoLook(score=0.66, payload={"description": "a photograph"}),
        ) as mock_look:
            result, _payloads = _enhance_with_llm(
                [(_photo("photo-1"), 0.5)],
                PhotoConfig(),
                tmp_path,
                lambda *_args: None,
                db_path=db_path,
                app_config=bumped,
            )

        assert mock_look.call_count == 1
        assert result[0][1] == 0.66

    def test_failed_semantic_score_falls_back_without_claiming_model(self, tmp_path: Path) -> None:
        from immich_memories.cache.asset_score_cache import AssetScoreCache
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        db_path = tmp_path / "scores.db"
        VideoAnalysisCache(db_path)
        app_config = Config(
            llm={"model": "qwen-3.6"},
            content_analysis={"enabled": True},
        )

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.photos.scoring._llm_score_photo",
            return_value=None,
        ):
            result, _payloads = _enhance_with_llm(
                [(_photo("photo-1"), 0.5)],
                PhotoConfig(),
                tmp_path,
                lambda *_args: None,
                db_path=db_path,
                app_config=app_config,
            )

        assert result[0][1] == 0.5
        assert AssetScoreCache(db_path).get_asset_score("photo-1") is None

    def test_incomplete_success_response_is_not_cached_as_a_semantic_score(
        self, tmp_path: Path
    ) -> None:
        import httpx

        from immich_memories.cache.asset_score_cache import AssetScoreCache
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        db_path = tmp_path / "scores.db"
        VideoAnalysisCache(db_path)
        app_config = Config(
            llm={"base_url": "http://localhost:9999/v1", "model": "qwen-3.6"},
            content_analysis={"enabled": True},
        )
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"message":"analysis unavailable"}'}}]},
            request=httpx.Request("POST", "http://localhost:9999/v1/chat/completions"),
        )

        # WHY: the model is the external boundary this test reaches.
        with patch("httpx.post", return_value=response):
            result, _payloads = _enhance_with_llm(
                [(_photo("photo-1"), 0.5)],
                PhotoConfig(),
                tmp_path,
                MagicMock(),
                db_path=db_path,
                app_config=app_config,
                thumbnail_fn=MagicMock(return_value=b"jpeg"),
            )

        assert result[0][1] == 0.5
        assert AssetScoreCache(db_path).get_asset_score("photo-1") is None

    def test_disabled_content_analysis_skips_thumbnail_and_download_io(
        self, tmp_path: Path
    ) -> None:
        from immich_memories.config_loader import Config
        from immich_memories.photos.scoring import _enhance_with_llm

        thumbnail_fn = MagicMock(return_value=b"jpeg")
        download_fn = MagicMock()

        result, _payloads = _enhance_with_llm(
            [(_photo("photo-1"), 0.5)],
            PhotoConfig(),
            tmp_path,
            download_fn,
            app_config=Config(content_analysis={"enabled": False}),
            thumbnail_fn=thumbnail_fn,
        )

        assert [(asset.id, score) for asset, score in result] == [("photo-1", 0.5)]
        thumbnail_fn.assert_not_called()
        download_fn.assert_not_called()

    def test_cache_miss_calls_llm_and_saves(self):
        """When score is NOT cached, run LLM and save result to cache."""
        from immich_memories.photos.scoring import _enhance_with_llm

        scored = [(_photo("uncached-1"), 0.5)]

        mock_cache = MagicMock()
        # WHY: database — no cached entry for this asset
        mock_cache.get_asset_scores_batch.return_value = {}

        with (
            # WHY: the model is the external boundary this test reaches.
            patch(
                "immich_memories.photos.scoring._get_score_cache",
                return_value=mock_cache,
            ),
            # WHY: external LLM API
            patch(
                "immich_memories.photos.scoring._llm_score_photo",
                return_value=PhotoLook(score=0.75, payload={"description": "a photograph"}),
            ) as mock_llm,
        ):
            result, _payloads = _enhance_with_llm(
                scored,
                PhotoConfig(),
                Path("/tmp"),
                lambda *_args: None,
                db_path=Path("/tmp/scores.db"),
                app_config=self._app_config(),
            )

        assert result[0][1] == 0.75
        mock_llm.assert_called_once()
        # The words go into the cache beside the score, so a later run can hand
        # the review a photograph that describes itself without paying again.
        mock_cache.save_asset_score.assert_called_once_with(
            asset_id="uncached-1",
            asset_type="photo",
            metadata_score=0.5,
            combined_score=0.75,
            llm_interest=None,
            llm_quality=None,
            llm_emotion=None,
            llm_description="a photograph",
            # The key names the model and the prompt it answered, so rows from
            # before a photo could describe itself are invalidated once.
            model_version=_photo_look_version("qwen-test"),
        )

    def test_mix_of_cached_and_uncached(self):
        """Batch with some hits and some misses handles both correctly."""
        from immich_memories.photos.scoring import _enhance_with_llm

        scored = [
            (_photo("hit-1"), 0.3),
            (_photo("miss-1"), 0.4),
            (_photo("hit-2"), 0.6),
        ]

        mock_cache = MagicMock()
        # WHY: database — two hits, one miss
        mock_cache.get_asset_scores_batch.return_value = {
            "hit-1": {"combined_score": 0.91},
            "hit-2": {"combined_score": 0.82},
        }

        with (
            # WHY: the model is the external boundary this test reaches.
            patch(
                "immich_memories.photos.scoring._get_score_cache",
                return_value=mock_cache,
            ),
            # WHY: external LLM API — only called for the miss
            patch(
                "immich_memories.photos.scoring._llm_score_photo",
                return_value=PhotoLook(score=0.55, payload={"description": "a photograph"}),
            ) as mock_llm,
        ):
            result, _payloads = _enhance_with_llm(
                scored,
                PhotoConfig(),
                Path("/tmp"),
                lambda *_args: None,
                db_path=Path("/tmp/scores.db"),
                app_config=self._app_config(),
            )

        assert len(result) == 3
        assert result[0][1] == 0.91  # cached
        assert result[1][1] == 0.55  # LLM
        assert result[2][1] == 0.82  # cached
        # LLM called exactly once (for miss-1)
        mock_llm.assert_called_once()
        # Cache save called exactly once (for miss-1)
        mock_cache.save_asset_score.assert_called_once()

    def test_no_cache_available_still_runs_llm(self):
        """When _get_score_cache returns None, LLM runs for all assets."""
        from immich_memories.photos.scoring import _enhance_with_llm

        scored = [(_photo("no-cache-1"), 0.5)]

        with (
            # WHY: database unavailable
            patch(
                "immich_memories.photos.scoring._get_score_cache",
                return_value=None,
            ),
            # WHY: external LLM API
            patch(
                "immich_memories.photos.scoring._llm_score_photo",
                return_value=PhotoLook(score=0.7, payload={"description": "a photograph"}),
            ) as mock_llm,
        ):
            result, _payloads = _enhance_with_llm(
                scored,
                PhotoConfig(),
                Path("/tmp"),
                lambda *_args: None,
                db_path=Path("/tmp/scores.db"),
                app_config=self._app_config(),
            )

        assert result[0][1] == 0.7
        mock_llm.assert_called_once()

    def test_llm_failure_is_distinguishable_from_a_real_semantic_score(
        self, tmp_path: Path
    ) -> None:
        """A failed request must not look like a model-authored score to the cache."""
        from immich_memories.photos.scoring import _llm_score_photo

        asset = _photo("fail-1", exif_make="Apple")
        meta_score = 0.42

        def download_explodes(_id: str, _path: Path) -> None:
            msg = "network error"
            raise ConnectionError(msg)

        result = _llm_score_photo(
            asset, meta_score, PhotoConfig(), tmp_path, download_explodes, None
        )
        assert result == LookFailed("download_failed")

    def test_llm_prepare_failure_is_not_a_semantic_score(self, tmp_path: Path):
        """A decode failure remains distinguishable so callers avoid caching it."""
        from immich_memories.photos.scoring import _llm_score_photo

        asset = _photo("fail-2")
        asset.original_file_name = "photo.jpg"
        meta_score = 0.55

        # Write a dummy file so download succeeds
        (tmp_path / "fail-2.jpg").write_bytes(b"not-a-real-image")

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.photos.photo_pipeline.prepare_photo_source",
            side_effect=RuntimeError("decode failed"),
        ):
            result = _llm_score_photo(
                asset,
                meta_score,
                PhotoConfig(),
                tmp_path,
                lambda *_args: None,
                None,
            )

        assert result == LookFailed("download_failed")

    def test_get_score_cache_returns_none_on_import_error(self):
        """_get_score_cache returns None when dependencies are unavailable."""
        from immich_memories.photos.scoring import _get_score_cache

        # WHY: the model is the external boundary this test reaches.
        with patch(
            "immich_memories.cache.asset_score_cache.AssetScoreCache",
            side_effect=ImportError("no module"),
        ):
            result = _get_score_cache(Path("/tmp/scores.db"))

        assert result is None


class TestPhotoScoringTimeout:
    """A stuck LLM must not hold the photo path for the full read budget.

    The generation path had this exact bug: a scalar httpx timeout gave
    connecting the same hour as generating, so an unreachable server stalled
    silently. Photo scoring builds its own request and needs the same split.
    """

    def test_request_uses_per_phase_timeout(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import patch

        import httpx

        from immich_memories.config_models_analysis import ContentAnalysisConfig
        from immich_memories.config_models_llm import LLMConfig
        from immich_memories.photos.scoring import _query_photo_llm

        photo = tmp_path / "photo.jpg"
        photo.write_bytes(b"not-a-real-jpeg")
        config = SimpleNamespace(
            llm=LLMConfig(timeout_seconds=3600),
            content_analysis=ContentAnalysisConfig(),
        )
        captured: dict = {}
        real_client = httpx.AsyncClient

        def _capture_client(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return real_client(*args, **kwargs)

        # The client is built inside the shared query path now, so the
        # per-phase budget is read off its construction, not off one request.
        # WHY: replaces the HTTP call to the configured LLM provider.
        with (
            # WHY: the model is the external boundary this test reaches.
            patch("httpx.AsyncClient", side_effect=_capture_client),
            # WHY: the model is the external boundary this test reaches.
            patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("no server")),
        ):
            _query_photo_llm(photo, config)

        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout), f"expected httpx.Timeout, got {timeout!r}"
        assert timeout.read == 3600
        assert timeout.connect is not None
        assert timeout.connect < 60


class TestPhotosAskTheSameWayEverythingElseDoes:
    """One way in. The photo path was a second HTTP client with its own rules.

    It posted OpenAI-style whatever provider was configured, skipped the
    rstrip the shared client does, capped itself at 256 tokens, and — unlike
    every other caller — gave up the first time a model answered with nothing.
    """

    def _config(self, provider: str, base_url: str):
        from immich_memories.config import Config

        return Config(
            llm={"provider": provider, "base_url": base_url, "model": "qwen-vl"},
            content_analysis={"enabled": True},
        )

    def test_an_ollama_photo_goes_to_the_ollama_endpoint(self, tmp_path: Path) -> None:
        from immich_memories.photos.scoring import _query_photo_llm

        photo = tmp_path / "p.jpg"
        photo.write_bytes(b"\xff\xd8jpeg")
        response = AsyncMock()
        response.status_code = 200
        response.json = MagicMock(return_value={"response": '{"interest": 0.4, "quality": 0.6}'})
        response.raise_for_status = lambda: None

        # WHY: the model server is the network boundary; the request is the subject.
        with patch("httpx.AsyncClient.post", return_value=response) as post:
            look = _query_photo_llm(photo, self._config("ollama", "http://localhost:11434/"))

        assert look is not None
        assert post.call_args[0][0] == "http://localhost:11434/api/generate"

    def test_a_trailing_slash_does_not_double_for_a_photo(self, tmp_path: Path) -> None:
        from immich_memories.photos.scoring import _query_photo_llm

        photo = tmp_path / "p.jpg"
        photo.write_bytes(b"\xff\xd8jpeg")
        response = AsyncMock()
        response.status_code = 200
        response.json = MagicMock(
            return_value={
                "choices": [{"message": {"content": '{"interest": 0.4, "quality": 0.6}'}}]
            }
        )
        response.raise_for_status = lambda: None

        # WHY: the model server is the network boundary; the URL is the subject.
        with patch("httpx.AsyncClient.post", return_value=response) as post:
            _query_photo_llm(photo, self._config("openai-compatible", "http://host:8080/v1/"))

        assert post.call_args[0][0] == "http://host:8080/v1/chat/completions"


def test_a_cold_photo_pass_reports_the_photos_it_had_to_score(tmp_path: Path, caplog) -> None:
    """The count was gated on a hit, so an all-miss pass reported nothing at all.

    A sweep reading the log back cannot otherwise tell a month whose photo
    cache served everything from one that scored every photo from scratch.
    """
    import logging

    from immich_memories.analysis.provider_health import ProviderCircuit
    from immich_memories.config import Config
    from immich_memories.photos.photo_pipeline import score_photos

    # An open circuit is how the pipeline itself declines to call the model,
    # so scoring runs end to end here without reaching the network.
    circuit = ProviderCircuit()
    circuit.disable("model unavailable")
    config = Config(
        cache={"database": str(tmp_path / "cache.db"), "directory": str(tmp_path)},
        content_analysis={"enabled": True},
        llm={"model": "some-vlm"},
    )

    # Separate days, so these are two moments and both are scored: the
    # shortlist samples one photo per moment, and make_asset dates every
    # photo to now.
    cold = [_photo("cold-1"), _photo("cold-2")]
    for offset, asset in enumerate(cold):
        asset.file_created_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=offset)

    with caplog.at_level(logging.INFO):
        score_photos(
            cold,
            config.photos,
            video_clip_count=0,
            work_dir=tmp_path,
            download_fn=None,
            db_path=config.cache.database_path,
            app_config=config,
            provider_circuit=circuit,
        )

    assert "Photo score cache: 0 hits, 2 misses" in caplog.text
