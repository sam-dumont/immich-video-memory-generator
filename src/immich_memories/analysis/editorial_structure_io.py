"""Production text and reranker adapters for the shared structure planner."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from immich_memories.analysis.editorial_async_bridge import _run_sync
from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_reranking import rerank
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure
from immich_memories.analysis.editorial_text_gateway import QueryTextRequester
from immich_memories.security import write_secret_file


class StructureTextJudge:
    def __init__(self, config, out: Path, *, cache_path: Path | None = None) -> None:
        self.config = config
        self.out = out
        self.cache_path = (
            cache_path if cache_path is not None else out.parent / "text-judgments.sqlite"
        )
        self.calls: list[dict] = []
        self.requester = QueryTextRequester(
            json_failure_observer=self._record_json_failure,
            json_decoding_observer=self._record_json_decoding,
        )

    def _record_json_decoding(self, record: dict) -> None:
        """Preserve the original complete reply when its representation is normalized."""
        path = (
            self.out
            / "calls"
            / f"{len(self.calls) + 1:02d}-json-decoding-{record['max_tokens']}.private.json"
        )
        write_secret_file(path, json.dumps(record, ensure_ascii=False, indent=2))

    def _record_json_failure(self, failure: dict) -> None:
        """Retain failed raw replies without accepting them into the judgment bank."""
        path = (
            self.out
            / "calls"
            / f"{len(self.calls) + 1:02d}-json-failure-{failure['max_tokens']}.private.json"
        )
        write_secret_file(path, json.dumps(failure, ensure_ascii=False, indent=2))

    def ask(
        self,
        stage: str,
        prompt: str,
        max_tokens: int = 260,
        *,
        json_object: bool = False,
        json_fields: tuple[str, ...] = (),
        json_empty_array_pairs: tuple[tuple[str, str], ...] = (),
        accepts: Callable[[str], bool] | None = None,
    ) -> str:
        n = len(self.calls) + 1
        d = self.out / "calls"
        d.mkdir(mode=0o700, exist_ok=True)
        write_secret_file(d / f"{n:02d}-{stage}.request.private.txt", prompt)
        request = TextRequest(
            prompt=prompt,
            llm_config=self.config.llm,
            cache_path=self.cache_path,
            max_tokens=max_tokens,
            timeout_seconds=int(self.config.llm.timeout_seconds),
            thinking=False,
            json_object=json_object,
            json_fields=json_fields,
            json_empty_array_pairs=json_empty_array_pairs,
        )
        started = time.monotonic()
        try:
            call = _run_sync(self.requester.request(request, accepts=accepts))
        except TextCompletionFailure as exc:
            write_secret_file(
                d / f"{n:02d}-{stage}.failure.private.json",
                json.dumps(exc.as_record(), ensure_ascii=False, indent=2),
            )
            self.calls.append(
                {
                    "stage": stage,
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "warning": str(exc),
                    "cache_hit": exc.cache_hit,
                    "judgment_key": request.judgment_key,
                    "response_contract": "bounded failure",
                }
            )
            raise
        write_secret_file(d / f"{n:02d}-{stage}.response.private.txt", call.raw)
        self.calls.append(
            {
                "stage": stage,
                "wall_seconds": round(call.wall_seconds, 3),
                "warning": call.warning,
                "cache_hit": call.cache_hit,
                "judgment_key": request.judgment_key,
                "response_contract": "complete final JSON payload"
                if json_object
                else "complete transport",
                **({"json_fields": list(json_fields)} if json_fields else {}),
                **(
                    {"json_empty_array_pairs": sorted(json_empty_array_pairs)}
                    if json_empty_array_pairs
                    else {}
                ),
            }
        )
        return call.raw


class StructureReranker:
    """Open transport only for an uncached retrieval request."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str = "Qwen3-Reranker-0.6B-4bit",
    ) -> None:
        self.endpoint = (
            endpoint
            if endpoint is not None
            else os.environ.get("OMLX_BASE_URL", "http://localhost:9999/v1")
        )
        self.api_key = api_key if api_key is not None else os.environ.get("OMLX_API_KEY", "")
        self.model = model

    @property
    def identity(self) -> dict[str, str]:
        return {"endpoint": self.endpoint.rstrip("/"), "model": self.model}

    def __call__(self, query: str, documents: tuple[str, ...]) -> dict[int, float]:
        with httpx.Client(timeout=180, trust_env=False) as client:
            results, _usage = rerank(
                client,
                endpoint=self.endpoint,
                api_key=self.api_key,
                model=self.model,
                query=query,
                documents=documents,
            )
        return {row.index: row.relevance_score for row in results}
