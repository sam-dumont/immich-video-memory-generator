"""Laya answering the audience check's activity question, behind `editorial.laya_audience`."""

from __future__ import annotations

import io
import json
import sys
import tarfile

from click.testing import CliRunner

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_laya_reader import (
    AUDIENCE_FINDINGS,
    LayaReader,
    laya_reader_for,
    unpack_checkpoint,
)
from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate
from immich_memories.config_models_editorial import EditorialConfig
from tests.editorial_thin_fixtures import PRIVATE, CountingJudge

FINDINGS = list(AUDIENCE_FINDINGS)


class StubScorer:
    """# WHY: replaces the MLX model at the process boundary (laya-mlx and an 811 MB checkpoint
    are not in CI). A bath caption scores as a bathing hold, anything else as `none`; it records
    every state it was shown so a test can see what Laya read."""

    def __init__(self) -> None:
        self.states: list[str] = []

    def probabilities(self, states, question):
        self.states += states
        out = []
        for state in states:
            p = [0.0] * len(FINDINGS)
            if PRIVATE in state:
                p[FINDINGS.index("bathing")] = 0.6
                p[FINDINGS.index("nudity_shirtless_or_underwear")] = 0.3
                p[FINDINGS.index("none")] = 0.1
            else:
                p[FINDINGS.index("none")] = 0.99
                p[FINDINGS.index("nudity_shirtless_or_underwear")] = 0.01
            out.append(p)
        return out


def gate(tmp_path, annotations, scorer):
    lines = {
        a: f"2024-02-01T09:0{i} | {n.description}" for i, (a, n) in enumerate(annotations.items())
    }
    judge = CountingJudge()
    return judge, AudienceGate(
        judge,
        audience="family",
        annotations=annotations,
        flag_rows={},
        lines=lines,
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|model-a|laya"),
        activity_reader=LayaReader(scorer, threshold=0.186).activity_answers,
    )


def line(asset_id, caption, **heads):
    return AssetAnnotationLine(asset_id, f"09:00 | {caption}", caption, tuple(heads.items()))


def test_laya_holds_a_private_carrier_and_the_text_model_is_never_asked(tmp_path):
    annotations = {"a1": line("a1", "people at a table"), "a2": line("a2", PRIVATE)}
    judge, audience = gate(tmp_path, annotations, StubScorer())
    units = [{"asset_id": a, "kind": "still"} for a in annotations]

    audience.prefetch(units, batch=12)

    assert {u["asset_id"]: audience.verdict_of(u) for u in units} == {
        "a1": "share",
        "a2": "just_us",  # a bath: the household only
    }
    assert not [stage for stage in judge.calls if "activity" in stage]


def test_a_detector_hold_stands_when_laya_answers_none(tmp_path):
    annotations = {"a1": line("a1", "a man on a beach", nsfw_marqo="yes")}
    _judge, audience = gate(tmp_path, annotations, StubScorer())
    unit = {"asset_id": "a1", "kind": "still"}

    audience.prefetch([unit], batch=12)

    assert audience.verdict_of(unit) != "share"


def test_laya_reads_the_compact_caption(tmp_path):
    annotations = {"a1": line("a1", PRIVATE)}
    scorer = StubScorer()
    _judge, audience = gate(tmp_path, annotations, scorer)
    unit = {"asset_id": "a1", "kind": "still"}

    audience.prefetch([unit], batch=12)

    assert scorer.states == [PRIVATE]


def test_the_nudity_finding_is_never_answered_where_the_check_may_not_ask_it():
    allowed = json.loads(
        LayaReader(StubScorer(), threshold=0.1).activity_answers({"k": ([PRIVATE], True)})["k"]
    )
    barred = json.loads(
        LayaReader(StubScorer(), threshold=0.95).activity_answers({"k": ([PRIVATE], False)})["k"]
    )

    assert allowed["finding"] == "bathing"
    # without nudity the hold mass is 0.6, under this threshold, so nothing is held
    assert barred["finding"] == "none"


def test_a_carrier_with_no_caption_is_left_to_the_text_model():
    assert LayaReader(StubScorer(), threshold=0.1).activity_answers({"k": ([""], True)}) == {}


def test_laya_is_off_by_default_and_a_missing_checkpoint_degrades_naming_the_fetch(
    tmp_path, caplog
):
    assert laya_reader_for(EditorialConfig()) is None
    config = EditorialConfig(laya_audience=True, laya_checkpoint=str(tmp_path / "absent.tar"))

    assert laya_reader_for(config) is None
    assert "models fetch" in caplog.text


def test_a_missing_laya_runtime_degrades_naming_the_install(monkeypatch, tmp_path, caplog):
    archive = tmp_path / "laya.tar"
    archive.write_bytes(b"")
    # WHY: stands in for an install without laya-mlx, which CI never has anyway.
    monkeypatch.setitem(sys.modules, "laya_mlx", None)

    assert (
        laya_reader_for(EditorialConfig(laya_audience=True, laya_checkpoint=str(archive))) is None
    )
    assert "laya-mlx" in caplog.text


def test_the_checkpoint_archive_is_unpacked_once(tmp_path):
    archive = tmp_path / "laya.tar"
    with tarfile.open(archive, "w") as bundle:
        data = b"weights"
        info = tarfile.TarInfo("model.safetensors")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))

    unpacked = unpack_checkpoint(archive, tmp_path / "laya")
    archive.unlink()

    assert unpack_checkpoint(archive, tmp_path / "laya") == unpacked
    assert (unpacked / "model.safetensors").read_bytes() == b"weights"


