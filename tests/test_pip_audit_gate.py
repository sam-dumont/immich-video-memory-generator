"""The vulnerability gate must fail closed.

Three paths through pip_audit_smart.py used to exit 0 without auditing
anything, and the worst of them printed "No known vulnerabilities found" when
the regex simply hadn't matched -- turning a crashed or reformatted pip-audit
into a clean bill of health. A security gate that reports success on absence of
evidence is worse than no gate, because it is believed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pip_audit_smart.py"


def run(stdin_text: str, audit_exit: int | None = None) -> int:
    argv = [sys.executable, str(SCRIPT)]
    if audit_exit is not None:
        argv += ["--audit-exit", str(audit_exit)]
    result = subprocess.run(argv, input=stdin_text, capture_output=True, text=True)
    return result.returncode


CLEAN = "No known vulnerabilities found\n"
FIXABLE = (
    "Name     Version ID            Fix Versions\n"
    "-------- ------- ------------- ------------\n"
    "pygments 2.19.2  CVE-2026-4539 2.19.3\n"
)
UNFIXABLE = (
    "Name     Version ID            Fix Versions\n"
    "-------- ------- ------------- ------------\n"
    "pygments 2.19.2  CVE-2026-4539\n"
)


def test_an_explicit_all_clear_passes() -> None:
    assert run(CLEAN) == 0


def test_a_fixable_vulnerability_fails() -> None:
    assert run(FIXABLE) == 1


def test_vulnerabilities_with_no_upstream_fix_still_pass() -> None:
    """Deliberate policy: nothing to update to, so failing would only teach
    people to ignore the gate."""
    assert run(UNFIXABLE) == 0


def test_unresolvable_dependencies_fail_instead_of_skipping() -> None:
    assert run("ERROR: ResolutionImpossible: could not resolve\n") != 0


def test_output_the_parser_does_not_recognise_fails() -> None:
    """pip-audit crashing, changing format, or being killed all land here. The
    old code answered 'No known vulnerabilities found'."""
    assert run("Traceback (most recent call last):\n  ValueError: boom\n") != 0


def test_empty_input_fails() -> None:
    """An empty requirements file audits nothing and used to read as clean."""
    assert run("") != 0


def test_a_crashed_audit_fails_even_if_its_output_looks_clean() -> None:
    """pip-audit's exit code is the authoritative signal. If it died, the text
    it managed to emit first does not get to say the dependencies are fine."""
    assert run(CLEAN, audit_exit=2) != 0


def test_a_nonzero_exit_with_only_unfixable_vulns_still_passes() -> None:
    """pip-audit --strict exits 1 on any vulnerability, including ones with no
    fix. The unfixable policy has to survive that."""
    assert run(UNFIXABLE, audit_exit=1) == 0


def test_a_clean_exit_code_passes() -> None:
    assert run(CLEAN, audit_exit=0) == 0
