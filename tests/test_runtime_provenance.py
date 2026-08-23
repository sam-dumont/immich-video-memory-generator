"""Tests for identifying which code a run is actually executing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from immich_memories.automation.runtime_provenance import (
    checkout_drift,
    git_checkout_root,
    runtime_provenance,
)

BuildCheckout = Callable[..., Path]


class TestCheckoutDrift:
    def test_reports_how_far_behind_the_tracking_branch_the_checkout_is(
        self, tmp_path: Path, git_checkout_factory: BuildCheckout
    ) -> None:
        checkout = git_checkout_factory(tmp_path / "runtime", 3)

        drift = checkout_drift(checkout)

        assert drift is not None
        assert drift.commits_behind == 3
        assert drift.upstream == "origin/main"

    def test_checkout_level_with_its_upstream_has_no_drift(
        self, tmp_path: Path, git_checkout_factory: BuildCheckout
    ) -> None:
        assert checkout_drift(git_checkout_factory(tmp_path / "runtime", 0)) is None

    def test_non_git_directory_has_no_drift(self, tmp_path: Path) -> None:
        assert checkout_drift(tmp_path) is None


class TestGitCheckoutRoot:
    def test_finds_the_checkout_holding_a_nested_path(
        self, tmp_path: Path, git_checkout_factory: BuildCheckout
    ) -> None:
        checkout = git_checkout_factory(tmp_path / "runtime", 1)
        nested = checkout / "src" / "immich_memories"
        nested.mkdir(parents=True)

        assert git_checkout_root(nested) == checkout

    def test_returns_none_outside_any_checkout(self, tmp_path: Path) -> None:
        assert git_checkout_root(tmp_path / "nowhere") is None


class TestRuntimeProvenance:
    def test_always_reports_the_installed_version(self) -> None:
        from immich_memories import __version__

        assert runtime_provenance().version == __version__

    def test_describes_drift_so_a_scheduled_run_says_it_is_stale(
        self, tmp_path: Path, git_checkout_factory: BuildCheckout
    ) -> None:
        checkout = git_checkout_factory(tmp_path / "runtime", 32)
        package = checkout / "src" / "immich_memories" / "__init__.py"
        package.parent.mkdir(parents=True)
        package.write_text("")

        provenance = runtime_provenance(package_file=package)

        assert provenance.is_stale is True
        assert "32" in provenance.describe()
        assert "origin/main" in provenance.describe()
        assert provenance.to_dict()["commits_behind"] == 32

    def test_a_checkout_free_install_still_describes_itself(self, tmp_path: Path) -> None:
        package = tmp_path / "site-packages" / "immich_memories" / "__init__.py"
        package.parent.mkdir(parents=True)
        package.write_text("")

        provenance = runtime_provenance(package_file=package)

        assert provenance.is_stale is False
        assert provenance.commit is None
        assert provenance.version in provenance.describe()
