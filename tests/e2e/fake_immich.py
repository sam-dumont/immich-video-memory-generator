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

_MONTH_BUCKET = "2024-06-01T00:00:00.000Z"

# WHY these three facts together (#525): selection drops a clip whose short
# side is under 1080 unless EXIF names a camera, drops a still that names no
# camera at all, and collapses assets whose thumbnails hash alike. A fixture
# that is small, EXIF-less and identical fails all three, and the release smoke
# test spent every run since #491 reporting "Pipeline selected no clips".
_CAMERA_EXIF = {"make": "FakeCam", "model": "Hermetic One"}
_VIDEO_SIZE = (1920, 1080)
_PHOTO_SIZE = (2016, 1512)
_VIDEO_DURATION = 4.0


@dataclass(frozen=True, slots=True)
class _Moment:
    """One synthetic capture: its own scene, its own day of the month."""

    asset_id: str
    lavfi: str
    taken_at: str
    is_favorite: bool = False


# Scenes chosen by measurement: every pair of these sources is at least 20 bits
# apart under the average hash the pipeline dedups with, against a duplicate
# threshold of 8 and a moment-suppression threshold of 10.
_VIDEO_MOMENTS = (
    _Moment("video-1", "testsrc2", "2024-06-08T10:15:00.000Z", is_favorite=True),
    _Moment("video-2", "mandelbrot", "2024-06-14T16:20:00.000Z"),
    _Moment("video-3", "testsrc", "2024-06-21T18:40:00.000Z"),
)
_PHOTO_MOMENTS = (
    _Moment("photo-1", "smptehdbars", "2024-06-09T11:30:00.000Z"),
    _Moment("photo-2", "rgbtestsrc", "2024-06-15T09:05:00.000Z", is_favorite=True),
    _Moment("photo-3", "colorspectrum", "2024-06-22T14:10:00.000Z"),
)


def _asset_payload(moment: _Moment, asset_type: str) -> dict[str, Any]:
    is_video = asset_type == "VIDEO"
    filename = f"{moment.asset_id}.{'mp4' if is_video else 'jpg'}"
    width, height = _VIDEO_SIZE if is_video else _PHOTO_SIZE
    return {
        "id": moment.asset_id,
        "deviceAssetId": f"fake-device-{moment.asset_id}",
        "ownerId": "fake-user",
        "deviceId": "fake-device",
        "type": asset_type,
        "originalPath": f"/fake-library/{filename}",
        "originalFileName": filename,
        "originalMimeType": "video/mp4" if is_video else "image/jpeg",
        "thumbhash": None,
        "fileCreatedAt": moment.taken_at,
        "fileModifiedAt": moment.taken_at,
        "localDateTime": moment.taken_at,
        "updatedAt": moment.taken_at,
        "isFavorite": moment.is_favorite,
        "isArchived": False,
        "isTrashed": False,
        "duration": int(_VIDEO_DURATION * 1000) if is_video else None,
        "width": width,
        "height": height,
        "exifInfo": {
            **_CAMERA_EXIF,
            "dateTimeOriginal": moment.taken_at,
            "fileSizeInByte": 400_000 if is_video else 20_000,
        },
        "people": [],
        "faces": [],
        "checksum": f"fake-checksum-{moment.asset_id}",
        "livePhotoVideoId": None,
        "smartInfo": {"objects": ["test-pattern"]},
    }


TIMELINE_ASSETS = tuple(_asset_payload(moment, "VIDEO") for moment in _VIDEO_MOMENTS) + tuple(
    _asset_payload(moment, "IMAGE") for moment in _PHOTO_MOMENTS
)


def _assets_for_query(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    requested_type = query.get("type", [None])[0]
    return [
        asset
        for asset in TIMELINE_ASSETS
        if requested_type is None or asset["type"] == requested_type
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
        self.base_url = f"http://{host}:{port}"

    @classmethod
    def start(cls, root: Path, *, upload_commit_delay: float = 0.0) -> Self:
        """Start the service on an operating-system-selected localhost port."""
        root.mkdir(parents=True, exist_ok=True)
        media_dir = root / "media"
        media_dir.mkdir()
        video_paths = _generate_videos(media_dir)
        photo_paths = _generate_photos(media_dir)
        media = video_paths | photo_paths
        thumbnail_paths = _generate_thumbnails(media_dir, media)
        uploads: list[RecordedUpload] = []
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _handler_type(media, thumbnail_paths, uploads, upload_commit_delay),
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return cls(
            root,
            httpd,
            thread,
            video_paths[_VIDEO_MOMENTS[0].asset_id],
            photo_paths,
            uploads,
        )

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
    width, height = _VIDEO_SIZE
    videos: dict[str, Path] = {}
    for index, moment in enumerate(_VIDEO_MOMENTS):
        video_path = media_dir / f"{moment.asset_id}.mp4"
        _ffmpeg(
            "-f",
            "lavfi",
            # WHY -t below rather than a duration= option: mandelbrot has none.
            "-i",
            f"{moment.lavfi}=size={width}x{height}:rate=30",
            "-f",
            "lavfi",
            # A tone per clip: silence detection reads the audio track too.
            "-i",
            f"sine=frequency={440 + index * 110}:sample_rate=48000:duration={_VIDEO_DURATION}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(_VIDEO_DURATION),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
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
        videos[moment.asset_id] = video_path
    return videos


def _generate_photos(media_dir: Path) -> dict[str, Path]:
    width, height = _PHOTO_SIZE
    photos: dict[str, Path] = {}
    for moment in _PHOTO_MOMENTS:
        photo_path = media_dir / f"{moment.asset_id}.jpg"
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"{moment.lavfi}=size={width}x{height}",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-update",
            "1",
            str(photo_path),
        )
        photos[moment.asset_id] = photo_path
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
        seek = ["-ss", str(_VIDEO_DURATION / 2)] if source.suffix == ".mp4" else []
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
                        "people": [
                            {
                                "id": "fake-person",
                                "name": "Fake Person",
                                "birthDate": None,
                                "thumbnailPath": "/fake/people/fake-person.jpg",
                                "isHidden": False,
                                "updatedAt": "2024-06-01T12:00:00.000Z",
                            }
                        ],
                        "total": 1,
                        "hidden": 0,
                    },
                )
                return
            if path == "/api/timeline/buckets":
                self._send_json(
                    200,
                    [{"count": len(_assets_for_query(query)), "timeBucket": _MONTH_BUCKET}],
                )
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
                requested_type = payload.get("type")
                items = [
                    asset
                    for asset in TIMELINE_ASSETS
                    if requested_type is None or asset["type"] == requested_type
                ]
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
