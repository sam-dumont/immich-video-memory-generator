#!/usr/bin/env python3
"""Merge multiple JUnit XML files into one.

Usage: python merge_junit_xml.py OUTPUT INPUT1 [INPUT2 ...]

Collects all <testcase> elements from each input file into a single
<testsuite> with aggregated counts and timing. Skips missing files
so partial suite runs still produce a report.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def merge(output_path: str, input_paths: list[str]) -> None:
    all_cases: list[ET.Element] = []
    total_time = 0.0
    total_errors = 0
    total_failures = 0
    total_skipped = 0

    for path in input_paths:
        if not Path(path).exists():
            continue
        try:
            tree = ET.parse(path)  # noqa: S314 — trusted local JUnit XML
        except ET.ParseError:
            continue

        root = tree.getroot()
        # Handle both <testsuites><testsuite>... and <testsuite>... formats
        suites = root.findall(".//testsuite")
        if root.tag == "testsuite":
            suites = [root]

        for suite in suites:
            total_time += float(suite.get("time", 0))
            total_errors += int(suite.get("errors", 0))
            total_failures += int(suite.get("failures", 0))
            total_skipped += int(suite.get("skipped", 0))
            all_cases.extend(suite.findall("testcase"))

    # Build merged output
    merged_root = ET.Element("testsuites")
    merged_suite = ET.SubElement(
        merged_root,
        "testsuite",
        name="integration",
        tests=str(len(all_cases)),
        errors=str(total_errors),
        failures=str(total_failures),
        skipped=str(total_skipped),
        time=f"{total_time:.3f}",
    )
    for case in all_cases:
        merged_suite.append(case)

    tree = ET.ElementTree(merged_root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} OUTPUT INPUT1 [INPUT2 ...]", file=sys.stderr)
        sys.exit(1)
    merge(sys.argv[1], sys.argv[2:])
