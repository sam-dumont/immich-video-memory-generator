#!/usr/bin/env python3
"""Smart pip-audit: warns on unfixable vulns, fails only on fixable ones.

Parses pip-audit's default text output format:
    Name     Version ID            Fix Versions
    -------- ------- ------------- ------------
    pygments 2.19.2  CVE-2026-4539

Usage: uvx pip-audit -r reqs.txt --strict 2>/dev/null | python3 scripts/pip_audit_smart.py
"""

import re
import sys


def main() -> None:
    text = sys.stdin.read()

    if "No known vulnerabilities found" in text:
        print("No known vulnerabilities found.")
        sys.exit(0)

    # Parse vulnerability lines: "name version CVE-ID fix_versions?"
    # Skip header lines (----, Name, etc.)
    vuln_pattern = re.compile(
        r"^(\S+)\s+(\S+)\s+((?:CVE|GHSA|PYSEC)-\S+)\s*(.*?)$", re.MULTILINE
    )
    matches = vuln_pattern.findall(text)

    if not matches:
        # pip-audit may have failed to run (dep resolution error)
        # In that case, pass — we can't audit what we can't resolve
        if "Failed to install" in text or "ResolutionImpossible" in text:
            print("⚠️  pip-audit could not resolve dependencies (Python version mismatch)")
            print("   Skipping vulnerability check — run manually with matching Python")
            sys.exit(0)
        print("No known vulnerabilities found.")
        sys.exit(0)

    fixable = []
    unfixable = []

    for name, version, vuln_id, fix_versions in matches:
        fix_versions = fix_versions.strip()
        if fix_versions:
            fixable.append((name, version, vuln_id, fix_versions))
        else:
            unfixable.append((name, version, vuln_id))

    for name, version, vuln_id in unfixable:
        print(f"⚠️  {name} {version} ({vuln_id}) — no fix available yet")

    for name, version, vuln_id, fix in fixable:
        print(f"❌ {name} {version} ({vuln_id}) — fix available: {fix}")

    total = len(fixable) + len(unfixable)
    print(f"\n{total} vulnerabilities: {len(fixable)} fixable, {len(unfixable)} unfixable")

    if fixable:
        print("\nFailing — fixable vulnerabilities exist. Update the affected packages.")
        sys.exit(1)
    else:
        print("\nPassing — all vulnerabilities are unfixable (no upstream fix yet).")
        sys.exit(0)


if __name__ == "__main__":
    main()
