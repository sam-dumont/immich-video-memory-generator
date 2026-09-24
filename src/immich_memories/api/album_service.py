"""Upload and album management API service."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from immich_memories.api.compatibility import ResolvedApiVersion

RequestFn = Callable[..., Any]
ApiVersionFn = Callable[[], Awaitable[ResolvedApiVersion]]

_UPLOAD_MEDIA_TYPES = {
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
}

# The device identity the V2 upload stamps on its own assets. Read back by the
# cleanup below, so the two must stay the same string.
_UPLOAD_DEVICE_ID = "immich-memories"


logger = logging.getLogger(__name__)


class InvalidUploadResponse(ValueError):
    """Raised when Immich returns a malformed successful upload response."""


class AlbumNotFoundError(LookupError):
    """Raised when an album reference matches no album name or ID."""


class AmbiguousAlbumError(LookupError):
    """Raised when an album name matches more than one album.

    Immich libraries synced from iOS routinely carry several albums with the
    same name ('Récentes', 'Favorites'), so a name alone is not an identifier.
    """


@dataclass(frozen=True)
class AlbumRef:
    """An album resolved to its ID, display name and asset count.

    ``start`` and ``end`` are when its earliest and latest pictures were taken,
    as Immich reports them; None when Immich did not say.
    """

    id: str
    name: str
    asset_count: int
    start: datetime | None = None
    end: datetime | None = None


# WHY these two bars are relative, never a fixed size or a list of names: a
# phone's catch-all ("Recents", in whatever language the phone speaks) is 600
# pictures in one library and 38,000 in another, and a trip album in a small
# library can be bigger than a whole year in another. What sets a catch-all
# apart is its proportion to the film. At most `pool` of an album's pictures
# can belong to the film, so an album more than twice the pool's size is
# mostly other films. And an album whose dates run mostly outside the window
# is filed around some other time.
_MAX_ALBUM_TO_POOL = 2.0
_MIN_SPAN_INSIDE_WINDOW = 0.75


@dataclass(frozen=True)
class FilmScope:
    """The window a film covers and how many pictures it drew from.

    ``pool`` is every picture the film could have picked (the period's, or the
    person's within it), not the cut.
    """

    start: datetime
    end: datetime
    pool: int

    def is_curated_for(self, album: AlbumRef) -> bool:
        """Whether this album was made for a film like this one.

        A catch-all holding most of the cut is not the film's name: it holds most
        of every cut. An album counts only when it is not out of proportion to
        the film's pool and its own dates fall mostly inside the film's window.
        A person film over twenty years holds a decade-long catch-all's whole
        span, so there size alone decides.
        """
        if album.asset_count > _MAX_ALBUM_TO_POOL * max(self.pool, 1):
            return False
        if album.start is None or album.end is None:
            return True
        album_start, album_end = _utc(album.start), _utc(album.end)
        span = (album_end - album_start).total_seconds()
        inside = (
            min(album_end, _utc(self.end)) - max(album_start, _utc(self.start))
        ).total_seconds()
        if span <= 0:
            return inside >= 0
        return inside / span >= _MIN_SPAN_INSIDE_WINDOW


def build_upload_fields(
    version: ResolvedApiVersion,
    file_path: Path,
    modified_at: datetime,
    captured_at: datetime | None = None,
) -> dict[str, str]:
    """Build deterministic request fields without opening or reading file contents.

    ``captured_at`` is when the content itself happened, offset included; it is
    what files the asset in the Immich timeline. Without one the file's own
    mtime stands in, which puts the asset on the day it was written.
    V2 identity intentionally retains the legacy hash of the real filename and file size.
    """
    common_fields = {
        "fileCreatedAt": (captured_at or modified_at).isoformat(),
        "fileModifiedAt": modified_at.isoformat(),
    }
    if version is ResolvedApiVersion.V3:
        return {"filename": file_path.name} | common_fields

    file_hash = hashlib.sha256(file_path.name.encode() + str(file_path.stat().st_size).encode())
    identity_fields = {
        "deviceAssetId": f"{_UPLOAD_DEVICE_ID}-{file_hash.hexdigest()[:16]}",
        "deviceId": _UPLOAD_DEVICE_ID,
    }
    return identity_fields | common_fields


def _file_sha1(file_path: Path) -> str:
    with file_path.open("rb") as f:
        return hashlib.file_digest(f, lambda: hashlib.sha1(usedforsecurity=False)).hexdigest()


def _upload_asset_id(data: Any) -> str:
    if not isinstance(data, dict):
        raise InvalidUploadResponse("Upload response must contain a non-empty string id")
    asset_id = data.get("id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise InvalidUploadResponse("Upload response must contain a non-empty string id")
    return asset_id


def _upload_media_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    try:
        return _UPLOAD_MEDIA_TYPES[suffix]
    except KeyError as exc:
        raise ValueError(f"Unsupported upload file suffix: {suffix or '<none>'}") from exc


def _utc(moment: datetime) -> datetime:
    # A naive window is the run's own local days; a day either way is noise at
    # the proportions these bars judge.
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _album_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _album_ref(album: dict) -> AlbumRef:
    return AlbumRef(
        id=album["id"],
        name=album.get("albumName") or album["id"],
        asset_count=album.get("assetCount") or 0,
        start=_album_date(album.get("startDate")),
        end=_album_date(album.get("endDate")),
    )


class AlbumService:
    """Upload and album management operations against the Immich API."""

    def __init__(self, request_fn: RequestFn, api_version_fn: ApiVersionFn) -> None:
        self._request = request_fn
        self._get_api_version = api_version_fn

    async def upload_asset(self, file_path: Path, *, captured_at: datetime | None = None) -> str:
        """Upload a file to Immich. Returns the asset ID.

        ``captured_at`` files the asset on the day its content happened; without
        one it lands on the day the file was written.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content_type = _upload_media_type(file_path)
        version = await self._get_api_version()
        stat = file_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        fields = build_upload_fields(version, file_path, modified_at, captured_at)

        checksum = _file_sha1(file_path)

        with file_path.open("rb") as f:
            data = await self._request(
                "POST",
                "/assets",
                data=fields,
                files={"assetData": (file_path.name, f, content_type)},
                # WHY: Immich answers "duplicate" instead of storing a second copy
                # of a file whose SHA-1 it already holds for this user.
                headers={"x-immich-checksum": checksum},
                timeout=600.0,  # 10 min for large video uploads on slow connections
                before_retry=lambda: self._stored_upload(checksum),
            )
        return _upload_asset_id(data)

    async def _stored_upload(self, checksum: str) -> dict[str, str] | None:
        """The asset a failed attempt stored anyway, so a retry does not store it twice.

        A 5xx or a timeout can arrive after Immich has written the asset. The
        library is asked for the file's checksum first; only a file it does not
        hold (or holds only in the trash) is sent again.
        """
        try:
            data = await self._request(
                "POST",
                "/assets/bulk-upload-check",
                json={"assets": [{"id": "upload", "checksum": checksum}]},
            )
        except Exception:  # WHY: an unanswered check still leaves the checksum header
            logger.debug("Upload check failed; retrying the upload", exc_info=True)
            return None
        results = data.get("results") if isinstance(data, dict) else None
        found = results[0] if isinstance(results, list) and results else {}
        asset_id = found.get("assetId") if isinstance(found, dict) else None
        if not isinstance(asset_id, str) or found.get("isTrashed"):
            return None
        logger.info("An earlier upload attempt already stored this film as %s", asset_id)
        return {"id": asset_id, "status": "duplicate"}

    async def create_album(self, name: str, description: str | None = None) -> str:
        """Create an album in Immich. Returns the album ID."""
        body: dict = {"albumName": name}
        if description:
            body["description"] = description
        data = await self._request("POST", "/albums", json=body)
        return data["id"]

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        await self._request("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids})

    async def get_albums(self) -> list[dict]:
        return await self._request("GET", "/albums")

    async def albums_containing(self, asset_id: str) -> list[AlbumRef]:
        """Every album holding one asset."""
        data = await self._request("GET", "/albums", params={"assetId": asset_id})
        albums = data if isinstance(data, list) else []
        return [_album_ref(a) for a in albums if isinstance(a, dict) and a.get("id")]

    async def album_holding_most(self, asset_ids: Sequence[str], *, scope: FilmScope) -> str | None:
        """The name of the album this cut mostly sits in, if one was made for it.

        Answers "what did this family day get called" for a title, from a name
        somebody typed rather than anything invented. Only albums curated for a
        film of this ``scope`` take part (see `FilmScope.is_curated_for`): a
        picture that only the phone's catch-all holds is filed nowhere. The bar
        comes from the cut's own composition: the leading album has to hold more
        of the cut than the pictures no such album claims. A day filed across
        two albums still learns the bigger one's name; two pictures out of ten
        learn nothing. Between albums holding as much of the cut as each other,
        the smaller album wins.

        One request per picture of the cut, once per film.
        """
        cut = list(dict.fromkeys(asset_ids))
        held: Counter[str] = Counter()
        refs: dict[str, AlbumRef] = {}
        unfiled = 0
        for asset_id in cut:
            albums = [ref for ref in await self._albums_of(asset_id) if scope.is_curated_for(ref)]
            unfiled += not albums
            for ref in albums:
                held[ref.id] += 1
                refs[ref.id] = ref
        if not held:
            logger.info("No album made for this film holds any of the cut's %d pictures", len(cut))
            return None
        leader = max(held, key=lambda album: (held[album], -refs[album].asset_count))
        logger.info(
            "Leading album holds %d of the cut's %d pictures, %d of which sit in no album "
            "made for this film",
            held[leader],
            len(cut),
            unfiled,
        )
        return refs[leader].name if held[leader] > unfiled else None

    async def _albums_of(self, asset_id: str) -> list[AlbumRef]:
        try:
            return await self.albums_containing(asset_id)
        except Exception:  # WHY: one deleted asset must not cost the whole answer
            logger.debug("Album lookup failed for %s", asset_id, exc_info=True)
            return []

    async def list_albums(self) -> list[AlbumRef]:
        """Albums that could make a memory, largest first.

        Empty albums are dropped: they cannot produce anything, so offering them
        in a picker only invites a dead end.
        """
        albums = await self.get_albums()
        refs = [_album_ref(a) for a in albums if (a.get("assetCount") or 0) > 0]
        refs.sort(key=lambda ref: ref.asset_count, reverse=True)
        return refs

    async def resolve_album(self, name_or_id: str) -> AlbumRef:
        """Resolve an album reference to its ID, name and asset count.

        Matches an album ID first, then an exact name, then a case-insensitive
        name, so users can pass what they see in Immich.

        Raises:
            AmbiguousAlbumError: the name matches several albums.
            AlbumNotFoundError: nothing matches.
        """
        albums = await self.get_albums()

        by_id = {a.get("id"): a for a in albums}
        if name_or_id in by_id:
            return _album_ref(by_id[name_or_id])

        exact = [a for a in albums if a.get("albumName") == name_or_id]
        folded = name_or_id.casefold()
        matches = exact or [a for a in albums if (a.get("albumName") or "").casefold() == folded]

        if len(matches) == 1:
            return _album_ref(matches[0])
        if matches:
            raise AmbiguousAlbumError(
                f"{len(matches)} albums are named {name_or_id!r}. Pass the ID instead:\n"
                + "\n".join(
                    f"  {a['id']}  ({a.get('assetCount') or 0} assets)"
                    for a in sorted(matches, key=lambda a: -(a.get("assetCount") or 0))
                )
            )

        known = ", ".join(sorted({n for a in albums if (n := a.get("albumName"))})) or "none"
        raise AlbumNotFoundError(
            f"No Immich album named or with ID {name_or_id!r}. Albums: {known}"
        )

    async def find_album_by_name(self, name: str) -> str | None:
        """Returns album ID if found, None otherwise."""
        albums = await self.get_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album["id"]
        return None

    async def list_album_assets(self, album_id: str) -> list[dict]:
        """Every asset in an album, via search: /albums/{id} omits them on Immich 3.x.

        Search answers at most 1000 per page, so the pages are followed until
        Immich stops naming a next one.
        """
        assets: list[dict] = []
        page: int | None = 1
        while page:
            data = await self._request(
                "POST",
                "/search/metadata",
                json={"albumIds": [album_id], "size": 1000, "page": page},
            )
            found = (data or {}).get("assets", {})
            assets.extend(found.get("items", []))
            page = int(found["nextPage"]) if found.get("nextPage") else None
        return assets

    async def trash_assets(self, asset_ids: list[str]) -> None:
        """Move assets to Immich's trash. Recoverable; never a hard delete."""
        await self._request("DELETE", "/assets", json={"ids": asset_ids, "force": False})

    async def upload_memory(
        self,
        video_path: Path,
        album_name: str | None = None,
        *,
        captured_at: datetime | None = None,
    ) -> dict[str, str | None]:
        """Upload a generated memory video, optionally adding it to an album.

        Reuses existing album if one with the same name exists.
        """
        asset_id = await self.upload_asset(video_path, captured_at=captured_at)

        album_id = None
        if album_name:
            album_id = await self.find_album_by_name(album_name)
            if album_id is None:
                album_id = await self.create_album(album_name)
            await self.add_assets_to_album(album_id, [asset_id])

        # WHY: the upload has already succeeded. Failing the delivery because the
        # tidy-up of a previous copy did not work would turn a working memory into
        # a reported failure, so this never propagates.
        try:
            superseded = await supersede_previous_renders(
                self, album_id=album_id, filename=video_path.name, keep_asset_id=asset_id
            )
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("Could not supersede earlier renders: %s", exc)
        else:
            if superseded:
                logger.info(
                    "Superseded %d earlier upload(s) of the same recipe (moved to Immich trash)",
                    len(superseded),
                )

        return {"asset_id": asset_id, "album_id": album_id}


def _is_our_upload(asset: dict) -> bool:
    """Whether this app uploaded the asset, by the identity it stamps at upload."""
    return asset.get("deviceId") == _UPLOAD_DEVICE_ID and str(
        asset.get("deviceAssetId", "")
    ).startswith(f"{_UPLOAD_DEVICE_ID}-")


async def supersede_previous_renders(
    client,
    *,
    album_id: str | None,
    filename: str,
    keep_asset_id: str,
) -> list[str]:
    """Trash earlier uploads of the same recipe, keeping the one just uploaded.

    The filename carries a hash of the recipe -- the memory type, range, duration
    and the exact clips in order -- so an identical name means an identical edit
    and the older copy is superseded rather than kept beside it. Without this, a
    library accumulates one indistinguishable file per run; eight of them is what
    prompted this.

    Deliberately narrow. Only assets inside the album we just uploaded to, with
    exactly this filename, never the asset we just created, and only ones
    carrying this app's own upload identity. A matching name is not proof of
    authorship -- a custom `output.filename` can name a video the user shot
    themselves, and trashing that is not ours to do. V3 uploads carry no device
    identity, so their older renders are kept until there is durable provenance
    for them. Immich's trash is recoverable, so this is reversible by the user.
    """
    if not album_id:
        return []

    assets = await client.list_album_assets(album_id)
    superseded = [
        asset["id"]
        for asset in assets
        if asset.get("originalFileName") == filename
        and asset.get("id") != keep_asset_id
        and _is_our_upload(asset)
    ]
    if superseded:
        await client.trash_assets(superseded)
    return superseded
