"""Small deterministic Immich v3 HTTP service for hermetic E2E tests."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

from tests.e2e.fake_library import ALL_PICTURES, CAST, Picture

_MONTH_BUCKET = "2024-06-01T00:00:00.000Z"

# WHY these three facts together (#525): selection drops a clip whose short
# side is under 1080 unless EXIF names a camera, drops a still that names no
# camera at all, and collapses assets whose thumbnails hash alike. A fixture
# that is small, EXIF-less and identical fails all three, and the release smoke
# test spent every run since #491 reporting "Pipeline selected no clips".
_CAMERA_EXIF = {"make": "FakeCam", "model": "Hermetic One"}
_VIDEO_SIZE = (1920, 1080)
_PHOTO_SIZE = (1920, 1280)
_VIDEO_DURATION = 4.0

# A still panned across for its four seconds, the way a phone pans when someone
# walks a camera along a table. Two thirds of the frame is travelled, so the
# thumbnail taken at the halfway point still shows the middle of the picture.
_PAN_HEADROOM = 1.25

_BY_ID = {picture.asset_id: picture for picture in ALL_PICTURES}
_VIDEOS = tuple(picture for picture in ALL_PICTURES if picture.is_video)
_PHOTOS = tuple(picture for picture in ALL_PICTURES if not picture.is_video)


def _person_payload(name: str) -> dict[str, Any]:
    """The household's cast as Immich returns a person: an id, a name, a face crop."""
    return {
        "id": f"person-{name.lower()}",
        "name": name,
        "birthDate": None,
        "thumbnailPath": f"/fake/people/{name.lower()}.jpg",
        "isHidden": False,
        "updatedAt": "2024-06-01T12:00:00.000Z",
    }


def _asset_payload(picture: Picture) -> dict[str, Any]:
    is_video = picture.is_video
    width, height = _VIDEO_SIZE if is_video else _PHOTO_SIZE
    return {
        "id": picture.asset_id,
        "deviceAssetId": f"fake-device-{picture.asset_id}",
        "ownerId": "fake-user",
        "deviceId": "fake-device",
        "type": "VIDEO" if is_video else "IMAGE",
        "originalPath": f"/fake-library/{picture.filename}",
        "originalFileName": picture.filename,
        "originalMimeType": "video/mp4" if is_video else "image/jpeg",
        "thumbhash": None,
        "fileCreatedAt": picture.taken_at,
        "fileModifiedAt": picture.taken_at,
        "localDateTime": picture.taken_at,
        "updatedAt": picture.taken_at,
        "isFavorite": picture.is_favorite,
        "isArchived": False,
        "isTrashed": False,
        "duration": int(picture.seconds * 1000) if is_video else None,
        "width": width,
        "height": height,
        "exifInfo": {
            **_CAMERA_EXIF,
            "dateTimeOriginal": picture.taken_at,
            "fileSizeInByte": 400_000 if is_video else 20_000,
            "latitude": picture.place.latitude,
            "longitude": picture.place.longitude,
            "city": picture.place.city,
            "country": picture.place.country,
        },
        "people": [_person_payload(name) for name in picture.people],
        "faces": [],
        "checksum": f"fake-checksum-{picture.asset_id}",
        "livePhotoVideoId": None,
        "smartInfo": {"objects": []},
    }


TIMELINE_ASSETS = tuple(_asset_payload(picture) for picture in _VIDEOS) + tuple(
    _asset_payload(picture) for picture in _PHOTOS
)


def _month_of(taken_at: str) -> str:
    """The MONTH bucket an asset falls in, in the form Immich returns."""
    return f"{taken_at[:7]}-01T00:00:00.000Z"


