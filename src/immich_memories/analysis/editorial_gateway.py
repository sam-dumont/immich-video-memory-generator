"""Provider-neutral, synchronous gateway for banked visual editorial calls."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import copy_context
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

import httpx

from immich_memories.analysis.contact_sheets import ContactSheetPage
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    RequestAttemptTrace,
    RequestTrace,
)
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.llm_wire import LLMTransportAttempt
from immich_memories.analysis.provider_failure import (
    RETRY_ATTEMPTS,
    THROTTLE,
    ProviderCredentialRejected,
    announce_retry,
    group_wait,
    provider_failure,
    retry_wait,
)
from immich_memories.analysis.provider_status import watch_provider
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.cache.judgment_cache import VisualJudgmentCache, VisualJudgmentIdentity
from immich_memories.config_models_llm import LLMConfig

__all__ = [
    "BankedVisualAnswer",
    "EditorialGateway",
    "EmptyVisualAnswer",
    "VisualEditorialGateway",
    "VisualEditorialRequest",
]


# Every pass behind this gateway DECIDES something, so it is asked greedily
# rather than sampled. Measured on one real pack, four repeats each: at the
# transport default of 0.3 no two answers matched and one named all 105 tiles
# in the pack; at 0 all four responses were byte-identical. Sampling also makes
# a banked answer a lie -- the cache would return something re-asking would not
# have produced.
EDITORIAL_TEMPERATURE = 0.0

logger = logging.getLogger(__name__)


class EmptyVisualAnswer(ValueError):
    """A completed visual request returned no usable text, without a transport defect."""

    def __init__(self, raw: str) -> None:
        super().__init__("visual editorial answer must be nonblank")
        self.raw = raw


@dataclass(frozen=True)
class VisualEditorialRequest:
    """All non-semantic evidence and versioning supplied to one visual pass."""

    pass_name: str
    pass_version: str
    prompt: str
    prompt_version: str
    schema_version: str
    pages: tuple[ContactSheetPage, ...]
    ordered_input_ids: tuple[str, ...]
    ordered_group_ids: tuple[str, ...]
    grounded_annotations: tuple[str, ...]
    upstream_material: tuple[str, ...]
    render_version: str
    limits: VisionRequestLimits
    thinking: bool = False
    image_detail: str = "low"
    continuation_number: int = 1
    continuation_count: int = 1


@dataclass(frozen=True)
class BankedVisualAnswer:
    """The raw model answer and provenance for pass-specific parsers to own."""

    raw_text: str
    provenance: DecisionProvenance
    original_provenance: DecisionProvenance
    request_trace: RequestTrace


class EditorialGateway(Protocol):
    """The only provider-neutral boundary used by editorial passes."""

    def ask(self, request: VisualEditorialRequest) -> BankedVisualAnswer:
        """Bank and return a complete raw visual answer."""


class VisualEditorialGateway:
    """Synchronous adapter that reuses the established LLM transport.

    `pass_llm_overrides` routes a named pass to its own model — the description
    student behind the same door — while identity keeps each model's answers
    banked apart.
    """

    def __init__(
        self,
        *,
        llm_config: LLMConfig,
        cache_path: Path,
        trace: Trace,
        pass_llm_overrides: Mapping[str, LLMConfig] | None = None,
    ) -> None:
        self.llm_config = llm_config
        self.pass_llm_overrides = dict(pass_llm_overrides or {})
        self.cache = VisualJudgmentCache(cache_path)
        self.trace = trace

    def _config_for(self, pass_name: str) -> LLMConfig:
        return self.pass_llm_overrides.get(pass_name, self.llm_config)

    def close(self) -> None:
        """Release the bank's connections; a later ask quietly reopens them."""
        self.cache.close()

    def request_identity(self, request: VisualEditorialRequest) -> VisualJudgmentIdentity:
        """Share the exact identity with consumers that bank typed completion failures."""
        llm_config = self._config_for(request.pass_name)
        _validated_page_hashes(request.pages)
        return VisualJudgmentIdentity(
            page_bytes=tuple(page.jpeg_bytes for page in request.pages),
            ordered_input_ids=request.ordered_input_ids,
            ordered_group_ids=request.ordered_group_ids,
            annotations=request.grounded_annotations,
            model=llm_config.model,
            thinking=request.thinking,
            image_detail=request.image_detail,
            pass_name=request.pass_name,
            pass_version=request.pass_version,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            render_version=request.render_version,
            layout_versions=tuple(page.layout_version for page in request.pages),
            upstream_material=request.upstream_material,
            request_limits=(
                f"max_pages={request.limits.max_pages_per_request}",
                f"max_output_tokens={request.limits.max_output_tokens}",
            ),
            continuation_identity=(request.continuation_number, request.continuation_count),
            endpoint=llm_config.base_url.rstrip("/"),
        )

    def ask(self, request: VisualEditorialRequest) -> BankedVisualAnswer:
        """Attach exact page bytes once, or reuse their already banked answer."""
        llm_config = self._config_for(request.pass_name)
        page_hashes = _validated_page_hashes(request.pages)
        request_key = self.request_identity(request).key()
        reused = self.cache.answer_for(request_key)
        if reused is not None:
            raw_text, original_serialized = reused
            original = _provenance_from_json(original_serialized)
            provenance = _provenance(
                request, page_hashes, request_key, cache_hit=True, model=llm_config.model
            )
            request_trace = self._record(
                provenance,
                request.pages,
                page_hashes,
                llm_config=llm_config,
                cache_hit=True,
                original_provenance=original,
            )
            return BankedVisualAnswer(raw_text, provenance, original, request_trace)

        provenance = _provenance(
            request, page_hashes, request_key, cache_hit=False, model=llm_config.model
        )
        attempts: list[RequestAttemptTrace] = []
        watch = watch_provider("reader", llm_config)

        def record_attempt(attempt: LLMTransportAttempt) -> None:
            watch(attempt)
            attempts.append(
                RequestAttemptTrace(
                    attempt=attempt.attempt,
                    outcome=attempt.outcome,
                    status_code=attempt.status_code,
                    adaptation=attempt.adaptation,
                )
            )

        try:
            raw_text = self._answered(request, llm_config, record_attempt)
            if not raw_text.strip():
                raise EmptyVisualAnswer(raw_text)
        except Exception as exc:
            failed_trace = self._record(
                provenance,
                request.pages,
                page_hashes,
                llm_config=llm_config,
                cache_hit=False,
                actual_calls=len(attempts),
                attempts=tuple(attempts),
            )
            setattr(exc, "request_trace", failed_trace)  # noqa: B010 - preserve original exception
            raise
        self.cache.remember(request_key, raw_text, _provenance_json(provenance))
        request_trace = self._record(
            provenance,
            request.pages,
            page_hashes,
            llm_config=llm_config,
            cache_hit=False,
            actual_calls=len(attempts),
            attempts=tuple(attempts),
        )
        return BankedVisualAnswer(raw_text, provenance, provenance, request_trace)

    def _answered(
        self,
        request: VisualEditorialRequest,
        llm_config: LLMConfig,
        record_attempt: Callable[[LLMTransportAttempt], None],
    ) -> str:
        """Ask once, waiting out a provider that is only shedding load.

        Every pass behind this gateway drops a failed call quietly -- a picture with
        no facts, a caption that reads `!! asset description failed`, a pair left
        unjudged. That is right for a payload the provider refuses and wrong for a
        rate limit: a reader throttled for a minute would otherwise come out of the
        comparison with fewer facts than its neighbours and read as a worse model.
        """
        held = THROTTLE.pause()
        if held:
            # Another call is already waiting out a rate limit on this key. Joining it
            # beats spending an attempt discovering the same throttle.
            time.sleep(held)
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return _run_sync(
                    query_llm(
                        _provider_prompt(request),
                        llm_config,
                        temperature=EDITORIAL_TEMPERATURE,
                        thinking=request.thinking,
                        images=tuple(page.jpeg_bytes for page in request.pages),
                        image_detail=request.image_detail,
                        transport_observer=record_attempt,
                        require_complete=True,
                        max_tokens=request.limits.max_output_tokens,
                        timeout_seconds=request.limits.timeout_seconds,
                    )
                )
            except httpx.HTTPStatusError as exc:
                refusal = provider_failure(exc, images_attached=bool(request.pages))
                if refusal is None:
                    raise
                if refusal.credential:
                    # Every pass behind this gateway drops a failed call quietly, so a
                    # rejected key would otherwise blank a whole run's captions and facts
                    # without anyone being told. It leaves here as something nobody catches.
                    raise ProviderCredentialRejected(refusal) from exc
                wait = retry_wait(refusal, attempt)
                if wait is None:
                    raise
                announce_retry(logger, refusal, wait, attempt)
                time.sleep(group_wait(refusal, wait))
        raise AssertionError("rate-limited visual request must return or raise")

    def _record(
        self,
        provenance: DecisionProvenance,
        pages: tuple[ContactSheetPage, ...],
        page_hashes: tuple[str, ...],
        *,
        llm_config: LLMConfig,
        cache_hit: bool,
        actual_calls: int = 0,
        original_provenance: DecisionProvenance | None = None,
        attempts: tuple[RequestAttemptTrace, ...] = (),
    ) -> RequestTrace:
        request_trace = RequestTrace(
            provenance=provenance,
            attached_sheet_hashes=page_hashes,
            actual_calls=actual_calls,
            cache_hit=cache_hit,
            tile_count=sum(len(page.tile_refs) for page in pages),
            provider=llm_config.provider,
            model=llm_config.model,
            attempts=attempts,
            original_provenance=original_provenance,
        )
        self.trace.record_request(request_trace)
        return request_trace


