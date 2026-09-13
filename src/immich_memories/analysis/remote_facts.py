"""HTTP transport for the same frozen facts the in-process producers bank.

A fact on the wire is the bank row without its asset id. The client validates the
whole answer against the versions it asked for and stores it verbatim: no URL,
host or provider name enters a key, so a remote row and a local row are the same row.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import AbstractContextManager

import httpx
from pydantic import BaseModel, Field, ValidationError

from immich_memories.analysis.editorial_preparation_detectors import DETECTOR_VERSIONS
from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.config_models_inference import InferenceConfig
from immich_memories.triage.heads import HeadFact


class RemoteFactsError(RuntimeError):
    """A bounded, payload-free failure suitable for preparation and preflight."""


class RemoteFact(BaseModel):
    head: str = Field(min_length=1, strict=True)
    version: str = Field(min_length=1, strict=True)
    label: str = Field(min_length=1, strict=True)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class ProducerFacts(BaseModel):
    encoder_key: str = Field(min_length=1, strict=True)
    facts: list[RemoteFact] = Field(min_length=1)

    def bank_facts(self) -> tuple[HeadFact, ...]:
        return tuple(HeadFact(**fact.model_dump()) for fact in self.facts)


class _Envelope(BaseModel):
    producers: dict[str, ProducerFacts]


def offloaded_versions(
    head_versions: Mapping[str, str], producers: Mapping[str, str] | list[str] | tuple[str, ...]
) -> dict[str, str]:
    """The heads the configured producers answer for; the rest stay in process."""
    wanted = set(producers)
    return {
        head: version
        for head, version in head_versions.items()
        if (head in PUBLIC_HEAD_VERSIONS and "heads" in wanted) or head in wanted
    }


def producer_names(head_versions: Mapping[str, str]) -> tuple[str, ...]:
    supported = PUBLIC_HEAD_VERSIONS | DETECTOR_VERSIONS
    if any(supported.get(head) != version for head, version in head_versions.items()):
        raise RemoteFactsError("Remote classifiers do not support the requested head versions")
    return tuple(
        dict.fromkeys("heads" if head in PUBLIC_HEAD_VERSIONS else head for head in head_versions)
    )


class RemoteFactsClient(AbstractContextManager):
    """One reused connection; one preview upload for all missing producers of a picture."""

    def __init__(self, config: InferenceConfig, client: httpx.Client | None = None) -> None:
        self._url = f"{config.facts_base_url}/facts"
        self._client = client or httpx.Client(timeout=config.timeout_seconds, trust_env=False)

    def __exit__(self, *_exc: object) -> None:
        self._client.close()

    def facts(self, image: bytes, head_versions: Mapping[str, str]) -> dict[str, ProducerFacts]:
        names = producer_names(head_versions)
        try:
            response = self._client.post(
                self._url,
                json={"image": base64.b64encode(image).decode("ascii"), "producers": names},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RemoteFactsError(
                f"Remote classifiers returned HTTP {exc.response.status_code}; check service logs"
            ) from None
        except httpx.HTTPError:
            raise RemoteFactsError("Remote classifiers could not be reached or timed out") from None
        try:
            result = _Envelope.model_validate(response.json()).producers
        except (ValidationError, ValueError):
            raise RemoteFactsError("Remote classifiers returned malformed facts") from None
        _validate_contract(result, names, head_versions)
        return result


def _validate_contract(
    result: Mapping[str, ProducerFacts], names: tuple[str, ...], wanted: Mapping[str, str]
) -> None:
    if set(result) != set(names):
        raise RemoteFactsError("Remote classifiers returned a different set of producers")
    seen: dict[str, str] = {}
    for name, producer in result.items():
        allowed = PUBLIC_HEAD_VERSIONS if name == "heads" else {name: DETECTOR_VERSIONS[name]}
        for fact in producer.facts:
            if fact.head in seen or allowed.get(fact.head) != fact.version:
                raise RemoteFactsError(
                    "Remote classifiers returned duplicate or incompatible facts"
                )
            seen[fact.head] = fact.version
    if any(seen.get(head) != version for head, version in wanted.items()):
        raise RemoteFactsError("Remote classifiers omitted a required head or version")