def test_a_checkpoint_member_that_climbs_out_of_its_folder_is_not_written(tmp_path):
    archive = tmp_path / "laya.tar"
    with tarfile.open(archive, "w") as bundle:
        for name in ("../escaped.txt", "model.safetensors"):
            info = tarfile.TarInfo(name)
            info.size = 1
            bundle.addfile(info, io.BytesIO(b"x"))

    unpack_checkpoint(archive, tmp_path / "laya")

    assert not (tmp_path / "escaped.txt").exists()


def _fetch_models(monkeypatch, tmp_path, editorial, *flags):
    from immich_memories.cli import models_cmd

    fetched = []

    def fake_fetch(**kwargs):
        # WHY: replaces the network download (a write to disk from GitHub releases).
        fetched.append(kwargs)
        return "downloaded"

    monkeypatch.setattr(models_cmd, "fetch_pinned_model", fake_fetch)
    config = type("C", (), {})()
    config.triage = type("T", (), {"encoder_url": "u", "encoder_path": tmp_path / "e"})()
    config.editorial = editorial
    import click

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj = {"config": config}

    models_cmd.register_models_commands(cli)
    result = CliRunner().invoke(cli, ["models", "fetch", "--no-detectors", *flags])
    assert result.exit_code == 0, result.output
    return fetched


def test_models_fetch_laya_downloads_the_pinned_checkpoint(monkeypatch, tmp_path):
    from immich_memories.pinned_models import LAYA_AUDIENCE, LAYA_MAX_BYTES

    editorial = EditorialConfig(
        laya_checkpoint=str(tmp_path / "laya.tar"), laya_checkpoint_url=LAYA_AUDIENCE.url
    )
    fetched = _fetch_models(monkeypatch, tmp_path, editorial, "--laya")

    laya = next(call for call in fetched if call["sha256"] == LAYA_AUDIENCE.sha256)
    assert laya["destination"] == tmp_path / "laya.tar"
    assert laya["max_bytes"] == LAYA_MAX_BYTES


def test_models_fetch_on_a_tier_with_laya_fetches_it_unasked(monkeypatch, tmp_path):
    from immich_memories.pinned_models import LAYA_AUDIENCE

    on = EditorialConfig(
        laya_audience=True,
        laya_checkpoint=str(tmp_path / "laya.tar"),
        laya_checkpoint_url=LAYA_AUDIENCE.url,
    )
    off = EditorialConfig(
        laya_checkpoint=str(tmp_path / "laya.tar"), laya_checkpoint_url=LAYA_AUDIENCE.url
    )

    assert any(
        c["sha256"] == LAYA_AUDIENCE.sha256 for c in _fetch_models(monkeypatch, tmp_path, on)
    )
    assert not any(
        c["sha256"] == LAYA_AUDIENCE.sha256 for c in _fetch_models(monkeypatch, tmp_path, off)
    )


def _fake_laya_mlx(monkeypatch):
    """# WHY: laya-mlx and mlx are installed out of band on Apple silicon and are not in CI; these
    stand-ins give the scorer a tokenizer that counts words and logits that favour option 1."""
    import sys
    import types

    import numpy as np

    class Array(np.ndarray):
        def astype(self, _dtype):
            return np.asarray(self)

    class Agent:
        def __init__(self, path, dtype, batch_size):
            self.path, self.batch_size = path, batch_size
            self.tok = types.SimpleNamespace(pad_token_id=0)
            self.cfg = {"max_len": 64, "head_max_len": 16}
            self.batches = []

        def forward(self, batch):
            self.batches.append(batch)
            logits = np.zeros((len(batch), len(FINDINGS)))
            logits[:, 1] = 4.0
            return (logits.view(Array),)

    def build_sequence(_tok, state, question, _max_len, _head):
        return state.split(), list(range(len(question["crit"])))

    common = types.ModuleType("laya_mlx.common")
    common.QTYPES = {"choice": 0}
    common.build_sequence = build_sequence
    agent = types.ModuleType("laya_mlx.agent")
    agent.Agent = Agent
    agent.collate_items = lambda items, _pad: items
    core = types.ModuleType("mlx.core")
    core.float32 = "float32"
    for name, module in {
        "laya_mlx": types.ModuleType("laya_mlx"),
        "laya_mlx.agent": agent,
        "laya_mlx.common": common,
        "mlx": types.ModuleType("mlx"),
        "mlx.core": core,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_the_mlx_scorer_returns_one_probability_row_per_state_in_the_order_given(
    monkeypatch, tmp_path
):
    from immich_memories.analysis.editorial_laya_reader import AUDIENCE_QUESTION, MlxLayaScorer

    _fake_laya_mlx(monkeypatch)
    scorer = MlxLayaScorer(tmp_path, batch_size=2)

    rows = scorer.probabilities(
        ["a much longer caption here", "short", "mid length"], AUDIENCE_QUESTION
    )

    assert len(rows) == 3
    assert all(abs(sum(r) - 1) < 1e-9 and max(r) == r[1] for r in rows)
    # shortest first, two per forward pass
    assert [len(b) for b in scorer._agent.batches] == [2, 1]


def test_a_configured_checkpoint_gives_a_reader_at_the_configured_threshold(monkeypatch, tmp_path):
    _fake_laya_mlx(monkeypatch)
    archive = tmp_path / "laya.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo("model.safetensors")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    config = EditorialConfig(laya_audience=True, laya_checkpoint=str(archive))

    reader = laya_reader_for(config)

    answers = reader.activity_answers({"k": (["a caption"], True)})
    assert json.loads(answers["k"])["finding"] == FINDINGS[1]
    assert (tmp_path / "laya" / "model.safetensors").is_file()
