"""What the runner has to wait for, and what it does when it never comes.

Every one of these was found on a real cluster. `kubectl wait` on a label selector
that matches nothing exits 1 with `error: no matching resources found`, and the pod
is made a moment after `apply` returns, so the scheduling wait lost the race and
ended the cell. A Job that FAILS never satisfies `kubectl wait
--for=condition=complete`, so the runner sat on a dead cell for three hours. A
Deployment that is Available is not a service that can answer: the pod was up
while every `/facts` request came back 503 because the models were not in its
cache yet, which a cell then measured as its own slowness. And a re-applied image
tag left that Deployment in ContainerCreating with its Service holding no ready
endpoint, so the port-forward the warm-up went through never got a listener and
the run died in sixty seconds without ever making a facts request.
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
from setup_matrix_plan import INFERENCE_ENV, Cell, CellPlan, Plan, Step  # noqa: E402

from immich_memories.analysis.editorial_description_contract import API_MODEL  # noqa: E402


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


_FORWARD_SCRIPT = """\
import http.server
import os
import socket
import sys
import time

tally = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forwards")
n = (int(open(tally).read()) if os.path.exists(tally) else 0) + 1
open(tally, "w").write(str(n))
local = int(sys.argv[-1].split(":")[0])
if n <= {silent}:
    time.sleep(120)
    raise SystemExit(0)
if n <= {silent} + {deaf}:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", local))
    listener.listen(5)
    while True:
        listener.accept()[0].close()


