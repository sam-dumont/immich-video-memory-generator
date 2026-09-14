"""Demand literal picture observations without asking a model to edit the memory."""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.contact_sheets import ContactSheetPage, TileRef
from immich_memories.analysis.editorial_bound_sample import BoundVideoSample
from immich_memories.analysis.editorial_gateway import (
    EmptyVisualAnswer,
    VisualEditorialGateway,
    VisualEditorialRequest,
)
from immich_memories.analysis.editorial_model_attestation import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_MODEL_ID,
    EXPECTED_REPO_ID,
    EXPECTED_REVISION,
    EXPECTED_TREE_SHA256,
)
from immich_memories.analysis.llm_providers import resolved_llm_config
from immich_memories.analysis.llm_text_identity import text_model_identity
from immich_memories.analysis.llm_wire import LLMIncompleteResponse
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.strict_json import final_json_object
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.config_models_llm import LLMConfig

STAGE_NAME = "selected-picture-facts"
TILE_VERSION = "selected-picture-facts-tile-800px-jpeg-q90-v1"
STAGE_VERSION = "selected-picture-facts-v2-uncovered-person"
PROMPT_VERSION = "selected-picture-facts-prompt-v2-uncovered-person"
SCHEMA_VERSION = "selected-picture-facts-schema-v2-uncovered-person"
COMPLETION_POLICY = "one-complete-visual-response-or-banked-incomplete-v1"
MAX_OUTPUT_TOKENS = 300
BODY_STATES = frozenset({"yes", "no", "unclear"})
FIELDS = (
    "uncovered_person",
    "subject_action",
    "clothing_exposure",
    "composition",
    "visible_records",
)
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        field: (
            {"type": "string", "enum": sorted(BODY_STATES)}
            if field == "uncovered_person"
            else {"type": "string", "minLength": 1}
        )
        for field in FIELDS
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}
PROMPT = (
    "Inspect this one image. Return only one flat JSON object with these five string fields "
    "in this order: uncovered_person, subject_action, clothing_exposure, composition, "
    "visible_records. "
    "First, look at the people in the photograph. uncovered_person must be yes, no, or unclear. "
    "Use yes if anyone is visibly shirtless/bare-torsoed, nude, or wearing only underwear. "
    "Bare arms, shoulders, or legs with an ordinary worn top and shorts do not count. "
    "Read actual clothing in the image. A clothed torso is not bare merely because its shape "
    "is visible. Trousers do not cover a genuinely shirtless torso. "
    "Use no when no person meets that description, including when no people are visible. "
    "Use unclear when the visible image does not permit this body observation. "
    "Do not infer what is hidden outside the frame or under a garment or cover. "
    "uncovered_person is a direct visual body observation, not an audience/export verdict; "
    "record it separately from general exposure wording in the descriptive fields. "
    "subject_action: describe the main visible people or objects and their actions. "
    "clothing_exposure: describe visible garments or coverings and visible body areas; "
    "distinguish what can be seen from what is outside the frame or hidden. "
    "composition: describe the framing and whether this is one photograph or multiple panels. "
    "visible_records: describe any visible printed material, identifiers or document layout "
    "and whether its text is legible; omit exact personal values. "
    "Use not visible or unclear in the four descriptive fields where evidence is absent. "
    "Do not guess identities, relationships, an event, exact place, a diagnosis, or anything "
    "hidden. Keep each descriptive field to one concise sentence grounded in the image."
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def picture_tile(preview: bytes) -> bytes:
    """Retain detail in demanded picture observations without changing library captions."""
    with Image.open(io.BytesIO(preview)) as source:
        image = source.convert("RGB")
        image.thumbnail((800, 800), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def _producer(config: LLMConfig) -> dict[str, Any]:
    artifact = (
        {
            "repository": EXPECTED_REPO_ID,
            "revision": EXPECTED_REVISION,
            "config_sha256": EXPECTED_CONFIG_SHA256,
            "tree_sha256": EXPECTED_TREE_SHA256,
        }
        if config.model == EXPECTED_MODEL_ID
        else {"model": config.model}
    )
    return {
        "pass_version": STAGE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "tile_version": TILE_VERSION,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "schema_sha256": _digest(RESPONSE_SCHEMA),
        "model_identity": text_model_identity(config, thinking=False),
        "artifact": artifact,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "image_detail": "high" if config.send_image_detail else None,
        "completion_policy": COMPLETION_POLICY,
    }


def picture_observation_request(
    *,
    config: LLMConfig,
    image_dir: Path,
    asset_id: str,
    tile: bytes,
    input_sha: str,
    sample: BoundVideoSample | None = None,
) -> VisualEditorialRequest:
    """Build the identical own-picture request for observation and provenance validation."""
    image_sha = hashlib.sha256(tile).hexdigest()
    path = image_dir / f"{image_sha}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_bytes(tile)
        path.chmod(0o600)
    page = ContactSheetPage(
        sheet_id=f"picture-facts-{image_sha}",
        path=path,
        jpeg_bytes=tile,
        sha256=image_sha,
        tile_refs=(TileRef(1, asset_id),),
        layout_version=TILE_VERSION,
    )
    return VisualEditorialRequest(
        pass_name=STAGE_NAME,
        pass_version=STAGE_VERSION,
        prompt=PROMPT,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        pages=(page,),
        ordered_input_ids=(asset_id,),
        ordered_group_ids=(),
        grounded_annotations=(),
        upstream_material=(_digest(_producer(config)), input_sha)
        + ((sample.key,) if sample is not None else ()),
        render_version=TILE_VERSION,
        limits=VisionRequestLimits(
            max_pages_per_request=1,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout_seconds=config.timeout_seconds,
        ),
        thinking=False,
        image_detail="high",
    )


class PictureFactsProvider:
    """One exact cached observation per demanded member; scope never enters the question."""

    def __init__(
        self,
        *,
        llm_config: LLMConfig,
        cache_path: Path,
        preview_bytes: Callable[[str], bytes | None],
        trace: Trace,
    ) -> None:
        self._config = resolved_llm_config(llm_config).model_copy(deep=True)
        self._producer = _producer(self._config)
        self._preview_bytes = preview_bytes
        self._trace = trace
        self._image_dir = Path(cache_path).parent / "picture-facts-images"
        self._gateway = VisualEditorialGateway(
            llm_config=self._config,
            cache_path=cache_path,
            trace=trace,
        )
        self._memo: dict[tuple[str, str], dict[str, Any]] = {}
        self._metrics: dict[str, int | float | None] = {
            "requested_members": 0,
            "unique_members": 0,
            "memo_hits": 0,
            "cache_hits": 0,
            "http_attempts": 0,
            "inference_calls": 0,
            "images_sent": 0,
            "wall_seconds": 0.0,
            "unavailable_members": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    def observe(self, asset_id: str) -> dict[str, Any]:
        """Return stable source facts or an explicit evidence gap, never a sharing verdict."""
        if not asset_id.strip():
            raise ValueError("picture observation needs an asset ID")
        return self._observed(("asset", asset_id), lambda: self._observe(asset_id))

    def observe_sample(self, sample: BoundVideoSample, frame: bytes) -> dict[str, Any]:
        """Observe supplied real video pixels with separate source and sample identities.

        The source provider must authorize the attached material and conserve the frame.
        This method neither fetches a poster nor reuses a parent still's observation.
        """
        if not isinstance(sample, BoundVideoSample):
            raise TypeError("attached observation requires a bound video sample")
        if not isinstance(frame, bytes) or hashlib.sha256(frame).hexdigest() != sample.frame_sha256:
            raise ValueError("supplied frame differs from its conserved sample")
        return self._observed(
            ("sample", sample.key),
            lambda: self._observe(sample.source_id, sample=sample, frame=frame),
        )

    def _observed(
        self, memo_key: tuple[str, str], observe: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        self._increment("requested_members")
        if memo_key in self._memo:
            self._increment("memo_hits")
            return deepcopy(self._memo[memo_key])
        self._increment("unique_members")
        started, trace_start = time.monotonic(), len(self._trace.requests)
        active = llm_metrics.active()
        with nullcontext(active) if active is not None else llm_metrics.collecting() as counters:
            assert counters is not None
            mark = counters.snapshot()
            try:
                result = observe()
                self._memo[memo_key] = result
                if result["status"] != "available":
                    self._increment("unavailable_members")
                return deepcopy(result)
            finally:
                self._record_cost(counters.since(mark), trace_start)
                self._metrics["wall_seconds"] = float(self._metrics["wall_seconds"] or 0) + (
                    time.monotonic() - started
                )

    def _observe(
        self, asset_id: str, *, sample: BoundVideoSample | None = None, frame: bytes | None = None
    ) -> dict[str, Any]:
        preview = frame if sample is not None else self._preview_bytes(asset_id)
        base: dict[str, Any] = {
            "producer": self._producer,
            "identity": None,
            "facts": {},
            "input_sha256": hashlib.sha256(preview).hexdigest() if preview else None,
        }
        if sample is not None:
            base.update(
                source_asset_id=asset_id, sample_id=sample.key, sample_binding=sample.as_dict()
            )
        if not preview:
            return base | {"status": "unavailable", "reason": "missing_preview"}
        try:
            tile = picture_tile(preview)
        except OSError:  # This scope only decodes supplied bytes; acquisition errors propagate.
            return base | {"status": "unavailable", "reason": "unreadable_preview"}
        request = self._request(asset_id, tile, base["input_sha256"], sample=sample)
        key = self._gateway.request_identity(request).key()
        base.update(identity=key, image_sha256=request.pages[0].sha256)
        failure = self._gateway.cache.completion_failure_for(key)
        if failure is not None:
            if not self._valid_failure(failure, base):
                raise ValueError("banked picture completion failure has invalid evidence identity")
            self._cache_hit()
            return failure
        try:
            answer = self._gateway.ask(request)
        except (LLMIncompleteResponse, EmptyVisualAnswer) as exc:
            failure = base | {
                "status": "completion_failure",
                "reason": "empty_response"
                if isinstance(exc, EmptyVisualAnswer)
                else "incomplete_response",
                "raw_response": exc.raw,
            }
            self._gateway.cache.remember_completion_failure(key, failure)
            return failure
        if answer.provenance.cache_hit:
            self._cache_hit()
        raw = answer.raw_text
        fields = final_json_object(raw)
        if (
            not isinstance(fields, dict)
            or set(fields) != set(FIELDS)
            or any(not isinstance(value, str) or not value.strip() for value in fields.values())
            or fields["uncovered_person"] not in BODY_STATES
        ):
            return base | {
                "status": "invalid",
                "reason": "invalid_factual_fields",
                "raw_response": raw,
            }
        facts = {field: fields[field].strip() for field in FIELDS}
        return base | {
            "status": "available",
            "facts": facts,
            "description": " ".join(f"{field}: {facts[field]}" for field in FIELDS),
            "raw_response": raw,
        }

    def _request(
        self, asset_id: str, tile: bytes, input_sha: str, *, sample: BoundVideoSample | None = None
    ) -> VisualEditorialRequest:
        return picture_observation_request(
            config=self._config,
            image_dir=self._image_dir,
            asset_id=asset_id,
            tile=tile,
            input_sha=input_sha,
            sample=sample,
        )

    @staticmethod
    def _valid_failure(failure: dict[str, Any], base: dict[str, Any]) -> bool:
        return (
            set(failure) == {*base, "status", "reason", "raw_response"}
            and all(failure.get(key) == value for key, value in base.items())
            and failure.get("status") == "completion_failure"
            and failure.get("reason") in {"incomplete_response", "empty_response"}
            and isinstance(failure.get("raw_response"), str)
        )

    def _cache_hit(self) -> None:
        self._increment("cache_hits")
        llm_metrics.record_cache_hit()

    def _increment(self, name: str, amount: int = 1) -> None:
        self._metrics[name] = int(self._metrics[name] or 0) + amount

    def _record_cost(self, spent: llm_metrics.LLMCounters, trace_start: int) -> None:
        traces = self._trace.requests[trace_start:]
        attempts = sum(trace.actual_calls for trace in traces)
        self._increment("http_attempts", attempts)
        self._increment(
            "images_sent", sum(trace.actual_calls * trace.tile_count for trace in traces)
        )
        self._increment("inference_calls", spent.calls)
        for name in ("prompt_tokens", "completion_tokens"):
            count = getattr(spent, name)
            if attempts and not count:
                self._metrics[name] = None  # The transport did not report usable token counts.
            elif self._metrics[name] is not None:
                self._increment(name, count)

    def metrics(self) -> dict[str, int | float | None]:
        """Actual work and reuse, kept outside every semantic observation record."""
        return self._metrics | {
            "wall_seconds": round(float(self._metrics["wall_seconds"] or 0), 3),
        }

    def close(self) -> None:
        """Release the existing visual answer and failure bank connections."""
        self._gateway.close()
