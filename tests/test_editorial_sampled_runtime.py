"""Production and sealed-matrix composition use the same conserved pair gateway.

The matrix replays stay on the probe branch.

The matrix replays stay on the probe branch.

The matrix replays stay on the probe branch.
"""

import json
import socket
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from immich_memories.analysis import editorial_runtime as runtime
from immich_memories.analysis.selection_trace import Trace
from immich_memories.config_loader import Config
from tests.test_editorial_duration_planner_integration import source


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("runtime composition test attempted network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def captured(tmp_path):
    value = source(tmp_path, seconds=60, pictures=3)
    # The captured context includes an extra asset; it is not selectable material.
    moment, members = next(iter(value.moment_asset_ids.items()))
    return replace(
        value,
        config=Config(llm={"model": "controlled-vision"}),
        moment_asset_ids={moment: members[:2]},
    )


def backend(value, store, planner):
    context = runtime.EditorialRunContext(
        value.case.key,
        value.case.label,
        value.case.product,
        value.case.ranges,
        value.case.target_seconds,
        value.artifact_dir,
    )
    return runtime.ProductionPostCardBackend(
        config=value.config,
        context=context,
        people={},
        thumbnail_cache=object(),
        store_path=store,
        ports=runtime.EditorialRuntimePorts(structure_planner=planner),
    )


def matrix_run(monkeypatch, tmp_path, value, store, planner, previews):
    from scripts.matrix27 import plan_structure_v18 as matrix

    wall = tmp_path / "wall"
    wall.mkdir(exist_ok=True)
    (wall / matrix.wall_sources.SIDECAR_NAME).write_text("{}")
    (wall / "production-moment-wall.tsv").write_bytes(value.wall_bytes)
    (wall / "manifest.private.json").write_text(
        json.dumps(
            {
                "inputs": {
                    "eligible_ids_sha256": matrix.card_probe._json_sha256(tuple(value.assets))
                },
            }
        )
    )
    alias_file = wall / "moment-alias-map.private.tsv"
    import csv

    with alias_file.open("w") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("alias", "selectable_asset_ids_json"), delimiter="\t"
        )
        writer.writeheader()
        for alias, ids in value.moment_asset_ids.items():
            writer.writerow({"alias": alias, "selectable_asset_ids_json": json.dumps(ids)})
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"key": value.case.key}]}))
    with sqlite3.connect(store) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS assets (asset_id TEXT, latitude REAL, longitude REAL)"
        )
    trace = Trace()
    prepared = SimpleNamespace(
        candidate_ids=tuple(value.assets),
        trace=trace,
        candidates=tuple(SimpleNamespace(asset_id=k, source=a) for k, a in value.assets.items()),
    )
    batch = SimpleNamespace(
        as_mapping=lambda: value.annotations, lines=tuple(value.audience_annotations.values())
    )
    monkeypatch.setattr(matrix, "get_config", lambda **_: value.config)
    monkeypatch.setattr(matrix.evaluation, "_validate_qwen_config", lambda *_: None)
    monkeypatch.setattr(matrix.card_probe, "_load_case", lambda *_: value.case)
    monkeypatch.setattr(matrix.wall_sources, "install_sealed_fetch", lambda *_: None)
    monkeypatch.setattr(
        matrix.card_probe,
        "_preflight",
        lambda *_: (tuple(value.assets.values()), None, prepared, {}, batch, None, None),
    )
    monkeypatch.setattr(
        matrix.provenance,
        "sealed_period_insight",
        lambda *_: SimpleNamespace(
            lineage=lambda: {},
            structure_evidence=lambda: value.period_evidence,
        ),
    )
    monkeypatch.setattr(
        matrix.kit_identity, "kit_identity", lambda: {"kit_sha256": "offline-fixture"}
    )
    monkeypatch.setattr(matrix, "read_pixel_facts", lambda *_: {})
    monkeypatch.setattr(matrix, "load_flags", lambda *_: {})
    monkeypatch.setattr(matrix, "production_motion_resolver", lambda *_, **__: None)
    monkeypatch.setattr(matrix.wp, "_thumbnail_path", lambda key: previews[key])
    monkeypatch.setattr(
        matrix,
        "EditorialRuntimePorts",
        lambda **kw: runtime.EditorialRuntimePorts(structure_planner=planner, **kw),
    )
    for key, val in {
        "CASE_KEY": value.case.key,
        "LABEL": value.case.label,
        "TARGET_SECONDS": 60,
        "CASES": cases,
        "STORE": store,
        "WALL_DIR": wall,
        "WALL": wall / "production-moment-wall.tsv",
        "ALIAS_MAP": alias_file,
        "OUT": value.artifact_dir,
        "PRIOR_PLAN": None,
    }.items():
        monkeypatch.setattr(matrix, key, val)
    monkeypatch.setenv("MATRIX27_TEXT_CACHE", str(store))
    monkeypatch.setenv("MATRIX27_BANK_DIR", str(value.bank_dir))
    monkeypatch.delenv("MATRIX27_MOTION_REPLAY_PATH", raising=False)
    monkeypatch.delenv("MATRIX27_MOTION_REPLAY_SHA256", raising=False)
    matrix.main()
    return trace
