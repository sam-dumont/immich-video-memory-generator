"""The same library must produce the same candidates, run after run.

The budget collects asset ids into a set[str] and the candidate list is built
by iterating it. Python randomises string hashing per process, so the order
differs between runs — and the cap that follows trims by resolution with a
stable sort, which leaves equal-resolution clips in that arbitrary order. On a
library where nearly every clip is 1920x1080 that makes "which clips get
analyzed" a coin flip on identical input.

It is a defect on its own, and it also makes any A/B measurement of selection
meaningless: two runs differ before anything under test has been touched.
"""

from types import SimpleNamespace

from immich_memories.analysis.smart_pipeline import _cap_analysis_candidates


def _clip(asset_id: str, width: int = 1920, height: int = 1080, favorite: bool = False):
    return SimpleNamespace(
        asset=SimpleNamespace(id=asset_id, is_favorite=favorite),
        width=width,
        height=height,
    )


def test_equal_resolution_clips_are_trimmed_the_same_way_whatever_the_input_order() -> None:
    """Input order must not decide who survives the cap."""
    clips = [_clip(f"asset-{i:02d}") for i in range(12)]

    forwards = _cap_analysis_candidates(list(clips), target_clips=4)
    backwards = _cap_analysis_candidates(list(reversed(clips)), target_clips=4)

    assert [c.asset.id for c in forwards] == [c.asset.id for c in backwards], (
        "the same clips at the same resolution were trimmed differently "
        "depending on the order they arrived in"
    )


def test_higher_resolution_still_wins() -> None:
    """The rule the cap exists for is unchanged."""
    clips = [_clip("small", 640, 480), _clip("large", 3840, 2160), _clip("mid", 1920, 1080)]

    kept = _cap_analysis_candidates(clips, target_clips=1)

    assert [c.asset.id for c in kept] == ["large"]


def test_favorites_are_still_never_trimmed() -> None:
    """Starting with ALL favorites is the selection's oldest contract."""
    clips = [_clip(f"fav-{i}", favorite=True) for i in range(3)]
    clips += [_clip(f"plain-{i}") for i in range(9)]

    kept = _cap_analysis_candidates(clips, target_clips=2)

    assert {c.asset.id for c in kept} >= {"fav-0", "fav-1", "fav-2"}


def test_the_candidate_set_survives_a_different_hash_seed(tmp_path) -> None:
    """The real failure mode: it only shows up across processes.

    Python fixes its string-hash seed at interpreter start, so a same-process
    test cannot see this. Two subprocesses with different PYTHONHASHSEED can.
    """
    import os
    import subprocess
    import sys

    script = tmp_path / "candidates.py"
    script.write_text(
        "from types import SimpleNamespace\n"
        "from immich_memories.analysis.smart_pipeline import _cap_analysis_candidates\n"
        "ids = {f'asset-{i:02d}' for i in range(40)}\n"
        "clips = {a: SimpleNamespace(asset=SimpleNamespace(id=a, is_favorite=False),"
        " width=1920, height=1080) for a in ids}\n"
        "chosen = [clips[a] for a in sorted(ids)]\n"
        "print(','.join(c.asset.id for c in _cap_analysis_candidates(chosen, target_clips=6)))\n"
    )

    def _run(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        # WHY a subprocess: the seed is fixed before the interpreter starts,
        # so there is no in-process way to vary it.
        return subprocess.run(  # noqa: S603
            [sys.executable, str(script)], capture_output=True, text=True, env=env, check=True
        ).stdout.strip()

    assert _run("1") == _run("7") == _run("12345"), "the candidate set moved with the hash seed"
