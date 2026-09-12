"""The HTTP surface: ``/ping``, ``/health`` and ``/facts``.

One picture in, the frozen classifiers' answer out. Nothing about this service —
its host, its device or its execution provider — reaches a producer key: the
client stores what it is handed, and the same picture through the cpu and cuda
images lands on one bank row.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from immich_memories.triage.encoder import provider_chain
from immich_memories_inference.producers import (
    DOC_DOCLING,
    HEADS,
    NSFW_MARQO,
    HeadsProducer,
    Producer,
    ProducerFacts,
    detector_loader,
    heads_loader,
)
from immich_memories_inference.runtime import ProducerRuntime, ProducerUnavailable
from immich_memories_inference.settings import InferenceSettings

logger = logging.getLogger(__name__)

# How often the idle sweep runs, whatever the TTL is.
SWEEP_SECONDS = 15.0


class FactsRequest(BaseModel):
    image: str = Field(description="one picture, base64-encoded, as it would be previewed")
    producers: list[str] | None = Field(
        default=None, description="which producers to ask; all of them by default"
    )


def default_loaders(settings: InferenceSettings) -> dict[str, Callable[[], Producer]]:
    return {
        HEADS: heads_loader(
            settings.encoder_path, settings.bundle_path, provider=settings.provider
        ),
        NSFW_MARQO: detector_loader(
            NSFW_MARQO,
            allow_downloads=settings.allow_model_downloads,
            cache_dir=settings.detector_cache,
            marqo_onnx=settings.marqo_onnx_path,
        ),
        DOC_DOCLING: detector_loader(
            DOC_DOCLING,
            allow_downloads=settings.allow_model_downloads,
            cache_dir=settings.detector_cache,
            marqo_onnx=settings.marqo_onnx_path,
        ),
    }


def available_providers() -> tuple[str, ...]:
    try:
        import onnxruntime as ort
    except ImportError:  # pragma: no cover - the service image always has it
        return ()
    return tuple(ort.get_available_providers())


def would_use_provider(choice: str, available: tuple[str, ...]) -> str | None:
    """The provider a session would open on, without opening one."""
    try:
        return provider_chain(choice, available)[0]
    except (RuntimeError, ValueError):
        return None


def create_app(
    settings: InferenceSettings | None = None, *, runtime: ProducerRuntime | None = None
) -> FastAPI:
    """The service, on the settings in the environment unless they are supplied."""
    settings = settings or InferenceSettings()
    runtime = runtime or ProducerRuntime(
        default_loaders(settings), idle_unload_seconds=settings.idle_unload_seconds
    )
    app = FastAPI(title="immich-memories inference", lifespan=_lifespan(settings, runtime))
    _routes(app, settings, runtime)
    return app


def _lifespan(settings: InferenceSettings, runtime: ProducerRuntime) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A thread pool in front of ORT: its sessions block, and an event loop
        # that blocks stops answering /ping while a picture is being decided.
        app.state.pool = ThreadPoolExecutor(
            max_workers=settings.request_threads, thread_name_prefix="inference"
        )
        if settings.preload:
            await _preload(app, runtime)
        sweeper = asyncio.create_task(_sweep_forever(runtime))
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            runtime.unload_all()
            app.state.pool.shutdown(wait=True)

    return lifespan


async def _preload(app: FastAPI, runtime: ProducerRuntime) -> None:
    """Warm every producer at boot. A failure is logged, never fatal: the first
    request will say the same thing with a status code."""
    loop = asyncio.get_running_loop()
    for name, outcome in zip(
        runtime.names,
        await asyncio.gather(
            *(loop.run_in_executor(app.state.pool, runtime.load, name) for name in runtime.names),
            return_exceptions=True,
        ),
        strict=True,
    ):
        if isinstance(outcome, BaseException):
            logger.warning("inference: %s did not preload: %s", name, outcome)


async def _sweep_forever(runtime: ProducerRuntime) -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        for name in runtime.unload_idle():
            logger.info("inference: unloaded %s after its idle period", name)


def _routes(app: FastAPI, settings: InferenceSettings, runtime: ProducerRuntime) -> None:
    @app.get("/ping")
    async def ping() -> str:
        return "pong"

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return _health(settings, runtime)

    @app.post("/facts")
    async def facts(request: FactsRequest) -> dict[str, Any]:
        image = _decode(request.image, settings.max_image_bytes)
        loop = asyncio.get_running_loop()
        # One producer at a time: three CPU-bound seats over one picture contend
        # rather than overlap. The pool is what keeps the loop answering.
        decided = [
            await _decide(loop, app.state.pool, runtime, name, image)
            for name in _requested(request.producers, runtime.names)
        ]
        return {
            "producers": {
                result.producer: {
                    "encoder_key": result.encoder_key,
                    "facts": [asdict(fact) for fact in result.facts],
                }
                for result in decided
            }
        }


async def _decide(
    loop: asyncio.AbstractEventLoop,
    pool: ThreadPoolExecutor,
    runtime: ProducerRuntime,
    name: str,
    image: bytes,
) -> ProducerFacts:
    try:
        return await loop.run_in_executor(pool, runtime.decide, name, image)
    except ProducerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"{name} could not read this picture: {exc}"
        ) from exc


def _health(settings: InferenceSettings, runtime: ProducerRuntime) -> dict[str, Any]:
    available = available_providers()
    status = runtime.status()
    heads = runtime.loaded(HEADS) if HEADS in status else None
    session_providers = heads.session_providers if isinstance(heads, HeadsProducer) else ()
    return {
        "status": "ok",
        # What a session is on while one is open, and what one would open on when
        # none is. The first is the authority; the second is a promise.
        "provider": session_providers[0]
        if session_providers
        else would_use_provider(settings.provider, available),
        "available_providers": list(available),
        "encoder_key": status[HEADS].encoder_key if HEADS in status else None,
        "producers": {name: asdict(entry) for name, entry in status.items()},
    }


def _decode(image: str, ceiling: int) -> bytes:
    try:
        payload = base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image must be base64-encoded") from None
    if not payload:
        raise HTTPException(status_code=400, detail="image is empty")
    if len(payload) > ceiling:
        raise HTTPException(status_code=413, detail=f"image is larger than {ceiling} bytes")
    return payload


def _requested(asked: list[str] | None, served: tuple[str, ...]) -> tuple[str, ...]:
    if asked is None:
        return served
    unknown = [name for name in asked if name not in served]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown producers {unknown}; this service serves {list(served)}",
        )
    # Ask each producer once, in the order the service serves them.
    wanted = set(asked)
    return tuple(name for name in served if name in wanted)


__all__ = ["FactsRequest", "create_app", "default_loaders"]