class _Facts(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = b'{{"producers": {{}}}}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


http.server.HTTPServer(("127.0.0.1", local), _Facts).serve_forever()
"""


def _fake_forwarding_kubectl(tmp_path: Path, *, silent: int = 0, deaf: int = 0) -> None:
    """A kubectl `port-forward` that is broken in each of the two ways a real one is.

    Its first `silent` calls bind nothing, which is a forward to a Service with no
    ready endpoint. The `deaf` calls after those bind the local port and close
    every connection, which is a forward whose pod has gone. Any call after that
    is the service: it binds the port it was handed and answers the facts request.
    """
    script = tmp_path / "kubectl"
    body = _FORWARD_SCRIPT.format(silent=silent, deaf=deaf)
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o755)


def test_a_forward_that_never_came_up_is_thrown_away_and_made_again(monkeypatch, tmp_path) -> None:
    """The pod was still pulling its image, so the first forwards had nothing to reach."""
    _fake_forwarding_kubectl(tmp_path, silent=2)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_LISTENER_TIMEOUT_S", 1.0)

    seconds = setup_matrix_readiness.await_facts_via_forward(
        ("kubectl",), "inference", 8092, b"jpeg-bytes", ("heads",)
    )

    assert (tmp_path / "forwards").read_text().strip() == "3", (
        "it kept the dead forward instead of making a new one on the same budget"
    )
    assert seconds >= 0.0


def test_a_forward_whose_pod_has_gone_is_replaced_rather_than_asked_again(
    monkeypatch, tmp_path
) -> None:
    """It is listening and it connects; nothing behind it answers, which is the same thing."""
    _fake_forwarding_kubectl(tmp_path, deaf=1)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_TIMEOUT_S", 30.0)

    setup_matrix_readiness.await_facts_via_forward(
        ("kubectl",), "inference", 8092, b"jpeg-bytes", ("heads",)
    )

    assert (tmp_path / "forwards").read_text().strip() == "2", (
        "it kept asking down a tunnel that reaches nothing"
    )


def test_a_forward_that_never_answers_spends_the_budget_and_says_it_was_the_forward(
    monkeypatch, tmp_path
) -> None:
    """`facts answered 503` and `port-forward never answered` are different findings."""
    _fake_forwarding_kubectl(tmp_path, silent=99)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_LISTENER_TIMEOUT_S", 0.2)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_TIMEOUT_S", 0.5)

    with pytest.raises(SystemExit) as failure:
        setup_matrix_readiness.await_facts_via_forward(
            ("kubectl",), "inference", 8092, b"jpeg-bytes", ("heads",)
        )

    assert "port-forward never answered" in str(failure.value)
    assert "svc/inference" in str(failure.value)


def test_broken_forward_stops_after_three_listener_attempts(monkeypatch, tmp_path) -> None:
    _fake_forwarding_kubectl(tmp_path, silent=99)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_LISTENER_TIMEOUT_S", 0.2)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_TIMEOUT_S", 3.0)
    launched = []
    popen = setup_matrix_readiness.subprocess.Popen

    # WHY: observe the process-launch boundary while still running real child processes.
    # A timed-out child may be killed before its Python bookkeeping ever executes.
    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process.pid)
        return process

    monkeypatch.setattr(setup_matrix_readiness.subprocess, "Popen", launch)

    with pytest.raises(SystemExit, match="3 listener attempts"):
        setup_matrix_readiness.await_facts_via_forward(
            ("kubectl",), "inference", 8092, b"jpeg-bytes", ("heads",)
        )

    assert len(launched) == 3


def test_warmup_reports_while_a_request_is_waiting(monkeypatch, capsys) -> None:
    """The heartbeat must not depend on a blocked HTTP request returning."""
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_STATUS_INTERVAL_S", 0.02)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    reported = threading.Event()
    calls = 0

    # WHY: observe the stdout boundary and release the waiting request only after
    # the real heartbeat reports it, independent of the runner's scheduling speed.
    def report(message, **kwargs):
        print(message, **kwargs)
        if "attempt 2" in message:
            reported.set()

    monkeypatch.setattr(setup_matrix_readiness, "print", report, raising=False)

    def attempt():
        nonlocal calls
        calls += 1
        if calls == 1:
            return "facts answered 503: loading heads"
        assert reported.wait(5), "the heartbeat did not report while the request was blocked"
        return None

    setup_matrix_readiness._keep_asking(attempt, reached="svc/inference", wanted="facts")

    output = capsys.readouterr().out
    assert "attempt 1" in output
    assert "attempt 2" in output
    assert "loading heads" in output
    assert "budget left" in output


def _fake_rollout_kubectl(tmp_path: Path, *, rollout_code: int) -> None:
    """A kubectl that writes down every subcommand it was given, and can fail the rollout."""
    script = tmp_path / "kubectl"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path}/ran"\n'
        f'case " $* " in *" rollout "*) exit {rollout_code};; esac\n'
        "exit 0\n"
    )
    script.chmod(0o755)


def _cluster_plan() -> Plan:
    item = _job_cell(_pod_step())
    plan = _plan(item)
    plan.environment.update({"MATRIX_K8S_CONTEXT": "ctx", "MATRIX_K8S_NAMESPACE": "ns"})
    return plan


def test_the_overlay_is_not_handed_on_until_its_new_pod_has_rolled_out(monkeypatch, tmp_path):
    """`apply` returns while the pod is still pulling, and nothing reaches it before it stops."""
    _fake_rollout_kubectl(tmp_path, rollout_code=0)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    setup_matrix.bring_up_inference(_cluster_plan(), "deploy/kubernetes/overlays/inference", "i:t")

    ran = (tmp_path / "ran").read_text().splitlines()
    applied = next(n for n, line in enumerate(ran) if line.startswith("--context ctx -n ns apply"))
    waited = next(n for n, line in enumerate(ran) if "rollout status" in line)
    assert applied < waited, "it went on to the service before the new pod existed"
    assert "deployment/immich-memories-inference --timeout=10m" in ran[waited]


def test_a_deployment_that_never_rolls_out_stops_the_run_rather_than_the_warm_up(
    monkeypatch, tmp_path
) -> None:
    """Fifteen minutes of facts requests cannot report an image that never got pulled."""
    _fake_rollout_kubectl(tmp_path, rollout_code=1)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(SystemExit) as failure:
        setup_matrix.bring_up_inference(
            _cluster_plan(), "deploy/kubernetes/overlays/inference", "i:t"
        )

    assert "immich-memories-inference" in str(failure.value)
    assert "10m" in str(failure.value)


def test_an_address_the_nas_cells_can_reach_is_warmed_without_a_forward(
    monkeypatch, tmp_path
) -> None:
    """A forward is a second process that can fail on its own, and this needs none."""
    _fake_forwarding_kubectl(tmp_path, silent=99)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    plan = _cluster_plan()

    with _facts_service([_READY]) as (base_url, asked):
        plan.environment[INFERENCE_ENV] = base_url
        setup_matrix.warm_inference(plan)

    assert len(asked) == 1
    assert not (tmp_path / "forwards").exists(), "it port-forwarded to an address it already had"


# --- the caption server ---------------------------------------------------------

_CAPTIONER_SCRIPT = """\
import http.server
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "ran"), "a").write(" ".join(sys.argv[1:]) + "\\n")
if "rollout" in sys.argv:
    raise SystemExit({rollout_code})

