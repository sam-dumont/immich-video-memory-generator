"""Exact compact-caption image and request bytes shared by acquisition and audit."""

from __future__ import annotations

import base64
import io
import json

from PIL import Image

from immich_memories.analysis.editorial_description_contract import (
    API_MODEL,
    MAX_OUTPUT_TOKENS,
    PROMPT,
    REPETITION_PENALTY,
    RESPONSE_SCHEMA,
)


def request_payload(image: bytes, *, api_model: str = API_MODEL) -> dict[str, object]:
    return {
        "model": api_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "repetition_penalty": REPETITION_PENALTY,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "compact_asset_description",
                "strict": True,
                "schema": RESPONSE_SCHEMA,
            },
        },
    }


def tile_preview(preview: bytes) -> bytes:
    with Image.open(io.BytesIO(preview)) as source:
        image = source.convert("RGB")
        image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def request_bytes(image: bytes) -> bytes:
    return json.dumps(request_payload(image)).encode()
