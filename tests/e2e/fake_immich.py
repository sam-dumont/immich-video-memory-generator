"""Small deterministic Immich v3 HTTP service for hermetic E2E tests."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

_MONTH_BUCKET = "2024-06-01T00:00:00.000Z"


def _asset_payload(asset_id: str, asset_type: str, filename: str) -> dict[str, Any]:
    created_at = f"2024-06-{1 + int(asset_id[-1]):02d}T12:00:00.000Z"
    is_video = asset_type == "VIDEO"
    return {
        "id": asset_id,
        "deviceAssetId": f"fake-device-{asset_id}",
        "ownerId": "fake-user",
        "deviceId": "fake-device",
        "type": asset_type,
        "originalPath": f"/fake-library/{filename}",
        "originalFileName": filename,
        "originalMimeType": "video/mp4" if is_video else "image/jpeg",
        "thumbhash": None,
        "fileCreatedAt": created_at,
        "fileModifiedAt": created_at,
        "localDateTime": created_at,
        "updatedAt": created_at,
        "isFavorite": False,
        "isArchived": False,
        "isTrashed": False,
        "duration": 2000 if is_video else None,
        "width": 640 if is_video else 320,
        "height": 360 if is_video else 240,
        "exifInfo": {
            "dateTimeOriginal": created_at,
            "fileSizeInByte": 100_000 if is_video else 2_000,
        },
        "people": [],
        "faces": [],
        "checksum": f"fake-checksum-{asset_id}",
        "livePhotoVideoId": None,
        "smartInfo": {"objects": ["test-pattern"]},
    }


_ASSETS = (
    _asset_payload("video-1", "VIDEO", "video-1.mp4"),
    _asset_payload("video-2", "VIDEO", "video-2.mp4"),
    _asset_payload("photo-1", "IMAGE", "photo-1.jpg"),
    _asset_payload("photo-2", "IMAGE", "photo-2.jpg"),
)


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
    def start(cls, root: Path) -> Self:
        """Start the service on an operating-system-selected localhost port."""
        root.mkdir(parents=True, exist_ok=True)
        source_video = _generate_video(root)
        photo_paths = _generate_photos(root)
        media = {"video-1": source_video, "video-2": source_video} | photo_paths
        uploads: list[RecordedUpload] = []
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_type(media, uploads))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return cls(root, httpd, thread, source_video, photo_paths, uploads)

    def close(self) -> None:
        """Stop the service and release its listening socket."""
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _generate_video(root: Path) -> Path:
    media_dir = root / "media"
    media_dir.mkdir()
    video_path = media_dir / "source.mp4"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=2.0",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2.0",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
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
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return video_path


def _generate_photos(root: Path) -> dict[str, Path]:
    media_dir = root / "media"
    photos: dict[str, Path] = {}
    for asset_id, color in (("photo-1", "red"), ("photo-2", "blue")):
        photo_path = media_dir / f"{asset_id}.jpg"
        subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:size=320x240",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-update",
                "1",
                str(photo_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        photos[asset_id] = photo_path
    return photos


def _handler_type(
    media: dict[str, Path], uploads: list[RecordedUpload]
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("x-api-key") != FakeImmichServer.api_key:
                self._send_json(401, {"message": "invalid API key"})
                return
            path = urlsplit(self.path).path
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
                self._send_json(200, [{"count": 4, "timeBucket": _MONTH_BUCKET}])
                return
            if path == "/api/timeline/bucket":
                self._send_json(200, _ASSETS)
                return
            parts = path.removeprefix("/api/assets/").split("/")
            if len(parts) == 2 and parts[0] in media and parts[1] == "original":
                content_type = "video/mp4" if media[parts[0]].suffix == ".mp4" else "image/jpeg"
                self._send_file(media[parts[0]], content_type)
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
                    for asset in _ASSETS
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
                uploads.append(upload)
                self._send_json(
                    201,
                    {"id": f"uploaded-{len(uploads)}", "status": "created"},
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
