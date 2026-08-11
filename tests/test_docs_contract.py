"""Contract checks for launch-critical Immich compatibility documentation."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def _normalized_section(relative_path: str, heading: str, next_heading: str) -> str:
    text = _read(relative_path)
    section = text.split(heading, 1)[1].split(next_heading, 1)[0]
    return " ".join(section.split())


def _assert_upgrade_contract(section: str) -> None:
    """Assert complete v2/v3 operator claims inside the upgrade section."""
    required_claims = (
        "Explicit `v2` and `v3` are manual troubleshooting escape hatches for unusual proxies "
        "or deployments that prevent correct detection; they force the selected contract.",
        "**Duration:** v2 duration strings and v3 integer milliseconds are normalized to seconds.",
        "**Upload:** v2 keeps the device identity fields; v3 sends `filename` and omits the "
        "removed `deviceAssetId` and `deviceId` fields.",
        "**Search dates:** date bounds include a UTC offset, which v3 requires.",
        "immich-memories config test",
        "This is a read-only authentication and compatibility check. It does not search assets, "
        "generate a video, create an album, or upload anything.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing Immich compatibility claim: {claim}"


@pytest.mark.parametrize(
    ("relative_path", "heading", "next_heading"),
    [
        pytest.param(
            "README.md",
            "### Supported Immich Versions",
            "### Optional: LLM for smart clip analysis",
            id="readme",
        ),
        pytest.param(
            "docs/USER_GUIDE.md",
            "### Immich Connection",
            "### Time Period Selection",
            id="user-guide",
        ),
        pytest.param(
            "docs-site/docs/reference/config-reference.md",
            "## Immich connection",
            "## Video analysis",
            id="config-reference",
        ),
        pytest.param(
            "docs-site/docs/deploy/configuration/config-file.md",
            "## Quick start config",
            "## Clip pacing",
            id="config-file",
        ),
    ],
)
def test_primary_manuals_document_the_default_immich_version_policy(
    relative_path: str, heading: str, next_heading: str
) -> None:
    """Removing the public auto/v2/v3 contract from a primary manual must fail."""
    section = _normalized_section(relative_path, heading, next_heading)

    assert "Immich v2 and v3" in section
    assert "api_version: auto # auto | v2 | v3" in section


def test_immich_environment_override_is_documented() -> None:
    """Operators must be able to find the environment form of the escape hatch."""
    section = _normalized_section(
        "docs-site/docs/deploy/configuration/environment-variables.md",
        "### Immich connection",
        "### Analysis settings",
    )

    assert 'IMMICH_MEMORIES_IMMICH__API_VERSION="auto"' in section
    assert "Keep `API_VERSION` on `auto` for default runtime detection" in section
    assert "`v2` and `v3` are manual troubleshooting overrides—escape hatches" in section


def test_upgrade_manual_explains_the_complete_v2_to_v3_contract() -> None:
    """The upgrade section must keep every compatibility boundary together."""
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/upgrading.md",
        "## Upgrading Immich from v2 to v3",
        "## Config compatibility",
    )

    _assert_upgrade_contract(section)


@pytest.mark.parametrize(
    ("documented", "weakened"),
    [
        pytest.param(
            "`v2` and `v3` are manual troubleshooting escape hatches",
            "`v2` and `v3` are routine settings",
            id="manual-overrides-are-escape-hatches",
        ),
        pytest.param(
            "they force the selected contract",
            "they prefer the selected contract",
            id="manual-overrides-force-contract",
        ),
        pytest.param(
            "v3 integer milliseconds are normalized to seconds",
            "v3 integer durations are normalized to seconds",
            id="v3-duration-is-milliseconds",
        ),
        pytest.param("`filename`", "`name`", id="v3-upload-uses-filename"),
        pytest.param("`deviceAssetId`", "`legacyAssetId`", id="v3-upload-omits-device-asset-id"),
        pytest.param("`deviceId`", "`legacyDeviceId`", id="v3-upload-omits-device-id"),
        pytest.param(
            "UTC offset, which v3 requires",
            "serialized date value",
            id="v3-search-dates-have-utc-offset",
        ),
        pytest.param(
            "This is a read-only authentication and compatibility check.",
            "This is an authentication and compatibility check.",
            id="config-test-is-read-only",
        ),
    ],
)
def test_upgrade_contract_rejects_semantic_mutations(documented: str, weakened: str) -> None:
    """Each launch-critical sentence must fail independently when its meaning is removed."""
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/upgrading.md",
        "## Upgrading Immich from v2 to v3",
        "## Config compatibility",
    )
    mutated = section.replace(documented, weakened, 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_upgrade_contract(mutated)


def test_readme_does_not_claim_v1_only_or_pin_v2_5_6() -> None:
    """The launch page must not contradict the supported-major contract."""
    text = _read("README.md")

    assert "Immich v2.5.6" not in text
    assert "v1.100+" not in text


def test_launch_audit_closes_the_immich_code_and_docs_blocker() -> None:
    """The saved launch assessment must track the verified compatibility closure."""
    section = _normalized_section(
        "docs/reviews/2026-08-11-launch-readiness-audit.md",
        "## Remediation status — 2026-08-11",
        "## Safety action already taken",
    )

    assert "P0.3 Immich v2/v3: fixed and independently approved" in section
    assert "closed through `144c38f`" in section
    assert "public compatibility docs landed in `59e97b2`" in section
    assert "final compatibility gate passed 465 tests" in section
    assert (
        "Live read-only `immich-memories config test` passed with both default `auto` detection"
        in section
    )
    assert "an explicit `v3` override" in section
