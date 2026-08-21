"""No test under tests/integration/ may leak into the unit suite (#450).

`pytestmark` in a conftest does nothing — markers only work in test modules —
so a forgotten `@pytest.mark.integration` silently ran a mega-flow test in
every unit CI job (+450 MB, real pipeline work). The root conftest now marks
by path, so the marker cannot be forgotten.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests.conftest import pytest_collection_modifyitems


def _item(path: str):
    markers: list = []
    return SimpleNamespace(
        path=Path(path),
        add_marker=markers.append,
        markers=markers,
    )


def test_integration_tests_are_marked_by_path():
    inside = _item("/repo/tests/integration/pipeline/test_mega_flows.py")
    outside = _item("/repo/tests/test_models.py")

    pytest_collection_modifyitems([inside, outside])

    assert inside.markers == ["integration"]
    assert outside.markers == []
