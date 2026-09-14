"""Allowlisted public aggregates from the anonymisation in PR #830."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Number = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Case = Literal[
    "monthly",
    "person",
    "multi_person",
    "special_day",
    "trip",
    "year",
    "season",
    "holiday",
    "on_this_day",
    "album",
    "broad_month_control",
    "long_month_control",
]


class PublicData(BaseModel):
    """Unknown keys and coerced values never become public data."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ReaderComparison(PublicData):
    local_model_seconds: Number
    hosted_model_seconds: Number
    hosted_model: Literal["GPT-4.1 mini"]
    recorded_token_cost_usd: Number
    price_basis: Literal["Recorded benchmark token estimate, not an invoice or current price quote"]
    cache_states_matched: bool


class CostBenchmark(PublicData):
    schema_id: Literal["anonymous-cost-benchmark-v2"] = Field(alias="schema")
    benchmark_date: Literal["2026-09-12"]
    scope: Literal["Single monthly selection, no render or music"]
    reader_comparison: ReaderComparison
    rules_model_api_fee_usd: Number
    unmeasured: list[
        Literal[
            "Electricity",
            "Hardware amortization",
            "Hosted costs for other products",
            "Controlled provider quality ranking",
        ]
    ]


class HostRun(PublicData):
    host: Literal["workstation", "celeron_nas", "kubernetes"]
    reader: Literal["rules", "remote_lan_model"]
    tier: Literal["metadata_only", "no_captions"]
    cache: Literal["cold", "warm", "mixed", "detector_backfill"]
    seconds: Number


class Preparation(PublicData):
    previews: Number
    pixels: Number


class HostPreparation(PublicData):
    workstation: Preparation
    celeron_nas: Preparation
    kubernetes: Preparation


class HostBenchmark(PublicData):
    schema_id: Literal["anonymous-host-benchmark-v2"] = Field(alias="schema")
    benchmark_date: Literal["2026-09-12"]
    benchmark_pictures: Count
    request_seconds: Number
    rendered: bool
    runs: list[HostRun]
    fresh_preparation_stage_seconds: HostPreparation
    limitations: list[
        Literal[
            "Single observations",
            "NAS container lifetime versus workstation/cluster CLI time",
            "Model files pre-staged",
            "External reader cache and context differences",
            "Source/dependency overlays for original cluster test",
        ]
    ]


class Selection(PublicData):
    case: Case
    profile: Literal["rules_metadata", "rules_classifiers"]
    first_selection_seconds: Number
    repeat_selection_seconds: Number
    historical_model_selection_seconds: Number
    selected_clips: Count
    selected_content_seconds: Number
    model_requests: Count
    repeat_selection_identical: bool
    reference_asset_overlap_fraction: Fraction
    known_occasion_retention_fraction: Fraction
    occasion_inventory_complete: bool


class Film(PublicData):
    case: Case
    whole_cli_seconds: Number
    film_seconds: Number
    codec: Literal["hevc"]
    width: Count
    height: Count
    full_decode: Literal["passed"]
    model_requests: Count
    model_api_fee_usd: Number


class CapabilityBenchmark(PublicData):
    schema_id: Literal["anonymous-capability-benchmark-v2"] = Field(alias="schema")
    benchmark_date: Literal["2026-09-12"]
    scope: Literal[
        "Ten standard products and two anonymous allocation controls; single observations, "
        "not a representative quality study."
    ]
    conditions: list[
        Literal[
            "Workstation selection matrix; first route invocation is not a universal cold start.",
            "Classifier facts partly precomputed; historical model references have mixed cache states.",
            "Film runs reuse warm preparation caches and include rendering and bundled music.",
            "Occasion retention is a lower bound where the reference inventory is incomplete.",
        ]
    ]
    selection: list[Selection]
    films: list[Film]
    unmeasured: list[
        Literal[
            "Full product-by-host matrix",
            "Hosted cost for every product",
            "Electricity and hardware cost",
            "Cold full-tier end-to-end run",
            "Controlled cross-provider quality ranking",
        ]
    ]


_PUBLIC_DATA = TypeAdapter(CostBenchmark | HostBenchmark | CapabilityBenchmark)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def valid_research_data(content: str) -> bool:
    """Reject fields, prose and identities outside the reviewed public vocabulary."""
    try:
        _PUBLIC_DATA.validate_python(json.loads(content, object_pairs_hook=_unique_object))
    except (ValueError, RecursionError):
        return False
    return True
