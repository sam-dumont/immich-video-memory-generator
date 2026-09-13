"""A helper child process must never write into the output of the CLI run that owns it.

Regression for #846. The kernel backend probe used a `multiprocessing` spawn
child, and a spawn child re-imports the parent's `__main__` with the parent's
`sys.argv` restored. Under any launcher whose module body is not guarded by
`if __name__ == "__main__"` — a wrapper script, a container entrypoint, the
end-to-end harness — that re-ran the whole command inside the running one and
printed its `Error: Immich not configured...` into the terminal of a run that
then went on to succeed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

# A launcher script: it resolves the config itself, then hands the command to
# Click. Its module body is unguarded, which is what a spawn child re-executes.
_LAUNCHER = '''\
"""Run the CLI the way a wrapper script does: config from argv, then the command."""

import sys
from pathlib import Path

import immich_memories.config_loader as config_loader

_config_path = Path(sys.argv[1])
config_loader.Config.get_default_path = classmethod(lambda cls: _config_path)
sys.argv = ["immich-memories", "years"]

from immich_memories.cli import main
from immich_memories.titles.kernel_backend_probe import _probe_backend

# Where a title render asks whether the GPU can dispatch, mid-run. Whichever
# answer comes back, none of it belongs in the command's output.
_probe_backend("metal", timeout=5.0)

main()
'''


class _EmptyLibraryHandler(BaseHTTPRequestHandler):
    """Answer the timeline query behind `immich-memories years` with nothing."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        self._respond()

    def _respond(self) -> None:
        body = json.dumps([]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the handler's own request log out of the test output."""


@pytest.fixture
def immich_url() -> str:
    # WHY: `years` is the cheapest real command that needs Immich configured,
    # so the server it reads is the one boundary this test cannot fake in-process.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _EmptyLibraryHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        yield f"http://{host if isinstance(host, str) else host.decode()}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_a_successful_cli_run_prints_no_error_from_a_helper_child(
    tmp_path: Path, immich_url: str
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "immich": {"url": immich_url, "api_key": "test-key", "api_version": "v2"},
                "cache": {"directory": str(tmp_path / "cache")},
                "output": {"directory": str(tmp_path / "output")},
            }
        )
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(_LAUNCHER)
    home = tmp_path / "home"
    home.mkdir()

    completed = subprocess.run(  # noqa: S603 — this interpreter, no shell
        [sys.executable, str(launcher), str(config_path)],
        capture_output=True,
        text=True,
        timeout=300,
        env={"HOME": str(home), "PATH": str(Path(sys.executable).parent)},
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    # The command ran once: a child that re-runs it is a second, unasked-for run.
    assert output.count("Years with video content") == 1, output
    assert [line for line in output.splitlines() if line.startswith("Error:")] == []
