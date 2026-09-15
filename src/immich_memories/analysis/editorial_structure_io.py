"""Production text and reranker adapters for the shared structure planner."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from immich_memories.analysis.editorial_async_bridge import _run_sync
from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_reranking import rerank
from immich_memories.analysis.editorial_text_failures import (
    StageCallFailure,
    TextCompletionFailure,
)
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

    def map_independent(self, work, items):
        """Run independent prompt chains with isolated, source-ordered call records."""
        from immich_memories.analysis.editorial_reader_concurrency import run_reader_jobs

        return run_reader_jobs(self, work, items)

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

    def record_failure(self, stage: str, record: Mapping[str, Any]) -> None:
        """Retain a stage's own exhausted envelope recovery, which the gateway never sees.

        The gateway records only what it can judge itself. A page the asking stage could
        not read left nothing in calls/ at all, so a failed run showed a decoder message
        and no way to tell which stage produced it.
        """
        path = self.out / "calls" / f"{len(self.calls):02d}-json-failure-{stage}.private.json"
        write_secret_file(path, json.dumps(dict(record), ensure_ascii=False, indent=2))

    def _failed_call(
        self,
        n: int,
        stage: str,
        failure: TextCompletionFailure | StageCallFailure,
        request: TextRequest,
        *,
        seconds: float,
        cache_hit: bool = False,
    ) -> None:
        """One shape for every call that ended without an answer: artifact, then call row."""
        write_secret_file(
            self.out / "calls" / f"{n:02d}-{stage}.failure.private.json",
            json.dumps(failure.as_record(), ensure_ascii=False, indent=2),
        )
        self.calls.append(
            {
                "stage": stage,
                "wall_seconds": round(seconds, 3),
                "warning": str(failure),
                "cache_hit": cache_hit,
                "judgment_key": request.judgment_key,
                "response_contract": "bounded failure",
            }
        )

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
            self._failed_call(
                n, stage, exc, request, seconds=time.monotonic() - started, cache_hit=exc.cache_hit
            )
            raise
        except Exception as exc:
            # A connection the provider dropped reaches here with nothing recorded and,
            # for httpx read errors, nothing to print either. Name it before it travels.
            failure = StageCallFailure(stage, call=n, cause=exc, seconds=time.monotonic() - started)
            self._failed_call(n, stage, failure, request, seconds=failure.seconds)
            raise failure from exc
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
