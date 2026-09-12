"""Loaded producers, held while they are used and dropped when they are not.

immich-machine-learning unloads after an idle TTL and then sends itself SIGINT.
The unload is worth copying; the suicide is not — a restart loop on a NAS costs
more than a resident idle process, so this drops the weights and stays up.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from time import monotonic

from immich_memories_inference.producers import Producer, ProducerFacts


class UnknownProducer(LookupError):
    """A producer this service does not serve."""


class ProducerUnavailable(RuntimeError):
    """A producer that cannot be loaded here: absent weights, absent dependency."""


@dataclass
class ProducerStatus:
    loaded: bool
    versions: Mapping[str, str] | None = None
    encoder_key: str | None = None


@dataclass
class _Slot:
    load: Callable[[], Producer]
    # One lock per producer: a sweep must never free a session another thread is
    # running on, and ORT is happier with one caller per session anyway.
    lock: threading.Lock = field(default_factory=threading.Lock)
    producer: Producer | None = None
    last_used: float = 0.0


class ProducerRuntime:
    """Lazy load, one lock per producer, idle unload without idle suicide."""

    def __init__(
        self,
        loaders: Mapping[str, Callable[[], Producer]],
        *,
        idle_unload_seconds: float = 300.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._slots = {name: _Slot(load=loader) for name, loader in loaders.items()}
        self._idle_unload_seconds = idle_unload_seconds
        self._clock = clock

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._slots)

    def load(self, name: str) -> None:
        """Make the producer resident now, so the first request does not wait."""
        slot = self._slot(name)
        with slot.lock:
            self._resident(slot, name)

    def decide(self, name: str, image: bytes) -> ProducerFacts:
        """Load the producer if it is not resident, then decide this picture."""
        slot = self._slot(name)
        with slot.lock:
            producer = self._resident(slot, name)
            try:
                return producer.decide(image)
            finally:
                slot.last_used = self._clock()

    def _resident(self, slot: _Slot, name: str) -> Producer:
        if slot.producer is None:
            try:
                slot.producer = slot.load()
            except Exception as exc:
                raise ProducerUnavailable(f"{name}: {type(exc).__name__}: {exc}") from exc
        slot.last_used = self._clock()
        return slot.producer

    def status(self) -> dict[str, ProducerStatus]:
        status = {}
        for name, slot in self._slots.items():
            producer = slot.producer
            status[name] = (
                ProducerStatus(loaded=False)
                if producer is None
                else ProducerStatus(
                    loaded=True,
                    versions=dict(producer.versions),
                    encoder_key=producer.encoder_key,
                )
            )
        return status

    def loaded(self, name: str) -> Producer | None:
        return self._slot(name).producer

    def unload_idle(self) -> tuple[str, ...]:
        """Drop every producer idle past the TTL. A TTL of 0 keeps them all."""
        if not self._idle_unload_seconds:
            return ()
        deadline = self._clock() - self._idle_unload_seconds
        return tuple(name for name, slot in self._slots.items() if self._release(slot, deadline))

    def unload_all(self) -> None:
        for slot in self._slots.values():
            with slot.lock:
                slot.producer = None

    @staticmethod
    def _release(slot: _Slot, deadline: float) -> bool:
        # Never wait: a producer mid-inference is by definition not idle.
        if slot.producer is None or slot.last_used > deadline or not slot.lock.acquire(False):
            return False
        try:
            slot.producer = None
        finally:
            slot.lock.release()
        return True

    def _slot(self, name: str) -> _Slot:
        try:
            return self._slots[name]
        except KeyError:
            raise UnknownProducer(name) from None
