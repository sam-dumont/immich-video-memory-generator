"""Film-time enrichment belongs to the NAS draft, not its whole source pool (#1397)."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_description_contract import validate_envelope
from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
from immich_memories.analysis.editorial_preparation_captions import _remember_caption
from immich_memories.analysis.editorial_runtime import EditorialRunContext, build_editorial_planner
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.smart_pipeline import SmartPipeline
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.test_editorial_detector_frames import requires_ffmpeg
from tests.test_editorial_preparation import successful_ports
from tests.test_editorial_rule_reader import _distinct_preview
from tests.test_editorial_source_route import photo


def _model_effects(source, calls):
    from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_thin_layer import ThinPolish
    from tests.test_editorial_thin_polish_end_to_end import PolishJudge

    calls.append(("effects-tier", (source.config.tier,)))

    # WHY: replace the external prose answers; the runtime, NAS draft, thin layer,
    # gates and evidence bank still run. Canonical episode context has its own tests.
    def context(stories):
        ids = tuple(asset for members in stories.values() for asset in members)
        assert all(source.audience_annotations[asset].description for asset in ids)
        calls.append(("period-context", ids))
        return "A month of family moments.", {}

    return StructurePlannerPorts(
        judge=PolishJudge(),
        rules=RuleStructureReader(source),
        thumbnail_hash=lambda _asset: None,
        thin=ThinPolish(bank_dir=source.bank_dir, read_period=context),
    )


def _film(
    directory,
    sources,
    *,
    tier,
    descriptions=None,
    playback=None,
    playback_content=None,
    frame_reader=None,
):
    calls = []
    # WHY: local model providers are external boundaries. Keep acquisition, SQLite,
    # annotation reading and the complete production selector real.
    providers = successful_ports(calls)
    if frame_reader is not None:
        providers = replace(providers, clip_frames=frame_reader)
    if descriptions:

        def captions(**kwargs):
            calls.append(("captions", tuple(kwargs["asset_ids"])))
            for asset_id in kwargs["asset_ids"]:
                _remember_caption(
                    kwargs["connection"],
                    asset_id,
                    validate_envelope(
                        {
                            "description": descriptions.get(asset_id, "People sit together."),
                            "setting": "a room",
                        }
                    ),
                )
            return {}

        providers = replace(providers, captions=captions)

    def prepare(**kwargs):
        return prepare_editorial_annotations(**kwargs, ports=providers)

    def read_playback(_client, asset_id, _start, _length):
        if playback is not None:
            playback.add(asset_id)
        if playback_content is not None:
            return playback_content[_start : _start + _length], len(playback_content)
        raise OSError("The synthetic source has no playable clip")

    config = Config(
        tier=tier,
        llm={"model": "test-prose", "base_url": "http://prose.invalid/v1"}
        if tier == "full"
        else {},
        cache={"directory": str(directory / "cache")},
        analysis={"min_source_short_side": 0},
        editorial={
            "reader": "model" if tier == "full" else "rules",
            "laya_audience": False,
            "preparation": {"tier": "no_captions" if tier == "nas" else "full"},
        },
    )
    window = DateRange(datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 2, 29, 23, 59, tzinfo=UTC))
    # WHY: the Immich transport supplies synthetic metadata and generated previews;
    # the requested film goes through the same public pipeline as the CLI and UI.
    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=ThumbnailCache(directory / "thumbnails"),
        context=EditorialRunContext(
            "month", "A month", "monthly_highlights", (window,), 60, directory / "artifacts"
        ),
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=lambda *_: sources,
            fetch_preview=lambda _client, key: _distinct_preview(key),
            fetch_playback_range=read_playback,
            prepare_annotations=prepare,
            structure_ports_factory=(lambda source: _model_effects(source, calls))
            if tier == "full"
            else None,
        ),
    )
    _, result = SmartPipeline(planner=planner).run_editorial_source(sources)
    return {clip.asset.id for clip in result.selected_clips}, calls


def test_full_refinement_reuses_gpu_captions_instead_of_recaptioning(tmp_path):
    first = datetime(2024, 2, 1, 12, tzinfo=UTC)
    sources = [photo(f"picture-{n:02}", at=first + timedelta(days=n)) for n in range(24)]
    for asset in sources:
        asset.is_favorite = True
    gpu, _ = _film(tmp_path, sources, tier="gpu")

    full, calls = _film(tmp_path, sources, tier="full")

    assert full == gpu
    assert [ids for producer, ids in calls if producer == "effects-tier"] == [("nas",), ("full",)]
    assert any(producer == "period-context" for producer, _ in calls)
    assert not any(producer == "captions" for producer, _ in calls)


def test_an_empty_nas_draft_does_not_request_refinement(tmp_path):
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.test_editorial_duration_planner_integration import source

    captured = source(tmp_path, seconds=60, pictures=3)
    captured = replace(
        captured,
        annotations=dict.fromkeys(
            captured.assets, "2020-05-02 | A screenshot showing a dashboard."
        ),
    )

    # WHY: an expensive refinement request must never be made for an empty cut.
    def forbidden(*_):
        raise AssertionError("empty NAS draft requested refinement")

    result = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            rules=RuleStructureReader(captured),
            thumbnail_hash=lambda _: None,
            refine=forbidden,
        ),
    )

    assert result.plan["carriers"] == []


def test_gpu_captions_the_nas_selection_before_considering_changes(tmp_path):
    first = datetime(2024, 2, 1, 12, tzinfo=UTC)
    sources = [photo(f"picture-{n:02}", at=first + timedelta(days=n)) for n in range(24)]
    for asset in sources:
        asset.is_favorite = True
    draft, _ = _film(tmp_path / "nas", sources, tier="nas")
    assert 0 < len(draft) < len(sources)

    refined, calls = _film(tmp_path / "gpu", sources, tier="gpu")

    captioned = {asset_id for producer, ids in calls if producer == "captions" for asset_id in ids}
    assert captioned == draft
    assert refined == draft
    repeated, warm_calls = _film(tmp_path / "gpu", sources, tier="gpu")
    assert repeated == refined
    assert not any(producer == "captions" for producer, _ in warm_calls)


def test_a_refinement_replacement_is_captioned_before_it_enters_the_film(tmp_path):
    first = datetime(2024, 2, 1, 12, tzinfo=UTC)
    sources = [
        photo(f"picture-{day:02}-{member}", at=first + timedelta(days=day, seconds=member * 15))
        for day in range(24)
        for member in range(2)
    ]
    for asset in sources:
        asset.is_favorite = True
    draft, _ = _film(tmp_path / "nas", sources, tier="nas")
    held = min(draft)

    refined, calls = _film(
        tmp_path / "gpu",
        sources,
        tier="gpu",
        descriptions={held: "A screenshot showing a dashboard."},
    )

    assert held not in refined
    replacements = refined - draft
    assert replacements
    captioned = {asset_id for producer, ids in calls if producer == "captions" for asset_id in ids}
    assert replacements <= captioned

    reports = [
        json.loads(path.read_text())
        for path in sorted(
            (tmp_path / "gpu" / "artifacts").glob("**/refinement/*/preparation.private.json")
        )
    ]
    assert len(reports) >= 2
    assert set(reports[0]["requested_asset_ids"]) == draft
    assert {asset for report in reports for asset in report["requested_asset_ids"]} == captioned


def test_nas_inspects_only_the_live_clips_its_draft_can_use(tmp_path):
    first = datetime(2024, 2, 1, 12, tzinfo=UTC)
    sources = [
        photo(f"picture-{n:02}", at=first + timedelta(days=n), live=f"clip-{n:02}")
        for n in range(24)
    ]
    for asset in sources:
        asset.is_favorite = True
    playback = set()

    selected, _ = _film(tmp_path / "nas", sources, tier="nas", playback=playback)

    assert 0 < len(selected) < len(sources)
    assert playback == {asset.live_photo_video_id for asset in sources if asset.id in selected}


@requires_ffmpeg
def test_fresh_video_frame_facts_are_applied_before_the_nas_cut_ships(tmp_path):
    from immich_memories.analysis.editorial_clip_frames import clip_frames_fact
    from immich_memories.cache.embedding_cache import HeadFactStore
    from tests.conftest import make_clip
    from tests.test_playback_keyframes import encode

    first = datetime(2024, 2, 1, 12, tzinfo=UTC)
    sources = [
        make_clip(f"video-{n:02}", file_created_at=first + timedelta(days=n)).asset
        for n in range(24)
    ]
    content = encode(tmp_path / "clip.mp4", gop=30)

    def frames(kind):
        # WHY: the frame classifier is external; real acquisition and FFmpeg supply
        # its sampled frames, and the stored facts must affect the actual finished cut.
        def read(**kwargs):
            store = HeadFactStore(kwargs["store_path"])
            try:
                for asset_id in kwargs["frame_paths"]:
                    store.remember_facts(
                        asset_id, [clip_frames_fact([kind] * 8)], encoder_key="test"
                    )
            finally:
                store.close()
            return {}

        return read

    baseline, _ = _film(
        tmp_path / "good",
        sources,
        tier="nas",
        playback_content=content,
        frame_reader=frames("people_moment"),
    )
    assert baseline

    rejected, _ = _film(
        tmp_path / "bad",
        sources,
        tier="nas",
        playback_content=content,
        frame_reader=frames("accidental_or_blurred_frame"),
    )

    assert not rejected
