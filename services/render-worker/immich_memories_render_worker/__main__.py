"""One process, one GPU lane; the submitting app owns orchestration."""

import uvicorn

from immich_memories_render_worker.app import create_app
from immich_memories_render_worker.native import NativeRenderer
from immich_memories_render_worker.settings import WorkerSettings


def main() -> None:
    """Start the authenticated worker using its environment settings."""
    settings = WorkerSettings()
    uvicorn.run(
        create_app(settings, renderer=NativeRenderer()),
        host=settings.host,
        port=settings.port,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