def _validated_page_hashes(pages: tuple[ContactSheetPage, ...]) -> tuple[str, ...]:
    if not pages:
        raise ValueError("visual editorial requests need at least one page")
    hashes = tuple(sha256(page.jpeg_bytes).hexdigest() for page in pages)
    if any(digest != page.sha256 for digest, page in zip(hashes, pages, strict=True)):
        raise ValueError("contact sheet digest does not match its exact bytes")
    return hashes


def _provider_prompt(request: VisualEditorialRequest) -> str:
    if not request.grounded_annotations:
        return request.prompt
    annotations = json.dumps(
        request.grounded_annotations,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{request.prompt}\n\nGrounded annotations (ordered JSON):\n{annotations}"


def _run_sync(coroutine: object) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)  # type: ignore[arg-type]
    result: list[str] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(coroutine))  # type: ignore[arg-type]
        except BaseException as exc:  # WHY: preserve the provider exception across the bridge
            failure.append(exc)

    context = copy_context()
    worker = threading.Thread(target=lambda: context.run(run), daemon=True)
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    return result[0]


def _provenance(
    request: VisualEditorialRequest,
    page_hashes: tuple[str, ...],
    request_key: str,
    *,
    cache_hit: bool,
    model: str,
) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name=request.pass_name,
        pass_version=request.pass_version,
        schema_version=request.schema_version,
        model_identity=model,
        input_ids=request.ordered_input_ids,
        sheet_hashes=page_hashes,
        request_key=request_key,
        cache_hit=cache_hit,
    )


def _provenance_json(provenance: DecisionProvenance) -> str:
    return json.dumps(provenance.__dict__, sort_keys=True)


def _provenance_from_json(serialized: str) -> DecisionProvenance:
    values = json.loads(serialized)
    return DecisionProvenance(
        pass_name=values["pass_name"],
        pass_version=values["pass_version"],
        schema_version=values["schema_version"],
        model_identity=values["model_identity"],
        input_ids=tuple(values["input_ids"]),
        sheet_hashes=tuple(values["sheet_hashes"]),
        request_key=values["request_key"],
        cache_hit=bool(values["cache_hit"]),
    )
