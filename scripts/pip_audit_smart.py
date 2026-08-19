#!/usr/bin/env python3
"""Smart pip-audit: warns on unfixable vulns, fails on fixable ones, fails closed.

Parses pip-audit's default text output format:
    Name     Version ID            Fix Versions
    -------- ------- ------------- ------------
    pygments 2.19.2  CVE-2026-4539

Exits 0 only when pip-audit gave an answer we understood. Anything else -- a
crash, a changed format, an empty stream -- is inconclusive, not clean. An
earlier version printed "No known vulnerabilities found" in those cases, which
turned a crashed audit into a green check.

--audit-exit carries pip-audit's own exit code, which a shell pipeline discards.
`pipefail` cannot be used instead: --strict exits non-zero for *any* finding,
which would override the policy of passing when nothing has an upstream fix.

Usage: uvx pip-audit -r reqs.txt --strict > out 2>&1; code=$?
       python3 scripts/pip_audit_smart.py --audit-exit "$code" < out
"""

import argparse
import re
import sys

EXIT_OK = 0
EXIT_VULNERABLE = 1
EXIT_INCONCLUSIVE = 2

CLEAN_MARKER = "No known vulnerabilities found"
VULN_PATTERN = re.compile(r"^(\S+)\s+(\S+)\s+((?:CVE|GHSA|PYSEC)-\S+)\s*(.*?)$", re.MULTILINE)


def _inconclusive(reason: str, output: str) -> None:
    print(reason)
    print("Treating that as a failure: an audit that did not happen is not a pass.")
    print("--- pip-audit output ---")
    print(output.strip()[:2000] or "(empty)")
    sys.exit(EXIT_INCONCLUSIVE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-exit",
        type=int,
        default=None,
        help="exit code pip-audit itself returned (0 clean, 1 vulnerabilities, other crash)",
    )
    args = parser.parse_args()
    text = sys.stdin.read()

    # pip-audit only uses 0 and 1 as considered answers. Anything else means it
    # died before finishing, so whatever it printed first does not get a vote.
    crashed = args.audit_exit is not None and args.audit_exit not in (0, 1)
    if crashed:
        _inconclusive(
            f"pip-audit exited {args.audit_exit} - it crashed rather than finishing.", text
        )

    if CLEAN_MARKER in text:
        print("No known vulnerabilities found.")
        sys.exit(EXIT_OK)

    matches = VULN_PATTERN.findall(text)
    if not matches:
        _inconclusive("pip-audit produced no result this script recognises.", text)

    fixable = []
    unfixable = []
    for name, version, vuln_id, fix_versions in matches:
        fix_versions = fix_versions.strip()
        if fix_versions:
            fixable.append((name, version, vuln_id, fix_versions))
        else:
            unfixable.append((name, version, vuln_id))

    for name, version, vuln_id in unfixable:
        print(f"WARN  {name} {version} ({vuln_id}) - no fix available yet")
    for name, version, vuln_id, fix in fixable:
        print(f"FAIL  {name} {version} ({vuln_id}) - fix available: {fix}")

    total = len(fixable) + len(unfixable)
    print(f"\n{total} vulnerabilities: {len(fixable)} fixable, {len(unfixable)} unfixable")

    if fixable:
        print("\nFailing - fixable vulnerabilities exist. Update the affected packages.")
        sys.exit(EXIT_VULNERABLE)

    # Deliberate: there is nothing to update to, and failing here would only
    # teach people to ignore the gate.
    print("\nPassing - every vulnerability found is unfixable upstream.")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
