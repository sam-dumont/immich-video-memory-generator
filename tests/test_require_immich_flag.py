"""REQUIRE_IMMICH=1 turns a missing Immich from a skip into a failure (the real-Immich gate)."""

from __future__ import annotations

import importlib

import pytest

import tests.integration.immich_fixtures as immich_fixtures


def test_a_missing_immich_skips_by_default(monkeypatch):
    monkeypatch.delenv("REQUIRE_IMMICH", raising=False)

    with pytest.raises(pytest.skip.Exception, match="Immich not reachable"):
        immich_fixtures.immich_unavailable("Immich not reachable")


def test_a_missing_immich_fails_when_the_run_requires_it(monkeypatch):
    monkeypatch.setenv("REQUIRE_IMMICH", "1")

    with pytest.raises(pytest.fail.Exception, match="REQUIRE_IMMICH=1"):
        immich_fixtures.immich_unavailable("Immich not reachable")


def test_requires_immich_never_skips_when_the_run_requires_it(monkeypatch):
    # Registered first so undo puts the import-time mark back after the reload.
    monkeypatch.setattr(immich_fixtures, "requires_immich", immich_fixtures.requires_immich)
    monkeypatch.setenv("REQUIRE_IMMICH", "1")

    reloaded = importlib.reload(immich_fixtures)

    assert reloaded.requires_immich.args == (False,)