open(os.path.join(here, "forwards"), "a").write("1\\n")
local = int(sys.argv[-1].split(":")[0])


class _Captioner(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._wrote("GET " + self.path)
        self._say({{"object": "list", "data": [{{"id": "{model}"}}]}})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self._wrote("POST " + self.path + " " + body)
        envelope = json.dumps({{"description": "a flat grey square", "setting": "plain"}})
        self._say({{"choices": [{{"finish_reason": "stop", "message": {{"content": envelope}}}}]}})

    def _wrote(self, line):
        open(os.path.join(here, "asked"), "a").write(line + "\\n")

    def _say(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


http.server.HTTPServer(("127.0.0.1", local), _Captioner).serve_forever()
"""


def _fake_captioner_kubectl(
    tmp_path: Path, *, model: str = API_MODEL, rollout_code: int = 0
) -> None:
    """A kubectl that answers `rollout status` and then port-forwards to a caption server.

    One fake for both halves, because the runner takes both in one go. Every call
    writes its own argv down; a `rollout` one exits with `rollout_code`, and the
    rest bind the local port they were handed and serve llama.cpp's two endpoints.
    A forward that never comes up is the facts tests' ground: the caption probe
    goes through the same disposable forward and the same budget.
    """
    script = tmp_path / "kubectl"
    body = _CAPTIONER_SCRIPT.format(rollout_code=rollout_code, model=model)
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o755)


def test_the_caption_server_is_made_to_caption_before_any_cell_asks_it_to(
    monkeypatch, tmp_path
) -> None:
    """Rolled out is not ready: llama.cpp maps 546 MB of weights on its way to the first answer."""
    _fake_captioner_kubectl(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)

    seconds = setup_matrix.bring_up_captioner(_cluster_plan())

    ran = (tmp_path / "ran").read_text().splitlines()
    assert "rollout status deployment/immich-memories-captioner --timeout=15m" in ran[0]
    assert "port-forward svc/captioner" in ran[1], "it asked for a caption before the rollout"
    asked = (tmp_path / "asked").read_text().splitlines()
    assert asked[0] == "GET /v1/models"
    assert asked[1].startswith("POST /v1/chat/completions")
    assert API_MODEL in asked[1], "the control went out under some other model name"
    assert "data:image/jpeg;base64," in asked[1], "it asked for a caption of nothing"
    assert seconds >= 0.0


def test_a_captioner_that_never_rolls_out_stops_the_run(monkeypatch, tmp_path) -> None:
    """Fifteen minutes of caption requests cannot report a claim that never filled."""
    _fake_captioner_kubectl(tmp_path, rollout_code=1)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(SystemExit) as failure:
        setup_matrix.bring_up_captioner(_cluster_plan())

    assert "immich-memories-captioner" in str(failure.value)
    assert "15m" in str(failure.value)
    assert not (tmp_path / "forwards").exists(), "it forwarded to a Deployment that was not up"


def test_a_server_advertising_another_model_spends_the_budget_and_names_the_alias(
    monkeypatch, tmp_path
) -> None:
    """An endpoint that serves and is wrong is what this probe exists to catch."""
    _fake_captioner_kubectl(tmp_path, model="llava-1.5-7b")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_FIRST_WAIT_S", 0.0)
    monkeypatch.setattr(setup_matrix_readiness, "WARMUP_TIMEOUT_S", 0.5)

    with pytest.raises(SystemExit) as failure:
        setup_matrix.bring_up_captioner(_cluster_plan())

    assert API_MODEL in str(failure.value)
    assert "svc/captioner" in str(failure.value)
    assert "a caption request" in str(failure.value)
    assert "POST" not in (tmp_path / "asked").read_text(), "it sent a tile to the wrong model"
