"""The accepted pixel-facts-v1 recipe (its JPEG quality is deliberately 85)."""

import io
import sqlite3

import numpy as np
from PIL import Image, ImageOps

from immich_memories.store.editorial_preparation import now

PRODUCER_KEY = "pixel-facts-v1"  # gitleaks:allow


def pixel_facts(preview: bytes) -> dict[str, object]:
    with Image.open(io.BytesIO(preview)) as original:
        width, height = original.size
        try:
            orientation_tag = int(original.getexif().get(274, 1) or 1)
        except Exception:  # A broken EXIF block was absent in the accepted recipe.
            orientation_tag = 1
        transposed = ImageOps.exif_transpose(original)
        needs_rotation = transposed.size != original.size or orientation_tag != 1
        if transposed is not original:
            transposed.close()
        image = original.convert("RGB")
    image.thumbnail((400, 400), Image.Resampling.LANCZOS)
    tile = io.BytesIO()
    image.save(tile, "JPEG", quality=85)
    with Image.open(io.BytesIO(tile.getvalue())) as decoded:
        luma = np.asarray(decoded.convert("L"))
    if min(luma.shape) < 3:
        raise ValueError("preview is too small for pixel-facts-v1")
    a = luma.astype(np.float32)
    laplacian = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    return {
        "sharpness": round(float(laplacian.var()), 3),
        "brightness": round(float(luma.mean()), 3),
        "contrast": round(float(luma.std()), 3),
        "dark_fraction": round(float((luma < 30).mean()), 5),
        "bright_fraction": round(float((luma > 225).mean()), 5),
        "width": width,
        "height": height,
        "orientation": "square"
        if width == height
        else "portrait"
        if height > width
        else "landscape",
        "needs_rotation": int(needs_rotation),
    }


def remember_pixel(connection: sqlite3.Connection, asset_id: str, preview: bytes) -> None:
    values = pixel_facts(preview)
    connection.execute(
        "INSERT OR REPLACE INTO pixel_facts (asset_id,producer_key,sharpness,brightness,contrast,"
        "dark_fraction,bright_fraction,width,height,orientation,needs_rotation,computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, PRODUCER_KEY, *values.values(), now()),
    )
    connection.commit()


def refresh_threshold(connection: sqlite3.Connection) -> None:
    values = [
        row[0]
        for row in connection.execute(
            "SELECT sharpness FROM pixel_facts WHERE producer_key=?", (PRODUCER_KEY,)
        )
        if isinstance(row[0], (int, float)) and np.isfinite(row[0])
    ]
    if values:
        connection.execute(
            "INSERT OR REPLACE INTO pixel_facts_thresholds (name,value,producer_key,n,computed_at) VALUES (?,?,?,?,?)",
            ("sharpness_p10", float(np.percentile(values, 10)), PRODUCER_KEY, len(values), now()),
        )
        connection.commit()
