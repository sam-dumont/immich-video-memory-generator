"""Provider-neutral, synchronous gateway for banked visual editorial calls."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from immich_memories.analysis.contact_sheets import ContactSheetPage
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    RequestAttemptTrace,
    RequestTrace,
)
from immich_memories.analysis.llm_query import LLMTransportAttempt, query_llm
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.cache.judgment_cache import VisualJudgmentCache, VisualJudgmentIdentity
from immich_memories.config_models_llm import LLMConfig

__all__ = [
    "BankedVisualAnswer",
    "EditorialGateway",
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
    """Synchronous adapter that reuses the established LLM transport."""

    def __init__(self, *, llm_config: LLMConfig, cache_path: Path, trace: Trace) -> None:
        self.llm_config = llm_config
        self.cache = VisualJudgmentCache(cache_path)
        self.trace = trace

    def ask(self, request: VisualEditorialRequest) -> BankedVisualAnswer:
        """Attach exact page bytes once, or reuse their already banked answer."""
        page_hashes = _validated_page_hashes(request.pages)
        identity = VisualJudgmentIdentity(
            page_bytes=tuple(page.jpeg_bytes for page in request.pages),
            ordered_input_ids=request.ordered_input_ids,
            ordered_group_ids=request.ordered_group_ids,
            annotations=request.grounded_annotations,
            model=self.llm_config.model,
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
                f"timeout_seconds={request.limits.timeout_seconds}",
            ),
            continuation_identity=(request.continuation_number, request.continuation_count),
            endpoint=self.llm_config.base_url.rstrip("/"),
        )
        request_key = identity.key()
        reused = self.cache.answer_for(request_key)
        if reused is not None:
            raw_text, original_serialized = reused
            original = _provenance_from_json(original_serialized)
            provenance = _provenance(
                request, page_hashes, request_key, cache_hit=True, model=self.llm_config.model
            )
            request_trace = self._record(
                provenance,
                request.pages,
                page_hashes,
                cache_hit=True,
                original_provenance=original,
            )
            return BankedVisualAnswer(raw_text, provenance, original, request_trace)

        provenance = _provenance(
            request, page_hashes, request_key, cache_hit=False, model=self.llm_config.model
        )
        attempts: list[RequestAttemptTrace] = []

        def record_attempt(attempt: LLMTransportAttempt) -> None:
            attempts.append(
                RequestAttemptTrace(
                    attempt=attempt.attempt,
                    outcome=attempt.outcome,
                    status_code=attempt.status_code,
                    adaptation=attempt.adaptation,
                )
            )

        try:
            raw_text = _run_sync(
                query_llm(
                    _provider_prompt(request),
                    self.llm_config,
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
            if not raw_text.strip():
                raise ValueError("visual editorial answer must be nonblank")
        except Exception as exc:
            failed_trace = self._record(
                provenance,
                request.pages,
                page_hashes,
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
            cache_hit=False,
            actual_calls=len(attempts),
            attempts=tuple(attempts),
        )
        return BankedVisualAnswer(raw_text, provenance, provenance, request_trace)

    def _record(
        self,
        provenance: DecisionProvenance,
        pages: tuple[ContactSheetPage, ...],
        page_hashes: tuple[str, ...],
        *,
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
            provider=self.llm_config.provider,
            model=self.llm_config.model,
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

    worker = threading.Thread(target=run, daemon=True)
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
