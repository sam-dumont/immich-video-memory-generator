"""Validated reranking transport shared by production and evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float


def parse_rerank_response(payload: object, *, document_count: int) -> tuple[RerankResult, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("reranker response has no results array")
    results: list[RerankResult] = []
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            raise ValueError("reranker result is not an object")
        index = raw.get("index")
        score = raw.get("relevance_score")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < document_count
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            raise ValueError("reranker result index or score is invalid")
        results.append(RerankResult(index=index, relevance_score=float(score)))
    if len(results) != document_count or {row.index for row in results} != set(
        range(document_count)
    ):
        raise ValueError("reranker response does not conserve document indices")
    if any(
        left.relevance_score < right.relevance_score
        for left, right in zip(results, results[1:], strict=False)
    ):
        raise ValueError("reranker response is not sorted by descending score")
    return tuple(results)


def rerank(
    client: httpx.Client,
    *,
    endpoint: str,
    api_key: str,
    model: str,
    query: str,
    documents: tuple[str, ...],
) -> tuple[tuple[RerankResult, ...], dict[str, object]]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = {
        "model": model,
        "query": query,
        "documents": list(documents),
        "top_n": len(documents),
        "return_documents": False,
    }
    response = client.post(
        endpoint.rstrip("/") + "/rerank",
        headers=headers,
        json=request,
    )
    response.raise_for_status()
    payload = response.json()
    results = parse_rerank_response(payload, document_count=len(documents))
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return results, usage if isinstance(usage, dict) else {}
