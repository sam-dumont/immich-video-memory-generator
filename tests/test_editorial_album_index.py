"""The album names a run may quote, indexed once from Immich."""

from __future__ import annotations

from collections.abc import Sequence

from immich_memories.api.album_service import AlbumRef


class _Albums:
    """The two Immich album reads an index needs.

    # WHY: replaces the Immich HTTP boundary — the album listing and one album's
    # assets — so the request shape can be counted without a server.
    """

    def __init__(self, albums: Sequence[tuple[AlbumRef, tuple[str, ...]]]) -> None:
        self._albums = tuple(albums)
        self.listings = 0
        self.read: list[str] = []

    def list_albums(self) -> list[AlbumRef]:
        self.listings += 1
        return [ref for ref, _assets in self._albums]

    def list_album_assets(self, album_id: str) -> list[dict[str, str]]:
        self.read.append(album_id)
        return [
            {"id": asset_id}
            for ref, assets in self._albums
            if ref.id == album_id
            for asset_id in assets
        ]


def _source() -> _Albums:
    return _Albums(
        (
            (AlbumRef(id="al-1", name="Summer Festival 2022", asset_count=2), ("a1", "a2")),
            (AlbumRef(id="al-2", name="Sunday at the lake", asset_count=1), ("a2",)),
        )
    )


def test_the_index_costs_one_listing_and_one_read_per_album() -> None:
    from immich_memories.analysis.editorial_album_index import build_album_index

    source = _source()
    index = build_album_index(source)

    assert source.listings == 1
    assert source.read == ["al-1", "al-2"]
    assert index.names_for(("a1",)) == ("Summer Festival 2022",)
    assert index.names_for(("a2", "a1")) == ("Summer Festival 2022", "Sunday at the lake")
    assert index.names_for(("unheld",)) == ()
    assert index.indexed_albums == ("Summer Festival 2022", "Sunday at the lake")


def test_an_album_above_the_cap_is_never_read() -> None:
    from immich_memories.analysis.editorial_album_index import build_album_index

    source = _Albums(
        (
            (AlbumRef(id="al-smart", name="Recents", asset_count=40_000), ("a1",)),
            (AlbumRef(id="al-1", name="Summer Festival 2022", asset_count=2), ("a1", "a2")),
        )
    )

    index = build_album_index(source, max_album_assets=10)

    assert source.read == ["al-1"]
    assert index.skipped_albums == ("Recents",)
    assert index.names_for(("a1",)) == ("Summer Festival 2022",)


def test_a_source_that_cannot_list_albums_names_nothing() -> None:
    from immich_memories.analysis.editorial_album_index import NO_ALBUMS, build_album_index

    assert build_album_index(object()) is NO_ALBUMS


def test_the_index_is_built_once_however_many_episodes_ask() -> None:
    from immich_memories.analysis.editorial_album_index import RunAlbumNames

    source = _source()
    recorded = []
    names = RunAlbumNames(source, record=recorded.append)

    assert names(("a1",)) == ("Summer Festival 2022",)
    assert names(("a2",)) == ("Summer Festival 2022", "Sunday at the lake")
    assert source.listings == 1
    assert [index.indexed_albums for index in recorded] == [
        ("Summer Festival 2022", "Sunday at the lake")
    ]
