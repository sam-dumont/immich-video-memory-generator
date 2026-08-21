"""The release smoke gate must authenticate against its own fake server (#480).

Every release since the gate landed failed with "Invalid API key": the script
invented a key while FakeImmichServer checks `x-api-key` against its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "docker_smoke.py"


def test_the_container_gets_the_fake_servers_key() -> None:
    source = _SCRIPT.read_text()

    assert "IMMICH_API_KEY={server.api_key}" in source
    assert "smoke-test-key" not in source


def test_a_failure_prints_the_childs_own_output() -> None:
    """The CLI logs to stdout, so stderr alone hid the cause."""
    source = _SCRIPT.read_text()
    failure_block = source.split("if proc.returncode != 0:")[1].split("return 1")[0]

    assert "proc.stdout" in failure_block
    assert "proc.stderr" in failure_block


def test_the_script_still_parses() -> None:
    ast.parse(_SCRIPT.read_text())
