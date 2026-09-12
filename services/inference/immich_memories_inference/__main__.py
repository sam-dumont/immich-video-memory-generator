"""``python -m immich_memories_inference`` — one worker, one port.

One worker on purpose: the producers batch inside the process and hold their own
weights, so a second worker doubles the resident memory to serve the same
pictures. immich-ml's `MACHINE_LEARNING_WORKERS` is the knob we did not copy.
"""

from __future__ import annotations

import logging

import uvicorn

from immich_memories_inference.app import create_app
from immich_memories_inference.settings import InferenceSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = InferenceSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
