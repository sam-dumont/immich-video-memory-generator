"""Contract checks for launch-critical Immich compatibility documentation."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "docs/USER_GUIDE.md",
        "docs-site/docs/reference/config-reference.md",
        "docs-site/docs/deploy/configuration/config-file.md",
    ],
)
def test_primary_manuals_document_the_default_immich_version_policy(relative_path: str) -> None:
    """Removing the public auto/v2/v3 contract from a primary manual must fail."""
    text = _read(relative_path)

    assert "Immich v2 and v3" in text
    assert "api_version: auto  # auto | v2 | v3" in text
    assert "runtime" in text.lower()
    assert "override" in text.lower()


def test_immich_environment_override_is_documented() -> None:
    """Operators must be able to find the environment form of the escape hatch."""
    text = _read("docs-site/docs/deploy/configuration/environment-variables.md")

    assert 'IMMICH_MEMORIES_IMMICH__API_VERSION="auto"' in text
    assert "v2" in text
    assert "v3" in text
    assert "runtime" in text.lower()
    assert "override" in text.lower()


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs-site/docs/deploy/maintenance/upgrading.md",
        "docs-site/docs/reference/troubleshooting.md",
    ],
)
def test_operator_manuals_explain_the_v2_to_v3_compatibility_boundary(
    relative_path: str,
) -> None:
    """Dropping any known v3 boundary from operator guidance must fail."""
    text = _read(relative_path).lower()

    assert "duration" in text
    assert "upload" in text
    assert "search" in text
    assert "immich-memories config test" in text
    assert "read-only" in text


def test_readme_does_not_claim_v1_only_or_pin_v2_5_6() -> None:
    """The launch page must not contradict the supported-major contract."""
    text = _read("README.md")

    assert "Immich v2.5.6" not in text
    assert "v1.100+" not in text


def test_launch_audit_closes_the_immich_code_and_docs_blocker() -> None:
    """The saved launch assessment must track the verified compatibility closure."""
    text = _read("docs/reviews/2026-08-11-launch-readiness-audit.md")

    assert "P0.3 Immich v2/v3: fixed" in text
    assert "`c6cea3b`" in text
    assert "code and documentation" in text.lower()
