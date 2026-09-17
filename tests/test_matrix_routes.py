"""The matrix-routes driver renders real CLI arguments, resumes, and grades honestly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import matrix_routes as driver  # noqa: E402
import matrix_routes_report as report  # noqa: E402

MONTHLY = {
    "id": "monthly",
    "memory_type": "monthly_highlights",
    "duration_seconds": 60,
    "scope": {"year": "@year", "month": "@month"},
}
ALBUM = {"id": "album", "memory_type": "album", "scope": {"album": "@album"}}
PEOPLE = {"id": "people", "memory_type": "multi_person", "scope": {"person": "@person"}}
PINNED_DAY = {
    "id": "on-this-day",
    "memory_type": "on_this_day",
    "scope": {"years_back": 20, "day": "@day"},
}


def args_for(route, values, **overrides):
    settings = {"music": Path("/loop.opus"), "album_name": "Matrix album", "upload": False}
    return driver.generate_args(
        route,
        driver.resolve_scope(route, values),
        output=Path("/out/film.mp4"),
        **{**settings, **overrides},
    )


def test_placeholders_take_their_values_from_the_private_overlay():
    assert driver.resolve_scope(MONTHLY, {"year": 2024, "month": 6}) == {"year": 2024, "month": 6}


@pytest.mark.parametrize("values", [{"year": 2024}, {"year": 2024, "month": None}])
def test_a_placeholder_with_no_private_value_is_a_hard_error(values):
    with pytest.raises(RuntimeError, match="@month"):
        driver.resolve_scope(MONTHLY, values)


def test_a_scope_key_with_no_flag_behind_it_is_a_hard_error():
    route = {"id": "bad", "memory_type": "season", "scope": {"weather": "@weather"}}
    with pytest.raises(RuntimeError, match="weather"):
        driver.resolve_scope(route, {"weather": "rain"})


def test_a_dated_route_renders_its_scope_and_the_fixed_flags():
    args = args_for(MONTHLY, {"year": 2024, "month": 6})
    assert args[:8] == [
        *("--memory-type", "monthly_highlights"),
        *("--year", "2024"),
        *("--month", "6"),
        *("--duration", "60"),
    ]
    assert set(driver.FIXED_FLAGS) <= set(args)
    assert args[args.index("--resolution") + 1] == "1080p"
    assert args[args.index("--format") + 1] == "h265"
    assert args[args.index("--output") + 1] == "/out/film.mp4"
    assert "--upload-to-immich" not in args


def test_a_pinned_day_is_one_public_flag_and_no_automation_identity():
    """The pin must not fabricate a memory key: that key names the directory the
    attempt is written to, so a pinned run would file itself somewhere else."""
    args = args_for(PINNED_DAY, {"day": "2024-07-15"})

    assert args[args.index("--day") + 1] == "2024-07-15"
    assert not {"--memory-key", "--memory-category", "--source"} & set(args)


def test_an_album_route_uses_from_album_and_no_memory_type():
    args = args_for(ALBUM, {"album": "Some album"})
    assert args[:2] == ["--from-album", "Some album"]
    assert "--memory-type" not in args and "--duration" not in args


def test_a_repeated_person_becomes_one_flag_per_name():
    args = args_for(PEOPLE, {"person": ["AAA", "BBB"]})
    assert args[2:6] == ["--person", "AAA", "--person", "BBB"]


def test_upload_appends_the_dated_album_and_refuses_to_run_without_one():
    values = {"year": 2024, "month": 6}
    args = args_for(MONTHLY, values, upload=True)
    assert args[-3:] == ["--upload-to-immich", "--album", "Matrix album"]
    with pytest.raises(RuntimeError, match="album_name"):
        args_for(MONTHLY, values, upload=True, album_name=None)


def test_every_rendered_option_exists_on_the_real_generate_command():
    known = driver.generate_option_flags()
    driver.assert_options_exist(args_for(MONTHLY, {"year": 2024, "month": 6}, upload=True), known)
    driver.assert_options_exist(args_for(ALBUM, {"album": "A"}, upload=True), known)
    with pytest.raises(RuntimeError, match="--not-a-flag"):
        driver.assert_options_exist(["--not-a-flag", "x"], known)


def test_a_supersede_route_must_repeat_the_recipe_it_supersedes():
    first = {"id": "monthly", "args": ["--year", "2024", "--output", "/a/monthly.mp4"]}
    driver.assert_same_recipe(
        {"id": "again", "args": [*first["args"][:2], "--output", "/b/m.mp4"]}, first
    )
    with pytest.raises(RuntimeError, match="cannot supersede"):
        driver.assert_same_recipe(
            {"id": "again", "args": ["--year", "2023", "--output", "/b/m.mp4"]}, first
        )


def _manifest(tmp_path: Path, **case_fields) -> dict:
    config = tmp_path / "config.yaml"
    config.write_text("immich: {}\n")
    return {
        "cli": "immich-memories",
        "config_path": str(config),
        "config_sha256": driver.sha256(config),
        "database_path": str(tmp_path / "runs.db"),
        "editorial_runs": str(tmp_path / "editorial-runs"),
        "upload": True,
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
        "reference": {},
        "cases": [
            {
                "id": "monthly",
                "memory_type": "monthly_highlights",
                "status": "pending",
                "output_directory": str(tmp_path / "monthly"),
                "args": ["--memory-type", "monthly_highlights"],
                **case_fields,
            }
        ],
    }


def _run(manifest, tmp_path, *, retry_failed=False, runner=lambda _command, _log: 0):
    driver.run(
        manifest, tmp_path / "m.private.json", only=None, retry_failed=retry_failed, runner=runner
    )


@pytest.fixture
def disk_headroom(monkeypatch):
    """The 50 GiB guard is real and fires on a busy laptop; these cases render nothing."""
    monkeypatch.setattr(driver, "DEFAULT_FREE_DISK_FLOOR_GIB", 0)


def test_a_finished_case_is_skipped_while_its_film_still_hashes_the_same(tmp_path):
    film = tmp_path / "monthly.mp4"
    film.write_bytes(b"finished film")
    manifest = _manifest(
        tmp_path, status="rendered", film={"path": str(film), "sha256": driver.sha256(film)}
    )
    launched: list[list[str]] = []
    _run(manifest, tmp_path, runner=lambda command, _log: launched.append(command) or 0)
    assert launched == []
    film.write_bytes(b"a different film")
    with pytest.raises(RuntimeError, match="changed on disk"):
        _run(manifest, tmp_path)


def test_a_failed_case_is_recorded_and_runs_again_only_when_asked(tmp_path, disk_headroom):
    manifest = _manifest(tmp_path)
    (tmp_path / "monthly").mkdir()
    (tmp_path / "monthly" / "monthly.mp4").write_bytes(b"film")
    case = manifest["cases"][0]
    _run(manifest, tmp_path, runner=lambda _command, _log: 3)
    assert case["status"] == "failed" and "exited 3" in case["error"]
    _run(manifest, tmp_path)
    assert case["status"] == "failed"
    _run(manifest, tmp_path, retry_failed=True)
    assert case["status"] == "rendered"


def test_a_full_disk_stops_the_batch_before_it_starts_a_render(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["min_free_gib"] = 1 << 30
    with pytest.raises(RuntimeError, match="GiB free"):
        _run(manifest, tmp_path)


def test_a_changed_frozen_config_stops_the_batch(tmp_path):
    manifest = _manifest(tmp_path)
    Path(manifest["config_path"]).write_text("immich: {changed: true}\n")
    with pytest.raises(RuntimeError, match="frozen configuration"):
        _run(manifest, tmp_path)


def test_a_run_whose_soundtrack_never_mixed_fails_verification(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("Optional music failed: no backend\n")
    with pytest.raises(RuntimeError, match="soundtrack"):
        driver.assert_music_applied(log)


def _probed_as(monkeypatch, tmp_path, streams):
    film = tmp_path / "monthly.mp4"
    film.write_bytes(b"not really a film")
    probe = {"streams": streams, "format": {"duration": "61.04"}}
    # WHY: ffprobe is the FFmpeg boundary; tmp_path holds no decodable film to read.
    monkeypatch.setattr(driver.subprocess, "check_output", lambda *_a, **_k: json.dumps(probe))
    return film


@pytest.mark.parametrize(("width", "height"), [(1280, 720), (1080, 1080), (3840, 2160)])
def test_a_film_that_is_not_1080p_fails_verification(tmp_path, monkeypatch, width, height):
    streams = [
        {"codec_type": "video", "width": width, "height": height, "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "aac"},
    ]
    film = _probed_as(monkeypatch, tmp_path, streams)
    with pytest.raises(RuntimeError, match=f"is {width}x{height}, not 1920x1080 or 1080x1920"):
        driver.probe_film(film, "ffprobe")


def test_a_film_without_sound_fails_verification(tmp_path, monkeypatch):
    streams = [{"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "hevc"}]
    film = _probed_as(monkeypatch, tmp_path, streams)
    with pytest.raises(RuntimeError, match="no audio stream"):
        driver.probe_film(film, "ffprobe")


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (1080, 1920)])
def test_a_1080p_film_passes_verification_in_either_orientation(
    tmp_path, monkeypatch, width, height
):
    """The recipes pin `--resolution 1080p` beside `--orientation auto`.

    A phone library renders upright at 1080x1920, and the 09-17 route run marked
    every such case failed while the films were exactly what the recipe asked for.
    """
    streams = [
        {"codec_type": "video", "width": width, "height": height, "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "aac"},
    ]
    film = _probed_as(monkeypatch, tmp_path, streams)

    delivered = driver.probe_film(film, "ffprobe")

    assert (delivered["seconds"], delivered["codec"], delivered["audio_codec"]) == (
        61.04,
        "hevc",
        "aac",
    )


def _collect(tmp_path, delivery, plan=None):
    manifest = _manifest(tmp_path, status="rendered", log_path=str(tmp_path / "run.log"))
    if plan is not None:
        attempt = Path(manifest["editorial_runs"]) / "key" / "attempts" / "a1"
        attempt.mkdir(parents=True)
        (attempt / "status.private.json").write_text(
            json.dumps({"request": {"product": "monthly_highlights"}})
        )
        (attempt / "plan.private.json").write_text(json.dumps(plan))
    driver.collect(
        manifest,
        tmp_path / "m.private.json",
        only=None,
        verify=lambda _case, _mf: {"path": "/out/monthly.mp4", "seconds": 61.0},
        delivery=delivery,
    )
    return manifest["cases"][0]


def test_collect_fails_a_case_that_uploaded_without_recording_an_asset_id(tmp_path):
    case = _collect(tmp_path, delivery=lambda _path, _db: {"run_id": "r1", "immich_asset_id": None})
    assert case["status"] == "failed"
    assert "asset id" in case["error"]


def test_collect_harvests_the_plan_and_the_delivery_of_a_good_film(tmp_path):
    plan = {
        "carriers": [{"asset_id": "a1", "favourite": True}, {"asset_id": "b2"}],
        "content_seconds": 55.5,
        "duration_realization": {"status": "near_target"},
        "llm_metrics": {"llm_calls": 12, "llm_cache_hits": 3},
    }
    case = _collect(tmp_path, delivery=lambda _path, _db: {"immich_asset_id": "asset-1"}, plan=plan)
    assert case["status"] == "ready"
    assert case["plan"]["carrier_asset_ids"] == ["a1", "b2"]
    assert case["plan"]["favourite_asset_ids"] == ["a1"]
    assert case["plan"]["content_seconds"] == 55.5
    assert case["delivery"]["immich_asset_id"] == "asset-1"


def _case(ids, *, sha="new", content=50.0):
    return {
        "id": "monthly",
        "status": "ready",
        "plan": {"carrier_asset_ids": ids, "plan_sha256": sha, "content_seconds": content},
    }


@pytest.mark.parametrize(
    ("ids", "entry", "expected", "moved"),
    [
        (["a"], {"plan_sha256": "new", "carrier_asset_ids": ["a"]}, "IDENTICAL", (0, 0)),
        (["a", "b"], {"carrier_asset_ids": ["a", "b"]}, "SAME_CARRIERS", (0, 0)),
        (["a", "c"], {"carrier_asset_ids": ["a", "b"]}, "CHANGED", (1, 1)),
    ],
)
def test_the_verdict_says_whether_the_owners_grade_still_applies(ids, entry, expected, moved):
    verdict = report.compare(_case(ids), entry)
    assert verdict["verdict"] == getattr(report, expected)
    assert (verdict["added"], verdict["removed"]) == moved
    # No reference content seconds to subtract from, so nothing is claimed about the delta.
    assert verdict["content_delta_seconds"] is None


def test_favourite_retention_comes_from_the_reference_attempts_own_plan(tmp_path):
    attempt = tmp_path / "accepted"
    attempt.mkdir()
    carriers = [{"asset_id": "a", "favourite": True}, {"asset_id": "b", "favourite": True}]
    (attempt / "plan.private.json").write_text(
        json.dumps({"carriers": carriers, "content_seconds": 50.0})
    )
    verdict = report.compare(
        _case(["a", "c"], content=61.0), {"plan_sha256": "o", "attempt_path": str(attempt)}
    )
    assert (verdict["reference_favourites"], verdict["favourites_kept"]) == (2, 1)
    assert verdict["content_delta_seconds"] == 11.0


def test_a_route_with_no_reference_and_one_never_collected_are_told_apart():
    assert report.compare(_case(["a"]), None)["verdict"] == report.NO_REFERENCE
    assert report.compare({"id": "x", "status": "failed"}, None)["verdict"] == report.NOT_COLLECTED


def test_the_console_table_carries_counts_and_never_an_asset_id():
    case = _case(["a", "b"]) | {
        "film": {"seconds": 61.0, "path": "/out/monthly.mp4"},
        "delivery": {"immich_asset_id": "secret-asset-id"},
    }
    case["plan"] |= {
        "duration_realization": {"status": "near_target"},
        "llm_metrics": {"llm_calls": 12, "llm_cache_hits": 3},
    }
    rows = report.report_rows({"reference": {}, "cases": [case]})
    console = report.console_table(rows)
    assert "secret-asset-id" not in console
    assert "12/3" in console and "near_target" in console
    assert "secret-asset-id" in report.markdown_report({"album_name": "Matrix"}, rows)
