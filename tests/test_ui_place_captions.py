"""The wizard can caption clips with their place, like the CLI's --add-place.

Last row of #505's asymmetry list: `add_place_overlay` reaches assembly from
every CLI path and from nowhere in the wizard, so the feature existed for one
surface only.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.config_loader import Config
from immich_memories.ui.state import AppState


def test_the_wizard_hands_the_place_caption_choice_to_the_pipeline(tmp_path: Path) -> None:
    """Ticking the box has to reach params, or the option does nothing."""
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="k",
        generation_options={"add_place": True},
    )

    params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.add_place_overlay is True


def test_place_captions_stay_off_unless_asked(tmp_path: Path) -> None:
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="k",
        generation_options={},
    )

    params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.add_place_overlay is False
