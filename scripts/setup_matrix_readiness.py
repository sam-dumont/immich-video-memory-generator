"""What the runner waits for: a pod that exists, a Job that has finished, a service
that can answer.

Every gate here replaces something that looked like a wait and was not. `kubectl
wait` on a label selector that matches nothing is an error rather than a wait, and
the Job controller makes the pod a moment after `apply` returns, so the scheduling
wait used to lose the race and end the cell with `error: no matching resources
found`. `kubectl wait job --for=condition=complete` is never satisfied by a Job
that FAILED, so the runner watched a dead cell until its three-hour ceiling. And
`rollout status` on the inference Deployment says a pod is Available, which is not
the same as a service that can decide a picture: the models are pulled into its
cache on the first request, and until they are there every `/facts` call is a 503
that a cell would otherwise have measured as its own slowness.

The way in is no more reliable than the thing at the end of it. A Service whose
only pod is still pulling an image has no ready endpoint, and a `port-forward` to
it never gets a local listener at all, so the one that used to be built around the
warm-up ended a whole run in sixty seconds with none of the warm-up's own budget
spent. The forward is disposable here instead: it is replaced whenever it stops
answering. Three failed listener starts stop the warm-up; a responding service
keeps the fifteen-minute model-loading budget. A heartbeat reports both cases.

The caption server is the same shape again and slower to arrive: its init
container fetches 546 MB of GGUF onto a claim that is empty the first time a
cluster runs the full tier, and llama.cpp maps the weights before it answers
anything. A `--cell k8s-full-rules` run on its own used to ask for a caption
while that was still happening and die on its first picture.
"""

from __future__ import annotations

import base64
import io
import json
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image

from immich_memories.analysis.editorial_description_contract import API_MODEL, validate_envelope
from immich_memories.analysis.editorial_description_wire import request_payload, tile_preview

POD_POLL_S = 3.0
# Creating the pod is the controller's first act on a Job it has just been given,
# and it takes a moment, not minutes. Two minutes with nothing there means the
# controller never acted, which the cell should say rather than wait out.
POD_TIMEOUT_S = 2 * 60

JOB_POLL_S = 15.0
# The ceiling the old single `kubectl wait` carried. A cell that has not finished
# in three hours is a finding, not a measurement.
JOB_TIMEOUT_S = 3 * 60 * 60

# The warm-up is a real request, so it is the first cold decision of the run and
# it can take the time a model download takes.
WARMUP_TIMEOUT_S = 15 * 60
WARMUP_FIRST_WAIT_S = 2.0
WARMUP_MAX_WAIT_S = 30.0
WARMUP_REQUEST_TIMEOUT_S = 60.0
WARMUP_STATUS_INTERVAL_S = 30.0
WARMUP_LISTENER_ATTEMPTS = 3
# How long one port-forward is given to produce a local listener. A forward that
# is going to work has one in a moment; a forward that has not is pointed at a
# Service with no ready endpoint, and no amount of waiting on THAT forward fixes
# it. Short, because the answer is a new forward rather than a longer wait.
WARMUP_LISTENER_TIMEOUT_S = 20.0
# Small enough to decide in a moment, real enough to pull every model the cells
# will ask for. The service is given the picture, not a synthetic square.
WARMUP_IMAGE_PX = 200
# The caption control is the opposite case: a flat square is what
# `editorial_preparation_captions.check_provider` sends, because what it tests is
# the schema rather than the subject.
CAPTION_CONTROL_PX = 400
CAPTION_CONTROL_RGB = (128, 128, 128)


def await_listener(port: int, timeout_s: float = 15.0) -> None:
    """Block until something answers on the loopback port, or give up saying so."""
    if not _listening(port, timeout_s):
        raise SystemExit(f"nothing answered on port {port} within {timeout_s:.0f}s")


