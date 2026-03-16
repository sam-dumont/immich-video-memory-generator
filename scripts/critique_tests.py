#!/usr/bin/env python3
"""Test quality critique — flags mock-heavy and assertion-light tests.

Run via: make critique (called automatically in CI)

Checks:
1. Files where mock count > assert count (testing mocks, not behavior)
2. Tests that only assert mock.assert_called_once() (no real verification)
3. Test classes where every method has >3 @patch decorators (over-mocked)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS_DIR = Path("tests")
MAX_PATCHES_PER_METHOD = 3
EXIT_FAILURE = 1
EXIT_SUCCESS = 0


def count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


def check_mock_vs_assert_ratio(test_files: list[Path]) -> list[str]:
    """Flag files where mock/patch count exceeds real assert count."""
    issues = []
    for f in test_files:
        text = f.read_text()
        mock_count = count_pattern(text, r"@patch|Mock\(\)|MagicMock\(\)")
        # Real asserts (not mock.assert_called*)
        total_asserts = count_pattern(text, r"\bassert\b")
        mock_asserts = count_pattern(text, r"\.assert_called|\.assert_has_calls")
        real_asserts = total_asserts - mock_asserts

        if mock_count > 0 and real_asserts > 0 and mock_count > real_asserts:
            issues.append(f"  {f}: {mock_count} mocks vs {real_asserts} real asserts")
    return issues


def check_mock_only_assertions(test_files: list[Path]) -> list[str]:
    """Flag test methods that only assert mock.assert_called_once() — no real verification."""
    issues = []
    method_pattern = re.compile(
        r"(def (test_\w+)\(.*?\n)(.*?)(?=\n    def |\nclass |\Z)",
        re.DOTALL,
    )
    for f in test_files:
        text = f.read_text()
        for match in method_pattern.finditer(text):
            method_name = match.group(2)
            body = match.group(3)
            has_mock_assert = bool(re.search(r"\.assert_called|\.assert_has_calls", body))
            has_real_assert = bool(re.search(r"\bassert\b(?!.*\.assert_called)", body))
            if has_mock_assert and not has_real_assert:
                issues.append(f"  {f}::{method_name}")
    return issues


def check_excessive_patches(test_files: list[Path]) -> list[str]:
    """Flag test classes where every method has >3 @patch decorators."""
    issues = []
    class_pattern = re.compile(r"^class (Test\w+).*?(?=\nclass |\Z)", re.MULTILINE | re.DOTALL)
    method_pattern = re.compile(r"def (test_\w+)")

    for f in test_files:
        text = f.read_text()
        for class_match in class_pattern.finditer(text):
            class_name = class_match.group(1)
            class_body = class_match.group(0)

            methods = method_pattern.findall(class_body)
            if not methods:
                continue

            # Check each method's @patch count
            all_over_limit = True
            for method_name in methods:
                # Find the method and count patches above it
                method_idx = class_body.find(f"def {method_name}")
                if method_idx < 0:
                    continue
                # Look at lines above the method def for @patch decorators
                preceding = class_body[:method_idx]
                lines_above = preceding.split("\n")
                patch_count = 0
                for line in reversed(lines_above):
                    stripped = line.strip()
                    if stripped.startswith("@patch"):
                        patch_count += 1
                    elif stripped and not stripped.startswith("@"):
                        break
                if patch_count <= MAX_PATCHES_PER_METHOD:
                    all_over_limit = False
                    break

            if all_over_limit and len(methods) > 1:
                issues.append(
                    f"  {f}::{class_name} ({len(methods)} methods, all >{MAX_PATCHES_PER_METHOD} @patch)"
                )
    return issues


def main() -> int:
    test_files = sorted(f for f in TESTS_DIR.rglob("test_*.py") if "__pycache__" not in str(f))

    if not test_files:
        print("No test files found")
        return EXIT_FAILURE

    has_issues = False

    # Check 1: Mock vs assert ratio
    ratio_issues = check_mock_vs_assert_ratio(test_files)
    if ratio_issues:
        print("WARN: Files with more mocks than real asserts:")
        print("\n".join(ratio_issues))
        print()

    # Check 2: Mock-only assertions
    mock_only = check_mock_only_assertions(test_files)
    if mock_only:
        print("WARN: Test methods with only mock assertions (no real asserts):")
        print("\n".join(mock_only))
        print()

    # Check 3: Excessive patches
    excessive = check_excessive_patches(test_files)
    if excessive:
        print("WARN: Test classes where all methods have >3 @patch decorators:")
        print("\n".join(excessive))
        has_issues = True
        print()

    if not ratio_issues and not mock_only and not excessive:
        print("Test critique: all clean.")

    # Only fail on excessive patches (the worst smell).
    # Ratio and mock-only are warnings for now.
    return EXIT_FAILURE if has_issues else EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
