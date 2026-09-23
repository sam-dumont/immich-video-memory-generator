"""Auto duration after discovery: the same pool gets the same length on both surfaces.

``test_surface_parity.py`` compares what each surface asks for before it has
seen a single picture. The length a film actually runs is decided later, from
the material discovery found (#1087, #1094), so this file hands both surfaces
the same discovered media and compares the length each one settles on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.cli._pipeline_runner import _decide_duration
from immich_memories.config import Config
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
from immich_memories.planning.auto_duration import (
    DURATION_FROM_DURATION_FLAG,
    DURATION_FROM_MATERIAL,
)
from immich_memories.ui.pages._step4_generate import _build_generation_params
from immich_memories.ui.pages.clip_pipeline import (
    _build_ui_editorial_context,
    _resolve_auto_duration_for_selection,
)
from immich_memories.ui.state import AppState
from tests.test_surface_parity import SPECS, cli_duration


def _asset(asset_id: str, when: datetime, asset_type: AssetType) -> Asset:
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )


def _clip(asset_id: str, when: datetime) -> VideoClipInfo:
    return VideoClipInfo(
        asset=_asset(asset_id, when, AssetType.VIDEO),
        duration_seconds=12.0,
        width=1920,
        height=1080,
    )


def _pool(
    first_day: datetime, days: int, *, clips_a_day: int, photos_a_day: int
) -> tuple[list[VideoClipInfo], list[Asset]]:
    clips: list[VideoClipInfo] = []
    photos: list[Asset] = []
    for day in range(days):
        when = first_day + timedelta(days=day, hours=12)
        clips.extend(_clip(f"v-{day}-{index}", when) for index in range(clips_a_day))
        photos.extend(
            _asset(f"p-{day}-{index}", when, AssetType.IMAGE) for index in range(photos_a_day)
        )
    return clips, photos


MARCH = datetime(2024, 3, 1, tzinfo=UTC)
# Every day of March photographed: far more than a one-minute film can hold.
DENSE_MONTH = _pool(MARCH, 31, clips_a_day=2, photos_a_day=6)
# Three stills on three days: nowhere near a minute of varied footage.
THIN_MONTH = _pool(MARCH, 3, clips_a_day=0, photos_a_day=1)
TRIP = _pool(datetime(2024, 7, 1, tzinfo=UTC), 10, clips_a_day=3, photos_a_day=4)


def cli_auto_seconds(
    memory_type: MemoryType,
    pool: tuple[list[VideoClipInfo], list[Asset]],
    *,
    config: Config,
    duration: float | None = None,
) -> float:
    """The length ``run_pipeline_and_generate`` settles on for this pool."""
    clips, photos = pool
    decision = _decide_duration(
        duration,
        requested_source=DURATION_FROM_DURATION_FLAG
        if duration is not None
        else DURATION_FROM_MATERIAL,
        preset_duration=cli_duration(memory_type, SPECS[memory_type]),
        memory_type=str(memory_type),
        clips=clips,
        photos=photos,
        config=config,
    )
    return decision.seconds


def ui_state(memory_type: MemoryType, *, config: Config) -> AppState:
    """The wizard once a card has been filled in, as step1_presets leaves it."""
    state = AppState(config=config, include_photos=True)
    state.choose_memory_type(str(memory_type))
    state.apply_preset(create_preset(memory_type, **SPECS[memory_type].as_preset_params()))
    return state


def ui_auto_seconds(state: AppState, pool: tuple[list[VideoClipInfo], list[Asset]]) -> float:
    """The length the Memory page hands the cut for this reviewed pool."""
    clips, photos = pool
    _resolve_auto_duration_for_selection(state, clips, photos)
    return state.target_duration_seconds


class TestAutoDurationAfterDiscovery:
    """Auto means the same thing whichever surface started the run."""

    @pytest.mark.parametrize(
        ("memory_type", "pool"),
        [
            pytest.param(MemoryType.MONTHLY_HIGHLIGHTS, DENSE_MONTH, id="dense-month"),
            pytest.param(MemoryType.MONTHLY_HIGHLIGHTS, THIN_MONTH, id="thin-month"),
            pytest.param(MemoryType.TRIP, TRIP, id="trip"),
        ],
    )
    def test_the_same_pool_gets_the_same_length(self, memory_type, pool) -> None:
        config = Config()

        cli = cli_auto_seconds(memory_type, pool, config=config)
        ui = ui_auto_seconds(ui_state(memory_type, config=config), pool)

        assert cli == ui

    def test_a_thin_month_is_shortened_on_both_surfaces(self) -> None:
        """The case the wizard used to miss: it kept asking for the card's minute."""
        config = Config()

        ui = ui_auto_seconds(ui_state(MemoryType.MONTHLY_HIGHLIGHTS, config=config), THIN_MONTH)

        assert ui < 60.0
        assert ui == cli_auto_seconds(MemoryType.MONTHLY_HIGHLIGHTS, THIN_MONTH, config=config)

    def test_an_explicit_target_wins_on_both_surfaces(self) -> None:
        """Manual on the Memory page is ``--duration`` on the CLI: the pool does not move it."""
        config = Config()
        state = ui_state(MemoryType.MONTHLY_HIGHLIGHTS, config=config)
        state.duration_mode = "manual"
        state.target_duration = 2.5

        ui = ui_auto_seconds(state, THIN_MONTH)
        cli = cli_auto_seconds(
            MemoryType.MONTHLY_HIGHLIGHTS, THIN_MONTH, config=config, duration=150.0
        )

        assert ui == cli == 150.0

    def test_a_fuller_pool_grows_back_to_the_cards_length(self) -> None:
        """A second cut is fitted from the card's ask, not from the last shortened answer."""
        config = Config()
        state = ui_state(MemoryType.MONTHLY_HIGHLIGHTS, config=config)

        ui_auto_seconds(state, THIN_MONTH)
        again = ui_auto_seconds(state, DENSE_MONTH)

        assert again == cli_auto_seconds(MemoryType.MONTHLY_HIGHLIGHTS, DENSE_MONTH, config=config)


def test_the_fitted_length_reaches_the_cut_and_the_render(tmp_path) -> None:
    """The story-first editor, the run record and the final render all read the fit."""
    config = Config(cache={"directory": str(tmp_path / "cache")})
    state = ui_state(MemoryType.MONTHLY_HIGHLIGHTS, config=config)
    clips, photos = THIN_MONTH
    fitted = ui_auto_seconds(state, THIN_MONTH)

    context = _build_ui_editorial_context(state, config, clips, photos)
    # WHY: the builder imports the Immich client; nothing here may reach a server.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert context.target_seconds == fitted
    assert context.duration_source == DURATION_FROM_MATERIAL
    assert context.render_timing.target_seconds == fitted
    assert params.target_duration_seconds == fitted