def _assets_for_query(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    requested_type = query.get("type", [None])[0]
    requested_bucket = query.get("timeBucket", [None])[0]
    return [
        asset
        for asset in TIMELINE_ASSETS
        if (requested_type is None or asset["type"] == requested_type)
        and (requested_bucket is None or _month_of(asset["fileCreatedAt"]) == requested_bucket)
    ]


def _buckets_for_query(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    """One MONTH bucket per month that holds a matching asset, newest first like Immich."""
    counts: dict[str, int] = {}
    for asset in _assets_for_query({k: v for k, v in query.items() if k != "timeBucket"}):
        month = _month_of(asset["fileCreatedAt"])
        counts[month] = counts.get(month, 0) + 1
    return [
        {"count": count, "timeBucket": month}
        for month, count in sorted(counts.items(), reverse=True)
    ]


def _assets_for_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The metadata search honours the type and the taken-at window, like the real one."""
    requested_type = payload.get("type")
    taken_after = payload.get("takenAfter")
    taken_before = payload.get("takenBefore")
    return [
        asset
        for asset in TIMELINE_ASSETS
        if (requested_type is None or asset["type"] == requested_type)
        and (taken_after is None or asset["fileCreatedAt"] >= taken_after)
        and (taken_before is None or asset["fileCreatedAt"] <= taken_before)
    ]


@dataclass(frozen=True, slots=True)
class RecordedUpload:
    """A multipart asset accepted by the fake service."""

    fields: dict[str, str]
    filename: str
    content_type: str
    data: bytes


class FakeImmichServer:
    """Own a fixture-scoped HTTP server implementing the tested Immich surface."""

    api_key = "fake-immich-api-key"

    def __init__(
        self,
        root: Path,
        httpd: ThreadingHTTPServer,
        thread: threading.Thread,
        source_video: Path,
        photo_paths: dict[str, Path],
        uploads: list[RecordedUpload],
    ) -> None:
        self.root = root
        self.source_video = source_video
        self.photo_paths = photo_paths
        self.uploads = uploads
        self._httpd = httpd
        self._thread = thread
        host, port = httpd.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode("ascii")
        # A wildcard bind is an address to listen on, not one to call. Anyone
        # reading base_url wants something they can fetch, and a caller on this
        # machine can always fetch the loopback.
        self.base_url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"  # noqa: S104

    @classmethod
    def start(
        cls,
        root: Path,
        *,
        upload_commit_delay: float = 0.0,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> Self:
        """Start the service, by default on an operating-system-selected localhost port.

        `host` and `port` exist for the setup matrix, which has to serve this
        library to a NAS and a cluster over the LAN. Every asset URL the service
        hands out is relative, so binding elsewhere needs no other change.
        """
        root.mkdir(parents=True, exist_ok=True)
        media_dir = root / "media"
        media_dir.mkdir(exist_ok=True)
        video_paths = _generate_videos(media_dir)
        photo_paths = _generate_photos(media_dir)
        media = video_paths | photo_paths
        thumbnail_paths = _generate_thumbnails(media_dir, media)
        uploads: list[RecordedUpload] = []
        httpd = ThreadingHTTPServer(
            (host, port),
            _handler_type(media, thumbnail_paths, uploads, upload_commit_delay),
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return cls(
            root,
            httpd,
            thread,
            video_paths[_VIDEOS[0].asset_id],
            photo_paths,
            uploads,
        )

    @property
    def listening_host(self) -> str:
        """The address the socket is bound to, which is not always one to call."""
        host = self._httpd.server_address[0]
        return host.decode("ascii") if isinstance(host, bytes) else str(host)

    def close(self) -> None:
        """Stop the service and release its listening socket."""
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _generate_videos(media_dir: Path) -> dict[str, Path]:
    """Pan across each source photograph for four seconds, with a tone under it."""
    width, height = _VIDEO_SIZE
    stage_w, stage_h = round(width * _PAN_HEADROOM), round(height * _PAN_HEADROOM)
    videos: dict[str, Path] = {}
    for index, picture in enumerate(_VIDEOS):
        video_path = media_dir / f"{picture.asset_id}.mp4"
        _ffmpeg(
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(picture.source),
            "-f",
            "lavfi",
            # A tone per clip: silence detection reads the audio track too.
            "-i",
            f"sine=frequency={440 + index * 110}:sample_rate=48000:duration={picture.seconds}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(picture.seconds),
            "-vf",
            (
                f"scale={stage_w}:{stage_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:"
                f"x='(in_w-out_w)*t/{picture.seconds}',"
                # WHY: a JPEG decodes full-range, and libx264 would then tag the
                # clip yuvj420p -- which the production probe reads as unknown.
                "scale=in_range=full:out_range=tv,format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(video_path),
        )
        videos[picture.asset_id] = video_path
    return videos


def _generate_photos(media_dir: Path) -> dict[str, Path]:
    """Serve each source photograph at the size its asset record advertises."""
    width, height = _PHOTO_SIZE
    photos: dict[str, Path] = {}
    for picture in _PHOTOS:
        photo_path = media_dir / f"{picture.asset_id}.jpg"
        _ffmpeg(
            "-i",
            str(picture.source),
            "-vf",
            (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-update",
            "1",
            str(photo_path),
        )
        photos[picture.asset_id] = photo_path
    return photos


def _generate_thumbnails(media_dir: Path, media: dict[str, Path]) -> dict[str, Path]:
    """Render each asset its own preview, the way Immich serves one per asset.

    One shared thumbnail made every asset a perceptual duplicate of every
    other, and Phase 1 clustering collapsed the whole pool into one clip.
    """
    thumbnail_dir = media_dir / "thumbnails"
    thumbnail_dir.mkdir()
    thumbnails: dict[str, Path] = {}
    for asset_id, source in media.items():
        thumbnail_path = thumbnail_dir / f"{asset_id}.jpg"
        seconds = _BY_ID[asset_id].seconds if asset_id in _BY_ID else _VIDEO_DURATION
        seek = ["-ss", str(seconds / 2)] if source.suffix == ".mp4" else []
        _ffmpeg(
            *seek,
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=1440:-2",
            "-q:v",
            "3",
            "-update",
            "1",
            str(thumbnail_path),
        )
        thumbnails[asset_id] = thumbnail_path
    return thumbnails


def _handler_type(
    media: dict[str, Path],
    thumbnails: dict[str, Path],
    uploads: list[RecordedUpload],
    upload_commit_delay: float,
) -> type[BaseHTTPRequestHandler]:
    upload_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("x-api-key") != FakeImmichServer.api_key:
                self._send_json(401, {"message": "invalid API key"})
                return
            request_url = urlsplit(self.path)
            path = request_url.path
            query = parse_qs(request_url.query)
            if path == "/api/server/version":
                self._send_json(200, {"major": 3, "minor": 1, "patch": 0})
                return
            if path == "/api/users/me":
                self._send_json(
                    200,
                    {
                        "id": "fake-user",
                        "email": "fake@example.test",
                        "name": "Fake Immich User",
                        "isAdmin": True,
                    },
                )
                return
            if path == "/api/people":
                self._send_json(
                    200,
                    {
                        "people": [_person_payload(name) for name in CAST],
                        "total": len(CAST),
                        "hidden": 0,
                    },
                )
                return
            if path == "/api/timeline/buckets":
                self._send_json(200, _buckets_for_query(query))
                return
            if path == "/api/timeline/bucket":
                self._send_json(200, _assets_for_query(query))
                return
            parts = path.removeprefix("/api/assets/").split("/")
            if len(parts) == 2 and parts[0] in media and parts[1] == "original":
                content_type = "video/mp4" if media[parts[0]].suffix == ".mp4" else "image/jpeg"
                self._send_file(media[parts[0]], content_type)
                return
            if len(parts) == 2 and parts[0] in thumbnails and parts[1] == "thumbnail":
                self._send_file(thumbnails[parts[0]], "image/jpeg")
                return
            person = path.removeprefix("/api/people/").split("/")
            if (
                len(person) == 2
                and person[1] == "thumbnail"
                and not path.startswith("/api/assets/")
            ):
                # Every person gets the same CC0 face stand-in: the People page
                # only needs a picture that decodes, not a likeness.
                self._send_file(next(iter(thumbnails.values())), "image/jpeg")
                return
            if (
                len(parts) == 3
                and parts[0] in media
                and media[parts[0]].suffix == ".mp4"
                and parts[1:] == ["video", "playback"]
            ):
                self._send_file(media[parts[0]], "video/mp4")
                return
            self._unexpected()

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("x-api-key") != FakeImmichServer.api_key:
                self._send_json(401, {"message": "invalid API key"})
                return
            path = urlsplit(self.path).path
            if path == "/api/search/metadata":
                payload = self._read_json()
                items = _assets_for_search(payload)
                self._send_json(
                    200,
                    {
                        "assets": {
                            "total": len(items),
                            "count": len(items),
                            "items": items,
                            "nextPage": None,
                        }
                    },
                )
                return
            if path == "/api/assets":
                try:
                    upload = self._read_multipart()
                except ValueError as error:
                    self._send_json(400, {"message": str(error)})
                    return
                with upload_lock:
                    uploads.append(upload)
                    if upload_commit_delay:
                        time.sleep(upload_commit_delay)
                    asset_id = f"uploaded-{len(uploads)}"
                self._send_json(
                    201,
                    {"id": asset_id, "status": "created"},
                )
                return
            self._unexpected()

        def do_PUT(self) -> None:  # noqa: N802
            self._unexpected()

        def do_PATCH(self) -> None:  # noqa: N802
            self._unexpected()

        def do_DELETE(self) -> None:  # noqa: N802
            self._unexpected()

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            payload = json.loads(body) if body else {}
            return payload if isinstance(payload, dict) else {}

        def _read_multipart(self) -> RecordedUpload:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data;"):
                raise ValueError("expected multipart/form-data")
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
            )
            fields: dict[str, str] = {}
            upload: RecordedUpload | None = None
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not isinstance(name, str):
                    continue
                value = part.get_payload(decode=True)
                if not isinstance(value, bytes):
                    raise ValueError("multipart part has no byte payload")
                filename = part.get_filename()
                if name == "assetData" and filename:
                    upload = RecordedUpload(
                        fields=fields,
                        filename=filename,
                        content_type=part.get_content_type(),
                        data=value,
                    )
                elif filename is None:
                    fields[name] = value.decode("utf-8")
            if upload is None:
                raise ValueError("assetData is required")
            for field in ("deviceAssetId", "deviceId"):
                if field in fields:
                    raise ValueError(f"v3 upload rejects {field}")
            return upload

        def _unexpected(self) -> None:
            self._send_json(
                500,
                {
                    "error": "unexpected fake Immich request",
                    "method": self.command,
                    "path": self.path,
                },
            )

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self._send_bytes(status, body, "application/json")

        def _send_file(self, path: Path, content_type: str) -> None:
            self._send_bytes(200, path.read_bytes(), content_type)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, _format: str, *args: object) -> None:
            return

    return Handler