def _listening(port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if _connects(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _connects(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def await_pod(probe: Callable[[], subprocess.CompletedProcess]) -> subprocess.CompletedProcess:
    """Poll until the Job has a pod, so the wait after this has something to wait on.

    `probe` runs `kubectl get pod -l job-name=... -o name`, which exits 0 and
    prints nothing at all until the controller has made one. That silence is the
    only signal: `kubectl wait` cannot be used here because a selector matching
    nothing is an error to it, which is the race this closes.

    A kubectl that failed for its own reasons is handed straight back rather than
    polled for two minutes: only an empty answer means "not yet".
    """
    deadline = time.monotonic() + POD_TIMEOUT_S
    while True:
        proc = probe()
        if proc.returncode != 0 or (proc.stdout or "").strip():
            return proc
        if time.monotonic() >= deadline:
            return subprocess.CompletedProcess(
                args=proc.args,
                returncode=1,
                stdout=proc.stdout,
                stderr=f"the job created no pod within {POD_TIMEOUT_S:.0f}s",
            )
        time.sleep(POD_POLL_S)


def job_outcome(text: str) -> str | None:
    """`complete`, `failed`, or None while the Job is still running.

    Reads what `setup_matrix_plan.JOB_STATUS_PATH` prints: both counters by name,
    either of them empty until the Job gets there.
    """
    counts = dict(part.partition("=")[::2] for part in text.split())
    if counts.get("failed", "").strip() not in {"", "0"}:
        return "failed"
    if counts.get("succeeded", "").strip() not in {"", "0"}:
        return "complete"
    return None


def await_job(probe: Callable[[], subprocess.CompletedProcess]) -> subprocess.CompletedProcess:
    """Poll a Job until it succeeds or fails, and report a failure as a failed step.

    `probe` runs the status query and returns what it printed. The result is a
    CompletedProcess because the caller logs and grades every step the same way:
    a Job that failed, or one still running at the ceiling, comes back non-zero.
    """
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while True:
        proc = probe()
        outcome = job_outcome(proc.stdout or "")
        if outcome == "complete":
            return proc
        if outcome == "failed":
            return subprocess.CompletedProcess(
                args=proc.args,
                returncode=1,
                stdout=proc.stdout,
                stderr="the job reports failed=1, so it will never reach condition=complete",
            )
        if time.monotonic() >= deadline:
            return subprocess.CompletedProcess(
                args=proc.args,
                returncode=1,
                stdout=proc.stdout,
                stderr=f"the job neither completed nor failed within {JOB_TIMEOUT_S / 3600:.0f}h",
            )
        time.sleep(JOB_POLL_S)


def warmup_picture(source: Path) -> bytes:
    """One fixture picture at 200 px, as the JPEG bytes a facts request carries."""
    with Image.open(source) as picture:
        picture.thumbnail((WARMUP_IMAGE_PX, WARMUP_IMAGE_PX))
        buffer = io.BytesIO()
        picture.convert("RGB").save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def await_facts(base_url: str, image: bytes, producers: tuple[str, ...]) -> float:
    """Post one real picture until the service answers 200, and say how long that took.

    The service loads a model on the first request that needs it, so a 503 here
    is "not yet" rather than "broken" and is retried with a widening wait. What
    it said is carried into the failure verbatim: the facts client throws the
    body away, and the body is the only thing that names the missing model.
    """
    url = f"{base_url.rstrip('/')}/facts"
    payload = _facts_payload(image, producers)
    return _keep_asking(lambda: _direct_facts(url, payload), reached=url, wanted="a facts request")


def _direct_facts(url: str, payload: dict[str, object]) -> str | None:
    try:
        return _ask_facts(url, payload)
    except _Unreachable as unreachable:
        return str(unreachable)


def await_facts_via_forward(
    kubectl: tuple[str, ...],
    service: str,
    port: int,
    image: bytes,
    producers: tuple[str, ...],
) -> float:
    """The same warm-up, through a port-forward that is replaced the moment it stops working.

    A Deployment that was just given a new image tag leaves its Service without a
    ready endpoint for as long as the pull takes, and a forward to an endpointless
    Service never gets a local listener. That used to end the whole run in sixty
    seconds, before the warm-up's own budget had been touched at all. So the
    forward is disposable: whatever the last one did, the next attempt gets a new
    one. Three consecutive listener failures stop early; a reachable service
    keeps its full model-loading budget.
    """
    payload = _facts_payload(image, producers)
    forward = _Forward(kubectl, service, port)
    try:
        return _keep_asking(
            lambda: _forwarded_facts(forward, payload),
            reached=f"svc/{service}",
            wanted="a facts request",
        )
    finally:
        forward.stop()


def _facts_payload(image: bytes, producers: tuple[str, ...]) -> dict[str, object]:
    return {"image": base64.b64encode(image).decode("ascii"), "producers": list(producers)}


def _keep_asking(attempt: Callable[[], str | None], *, reached: str, wanted: str) -> float:
    """Retry `attempt` on the one warm-up budget, widening the wait, and time the success.

    `attempt` answers with a sentence naming what went wrong this time, or None
    once the service decided the picture. The last sentence is what the give-up
    carries: a forward that never came up and a service that answered 503 are
    different findings, and the run should not leave anyone guessing which it hit.
    """
    started = time.monotonic()
    deadline = started + WARMUP_TIMEOUT_S
    wait = WARMUP_FIRST_WAIT_S
    said = "none yet"
    number = 1
    stopped = threading.Event()

    def report() -> None:
        remaining = max(0, deadline - time.monotonic())
        detail = " ".join(said.split())[:240]
        print(
            f"{reached}: warming {wanted}, attempt {number}, "
            f"{remaining:.0f}s budget left; last failure: {detail}",
            flush=True,
        )

    def heartbeat() -> None:
        while not stopped.wait(WARMUP_STATUS_INTERVAL_S):
            report()

    report()
    reporter = threading.Thread(target=heartbeat, daemon=True)
    reporter.start()
    try:
        while True:
            failure = attempt()
            if failure is None:
                return round(time.monotonic() - started, 2)
            said = failure
            if time.monotonic() + wait >= deadline:
                raise SystemExit(
                    f"{reached} never answered {wanted} within "
                    f"{WARMUP_TIMEOUT_S / 60:.0f} min. Last failure: {said}"
                )
            time.sleep(wait)
            wait = min(wait * 2, WARMUP_MAX_WAIT_S)
            number += 1
    finally:
        stopped.set()
        reporter.join()


class _Unreachable(RuntimeError):
    """Nothing answered at all, which is a finding about the way in rather than the service."""


def _ask_facts(url: str, payload: dict[str, object]) -> str | None:
    response = _post(url, payload, "the request")
    if response.status_code == 200:
        return None
    return f"facts answered {response.status_code}: {_body(response)}"


def _forwarded_facts(forward: _Forward, payload: dict[str, object]) -> str | None:
    base = forward.address()
    if base is None:
        return f"the port-forward never answered within {WARMUP_LISTENER_TIMEOUT_S:.0f}s"
    try:
        return _ask_facts(f"{base}/facts", payload)
    except _Unreachable as unreachable:
        # Listening while nothing comes back is a forward whose pod has gone, and
        # it is worth no more than one that never came up. A 503 is the opposite:
        # the tunnel carried a real answer, so that forward is kept.
        forward.stop()
        return str(unreachable)


def await_captions_via_forward(kubectl: tuple[str, ...], service: str, port: int) -> float:
    """Ask the caption server for a caption until it gives one, and say how long that took.

    The same disposable forward and the same fifteen minutes the facts warm-up
    runs on. `rollout status` has already waited the weights onto the claim by
    the time this starts, and it is still not the same thing as a server that can
    caption: llama.cpp answers `/health` once the model is mapped, and the cells
    need an endpoint that advertises the alias AND returns the envelope.
    """
    forward = _Forward(kubectl, service, port)
    try:
        return _keep_asking(
            lambda: _forwarded_captions(forward),
            reached=f"svc/{service}",
            wanted="a caption request",
        )
    finally:
        forward.stop()


def _forwarded_captions(forward: _Forward) -> str | None:
    base = forward.address()
    if base is None:
        return f"the port-forward never answered within {WARMUP_LISTENER_TIMEOUT_S:.0f}s"
    try:
        return _ask_captions(f"{base}/v1")
    except _Unreachable as unreachable:
        forward.stop()
        return str(unreachable)


def _ask_captions(base_url: str) -> str | None:
    """Both halves of what a `tier: full` cell checks before it sends a library picture.

    The inventory has to name the alias, and one control tile has to come back an
    envelope. A server that lists the alias and cannot caption a flat square was
    started without its projector: it serves, it is blind, and the cell would
    find that out one picture in.
    """
    response = _get(f"{base_url}/models", "the models request")
    if response.status_code != 200:
        return f"models answered {response.status_code}: {_body(response)}"
    if API_MODEL not in _advertised(response):
        return f"models does not advertise {API_MODEL}: {_body(response)}"
    return _one_caption(base_url)


def _one_caption(base_url: str) -> str | None:
    response = _post(
        f"{base_url}/chat/completions", request_payload(_control_tile()), "the caption request"
    )
    if response.status_code != 200:
        return f"chat/completions answered {response.status_code}: {_body(response)}"
    try:
        content = response.json()["choices"][0]["message"]["content"]
        validate_envelope(json.loads(content))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return f"the caption is not a compact-v3 envelope: {exc}"
    return None


def _control_tile() -> bytes:
    """A flat square, through the same tiling a caption request puts a preview through."""
    buffer = io.BytesIO()
    size = (CAPTION_CONTROL_PX, CAPTION_CONTROL_PX)
    Image.new("RGB", size, CAPTION_CONTROL_RGB).save(buffer, "JPEG", quality=90)
    return tile_preview(buffer.getvalue())


def _advertised(response: httpx.Response) -> set[str]:
    try:
        payload = response.json()
    except ValueError:
        return set()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return set()
    return {row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def _get(url: str, what: str) -> httpx.Response:
    try:
        return httpx.get(url, timeout=WARMUP_REQUEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise _Unreachable(f"{what} never got an answer: {exc}") from None


def _post(url: str, payload: dict[str, object], what: str) -> httpx.Response:
    try:
        return httpx.post(url, json=payload, timeout=WARMUP_REQUEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise _Unreachable(f"{what} never got an answer: {exc}") from None


def _body(response: httpx.Response) -> str:
    """What the service said, preferring FastAPI's `detail` over the envelope around it."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail if detail is not None else payload)


class _Forward:
    """A `kubectl port-forward` to an in-cluster Service, kept only while it answers."""

    def __init__(self, kubectl: tuple[str, ...], service: str, port: int) -> None:
        self._command = (*kubectl, "port-forward", f"svc/{service}")
        self._port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._local = 0
        self._listener_failures = 0
        self._stderr = None
        self._last_error = ""

    def address(self) -> str | None:
        """A local base URL something is listening on, or None if a new forward never came up.

        A forward that has died, or that is alive while its local port answers
        nothing, is worth no more than one that was never started: both get
        replaced here rather than waited on.
        """
        if self._process is not None and self._process.poll() is None and _connects(self._local):
            return f"http://127.0.0.1:{self._local}"
        self.stop()
        self._local = _free_port()
        self._stderr = tempfile.TemporaryFile()
        self._process = subprocess.Popen(  # noqa: S603
            [*self._command, f"{self._local}:{self._port}"],
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        if _listening(self._local, WARMUP_LISTENER_TIMEOUT_S):
            self._listener_failures = 0
            return f"http://127.0.0.1:{self._local}"
        self.stop()
        self._listener_failures += 1
        if self._listener_failures >= WARMUP_LISTENER_ATTEMPTS:
            raise SystemExit(
                f"{self._command[-1]} port-forward never answered after "
                f"{self._listener_failures} listener attempts; check the Service endpoints "
                f"and kubectl connection. {self._last_error}"
            )
        return None

    def stop(self) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
        if self._stderr is not None:
            size = self._stderr.seek(0, 2)
            self._stderr.seek(max(0, size - 4096))
            self._last_error = self._stderr.read().decode(errors="replace").strip()
            self._stderr.close()
            self._stderr = None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
