"""The three things the runner has to wait for, and what it does when they never come.

All three were found on a real cluster. `kubectl wait` on a label selector that
matches nothing exits 1 with `error: no matching resources found`, and the pod is
made a moment after `apply` returns, so the scheduling wait lost the race and
ended the cell. A Job that FAILS never satisfies `kubectl wait
--for=condition=complete`, so the runner sat on a dead cell for three hours. And a
Deployment that is Available is not a service that can answer: the pod was up
while every `/facts` request came back 503 because the models were not in its
cache yet, which a cell then measured as its own slowness.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_matrix  # noqa: E402
import setup_matrix_readiness  # noqa: E402
from setup_matrix_plan import Cell, CellPlan, Plan, Step  # noqa: E402


def _plan(item: CellPlan) -> Plan:
    return Plan(
        library="demo",
        month="2024-06",
        image="image:tag",
        anonymize_required=False,
        cells=(item,),
    )


def _job_cell(step: Step) -> CellPlan:
    cell = Cell(
        id="k8s-job",
        lane="k8s",
        reader="rules",
        facts="service",
        tier="no_captions",
        why="",
        requires_env=(),
        config={},
        inference_overlay=False,
    )
    return CellPlan(
        cell=cell,
        pins={},
        config_yaml="",
        steps=(step,),
        manifests={},
        app_credentials=(),
        cache_dir="",
    )


def _fake_kubectl(tmp_path: Path, answers: list[str]) -> None:
    """A kubectl that answers each poll with the next line, so a Job can change state."""
    (tmp_path / "answers").write_text("\n".join(answers) + "\n")
    script = tmp_path / "kubectl"
    script.write_text(
        "#!/bin/sh\n"
        f'count="{tmp_path}/calls"\n'
        'n=$(cat "$count" 2>/dev/null || echo 0)\n'
        "n=$((n + 1))\n"
        'echo "$n" > "$count"\n'
        f'sed -n "${{n}}p" "{tmp_path}/answers"\n'
    )
    script.chmod(0o755)


def _pod_step() -> Step:
    return Step("wait-created", ("kubectl", "get", "pod", "-l", "job-name=j", "-o", "name"))


def test_the_cell_waits_for_the_pod_the_scheduling_wait_will_wait_on(monkeypatch, tmp_path) -> None:
    """`kubectl get` prints nothing and exits 0 until the controller has made one."""
    _fake_kubectl(tmp_path, ["", "", "pod/k8s-job-lxvhw"])
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "POD_POLL_S", 0.0)
    item = _job_cell(_pod_step())

    record = setup_matrix.run_remote_cell(item, _plan(item), tmp_path / "out")

    assert record["error"] is None, "it gave up on the empty answer the old wait died on"
    assert (tmp_path / "calls").read_text().strip() == "3"
    said = (tmp_path / "out" / "k8s-job" / "wait-created.stdout.log").read_text()
    assert "pod/k8s-job-lxvhw" in said


def test_a_job_that_never_makes_a_pod_ends_the_cell_rather_than_hanging(
    monkeypatch, tmp_path
) -> None:
    _fake_kubectl(tmp_path, ["", ""])
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "POD_POLL_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "POD_TIMEOUT_S", 0.0)
    item = _job_cell(_pod_step())

    record = setup_matrix.run_remote_cell(item, _plan(item), tmp_path / "out")

    assert record["error"] == "wait-created exited 1"
    assert (
        "created no pod" in (tmp_path / "out" / "k8s-job" / "wait-created.stderr.log").read_text()
    )


def test_a_job_that_fails_ends_the_cell_instead_of_waiting_out_the_ceiling(
    monkeypatch, tmp_path
) -> None:
    _fake_kubectl(tmp_path, ["succeeded= failed=", "succeeded= failed=1"])
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "JOB_POLL_S", 0.0)
    item = _job_cell(Step("wait", ("kubectl", "get", "job", "j", "-o", "jsonpath=x")))

    record = setup_matrix.run_remote_cell(item, _plan(item), tmp_path / "out")

    assert record["error"] == "wait exited 1"
    said = (tmp_path / "out" / "k8s-job" / "wait.stderr.log").read_text()
    assert "failed=1" in said


def test_a_job_that_completes_lets_the_cell_carry_on(monkeypatch, tmp_path) -> None:
    _fake_kubectl(tmp_path, ["succeeded= failed=", "succeeded=1 failed="])
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "JOB_POLL_S", 0.0)
    item = _job_cell(Step("wait", ("kubectl", "get", "job", "j", "-o", "jsonpath=x")))

    record = setup_matrix.run_remote_cell(item, _plan(item), tmp_path / "out")

    assert record["error"] is None


class _FactsHandler(BaseHTTPRequestHandler):
    """A service that is up before it can decide: 503 until its models are loaded."""

    answers: list[tuple[int, bytes]] = []
    asked: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        self.asked.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        status, body = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@contextmanager
def _facts_service(answers: list[tuple[int, bytes]]) -> Iterator[tuple[str, list[bytes]]]:
    asked: list[bytes] = []
    handler = type("_Answers", (_FactsHandler,), {"answers": list(answers), "asked": asked})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", asked
    finally:
        server.shutdown()
        server.server_close()


_NOT_READY = (503, b'{"detail": "heads: the model is not in the cache yet"}')
_READY = (200, b'{"producers": {}}')


def test_the_warm_up_keeps_asking_until_the_service_can_actually_decide(monkeypatch) -> None:
    """A pod is Available long before the models it needs are on disk."""
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    with _facts_service([_NOT_READY, _NOT_READY, _READY]) as (base_url, asked):
        seconds = setup_matrix_readiness.await_facts(base_url, b"jpeg-bytes", ("heads",))

    assert len(asked) == 3, "it gave up on the first 503 instead of waiting for the models"
    assert b"anBlZy1ieXRlcw==" in asked[0], (
        "the picture goes over as base64, as the client sends it"
    )
    assert seconds >= 0.0


def test_a_service_that_never_warms_up_fails_with_what_it_actually_said(monkeypatch) -> None:
    """The facts client drops the body, and the body is what names the missing model."""
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_TIMEOUT_S", 0.2)
    with _facts_service([_NOT_READY]) as (base_url, _), pytest.raises(SystemExit) as failure:
        setup_matrix_readiness.await_facts(base_url, b"jpeg-bytes", ("heads",))

    assert "heads: the model is not in the cache yet" in str(failure.value)
    assert "503" in str(failure.value)
