"""Turn fetched files into a camera roll: capture times, places, cameras and clutter.

Flickr uploads are a photographer's picks; a camera roll is not. So besides writing the
household's dates and places into every file, this makes the clutter a real roll has:
screenshots, photographed documents, bursts, blurred and dark frames. Every file made
here is marked in the manifest, so a cut that keeps one can be counted.
"""

from __future__ import annotations

import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

_EXIF_MAKE, _EXIF_MODEL, _EXIF_IFD, _GPS_IFD = 0x010F, 0x0110, 0x8769, 0x8825
_DATETIME_ORIGINAL, _OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
PHONE = ("Apple", "iPhone 12")
UNKNOWN_CAMERA = ("Camera", "Unknown camera")


def camera_of(device: str) -> tuple[str, str]:
    """Make and model from a Flickr capture-device string ("Canon EOS 40D")."""
    device = device.strip()
    if not device:
        return UNKNOWN_CAMERA
    return device.split()[0], device


def _dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    return float(degrees), float(minutes), round((value - degrees - minutes / 60) * 3600, 4)


def exif_for(
    moment: datetime, camera: tuple[str, str] | None, place: tuple[float, float] | None
) -> Image.Exif:
    """EXIF carrying the capture time (UTC offset written), camera and GPS."""
    exif = Image.Exif()
    if camera is not None:
        exif[_EXIF_MAKE], exif[_EXIF_MODEL] = camera
    details = exif.get_ifd(_EXIF_IFD)
    details[_DATETIME_ORIGINAL] = moment.strftime("%Y:%m:%d %H:%M:%S")
    details[_OFFSET_TIME_ORIGINAL] = "+00:00"
    if place is not None:
        gps = exif.get_ifd(_GPS_IFD)
        lat, lon = place
        gps[1], gps[2] = ("N" if lat >= 0 else "S"), _dms(lat)
        gps[3], gps[4] = ("E" if lon >= 0 else "W"), _dms(lon)
    return exif


def write_photo(
    image: Image.Image,
    target: Path,
    moment: datetime,
    camera: tuple[str, str] | None,
    place: tuple[float, float] | None,
) -> None:
    image.convert("RGB").save(target, "JPEG", quality=88, exif=exif_for(moment, camera, place))


def stamp_video(source: Path, target: Path, moment: datetime, place: tuple[float, float] | None):
    """Copy a video with its capture time and place in the container, the way a phone writes them."""
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        "-metadata",
        f"creation_time={moment:%Y-%m-%dT%H:%M:%S}Z",
    ]
    if place is not None:
        command += ["-metadata", f"location={place[0]:+.4f}{place[1]:+.4f}/"]
    command += ["-movflags", "+faststart+use_metadata_tags", str(target)]
    subprocess.run(command, capture_output=True, check=True)  # noqa: S603 -- fixed argv


def _text_lines(draw: ImageDraw.ImageDraw, rng: random.Random, box, color, rows: int) -> None:
    left, top, right, _bottom = box
    for row in range(rows):
        width = rng.randint(int((right - left) * 0.4), right - left)
        y = top + row * 34
        draw.rounded_rectangle((left, y, left + width, y + 16), radius=6, fill=color)


def screenshot(rng: random.Random) -> Image.Image:
    """A phone screenshot: a chat, a map-like card or a boarding pass, rendered from nothing."""
    image = Image.new("RGB", (1170, 2532), rng.choice(["#ffffff", "#f2f2f7", "#111111"]))
    draw = ImageDraw.Draw(image)
    dark = image.getpixel((0, 0)) == (17, 17, 17)
    ink = "#e5e5ea" if not dark else "#3a3a3c"
    draw.rectangle((0, 0, 1170, 140), fill="#d1d1d6" if not dark else "#1c1c1e")
    style = rng.choice(["chat", "card", "pass"])
    if style == "chat":
        for index in range(rng.randint(6, 12)):
            mine = rng.random() < 0.5
            y = 220 + index * 170
            x0 = 520 if mine else 60
            draw.rounded_rectangle(
                (x0, y, x0 + 590, y + 120), radius=40, fill="#0a84ff" if mine else ink
            )
    elif style == "card":
        draw.rectangle((0, 140, 1170, 1500), fill=rng.choice(["#cfe8cf", "#dfe6ee", "#f6e7c1"]))
        for _ in range(12):
            x, y = rng.randint(0, 1170), rng.randint(140, 1500)
            draw.line(
                (x, y, x + rng.randint(-600, 600), y + rng.randint(-400, 400)),
                fill="#ffffff",
                width=rng.randint(8, 24),
            )
        _text_lines(draw, rng, (60, 1600, 1110, 2400), ink, 14)
    else:
        draw.rounded_rectangle(
            (80, 300, 1090, 2100), radius=60, fill="#ffffff", outline=ink, width=6
        )
        _text_lines(draw, rng, (140, 400, 1030, 1200), "#8e8e93", 16)
        for i in range(0, 700, 28):
            draw.rectangle((235 + i, 1400, 235 + i + rng.randint(6, 18), 1900), fill="#000000")
    return image


def document(rng: random.Random) -> Image.Image:
    """A photographed page: a receipt or a letter on a table, slightly turned."""
    table = Image.new("RGB", (3024, 4032), rng.choice(["#7a5c3e", "#9a9a9a", "#c8b89a"]))
    page = Image.new("RGB", (1800, 2600 if rng.random() < 0.5 else 3400), "#fbfbf7")
    _text_lines(
        ImageDraw.Draw(page),
        rng,
        (140, 200, 1660, page.height - 200),
        "#4a4a4a",
        (page.height - 400) // 34,
    )
    page = page.rotate(rng.uniform(-9, 9), expand=True, fillcolor=table.getpixel((0, 0)))
    table.paste(page, ((3024 - page.width) // 2, (4032 - page.height) // 2))
    return table.filter(ImageFilter.GaussianBlur(1.2))


def burst_frame(image: Image.Image, rng: random.Random) -> Image.Image:
    """A near-twin of a picture: a few percent of crop and a small shift, as a burst gives."""
    width, height = image.size
    dx, dy = int(width * rng.uniform(0.01, 0.05)), int(height * rng.uniform(0.01, 0.05))
    frame = image.crop((dx, dy, width - int(width * 0.04) + dx // 2, height - dy))
    frame = frame.resize((width, height))
    return ImageEnhance.Brightness(frame).enhance(rng.uniform(0.94, 1.06))


def blurred(image: Image.Image, rng: random.Random) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(rng.uniform(6, 14)))


def dark(image: Image.Image, rng: random.Random) -> Image.Image:
    return ImageEnhance.Brightness(image).enhance(rng.uniform(0.12, 0.25))


def jitter(moment: datetime, rng: random.Random, low: float, high: float) -> datetime:
    return moment + timedelta(seconds=rng.uniform(low, high))
