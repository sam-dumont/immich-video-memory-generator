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
"""

from __future__ import annotations

import base64
import io
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

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
# Small enough to decide in a moment, real enough to pull every model the cells
# will ask for. The service is given the picture, not a synthetic square.
WARMUP_IMAGE_PX = 200


def await_listener(port: int, timeout_s: float = 15.0) -> None:
    """Block until something answers on the loopback port, or give up saying so."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"nothing answered on port {port} within {timeout_s:.0f}s"
                ) from None
            time.sleep(0.2)


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
    from PIL import Image

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
    payload = {"image": base64.b64encode(image).decode("ascii"), "producers": list(producers)}
    url = f"{base_url.rstrip('/')}/facts"
    started = time.monotonic()
    deadline = started + WARMUP_TIMEOUT_S
    wait = WARMUP_FIRST_WAIT_S
    said = "it never answered at all"
    while True:
        try:
            response = httpx.post(url, json=payload, timeout=WARMUP_REQUEST_TIMEOUT_S)
            if response.status_code == 200:
                return round(time.monotonic() - started, 2)
            said = f"HTTP {response.status_code}: {_body(response)}"
        except httpx.HTTPError as exc:
            said = str(exc)
        if time.monotonic() + wait >= deadline:
            raise SystemExit(
                f"{url} never answered a facts request within "
                f"{WARMUP_TIMEOUT_S / 60:.0f} min. Last answer: {said}"
            )
        time.sleep(wait)
        wait = min(wait * 2, WARMUP_MAX_WAIT_S)


def _body(response: httpx.Response) -> str:
    """What the service said, preferring FastAPI's `detail` over the envelope around it."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail if detail is not None else payload)


@contextmanager
def port_forward(kubectl: tuple[str, ...], service: str, port: int) -> Iterator[str]:
    """A local URL for an in-cluster Service, for as long as the block runs."""
    local = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [*kubectl, "port-forward", f"svc/{service}", f"{local}:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        await_listener(local, timeout_s=60.0)
        yield f"http://127.0.0.1:{local}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
