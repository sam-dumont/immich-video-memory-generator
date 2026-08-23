"""Everything the analysis package advertises has to actually resolve.

__init__ maps public names to modules as strings, so a name pointing at the
wrong module is invisible to mypy and to every test that imports directly.
Three of them pointed at analysis.duplicates for things that live elsewhere,
and `from immich_memories.analysis import cluster_thumbnails` raised
AttributeError at runtime.
"""

import pytest

import immich_memories.analysis as analysis


@pytest.mark.parametrize("name", sorted(analysis.__all__))
def test_every_advertised_name_resolves(name: str) -> None:
    assert getattr(analysis, name) is not None
