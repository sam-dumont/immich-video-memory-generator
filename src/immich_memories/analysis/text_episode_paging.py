"""How an episode request is cut into pages and packed into prompts under the model's limits.

A reading is asked for in packs of whole episodes; an episode too large for one prompt is split
into pages of its own, and every pack is sized so its answer fits the output budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from immich_memories.analysis.text_episode_answers import _EpisodeRequestScope
from immich_memories.analysis.text_episode_prompt import EpisodePromptFacts, episode_prompt
from immich_memories.store.episode_readings import EpisodeReadingIdentity

TEXT_EPISODE_MAX_OUTPUT_TOKENS = 4_000
_DEFAULT_MAX_PROMPT_CHARS = 24_000
_DEFAULT_MIN_OUTPUT_TOKENS = 512
_DEFAULT_OUTPUT_BASE_TOKENS = 200
_DEFAULT_OUTPUT_TOKENS_PER_ROW = 128
_DEFAULT_OUTPUT_TOKENS_PER_ASSET = 12


@dataclass(frozen=True)
class TextEpisodeRequestLimits:
    """Bound one serialized episode request below the model's safe context."""

    max_prompt_chars: int = _DEFAULT_MAX_PROMPT_CHARS
    max_assets_per_page: int = 90
    max_output_tokens: int = TEXT_EPISODE_MAX_OUTPUT_TOKENS
    min_output_tokens: int = _DEFAULT_MIN_OUTPUT_TOKENS
    output_base_tokens: int = _DEFAULT_OUTPUT_BASE_TOKENS
    output_tokens_per_row: int = _DEFAULT_OUTPUT_TOKENS_PER_ROW
    output_tokens_per_asset: int = _DEFAULT_OUTPUT_TOKENS_PER_ASSET
    unread_retry_rounds: int = 2

    def __post_init__(self) -> None:
        if (
            self.max_prompt_chars <= 0
            or self.max_assets_per_page <= 0
            or self.max_output_tokens <= 0
            or self.min_output_tokens <= 0
            or self.output_base_tokens <= 0
            or self.output_tokens_per_row <= 0
            or self.output_tokens_per_asset <= 0
            or self.unread_retry_rounds < 0
        ):
            raise ValueError("episode request limits must be positive")
        if self.min_output_tokens > self.max_output_tokens:
            raise ValueError("episode minimum output budget cannot exceed its ceiling")


def episode_page_scopes(
    identity: EpisodeReadingIdentity,
    full_asset_ids: tuple[str, ...],
    *,
    max_assets_per_page: int,
    facts: EpisodePromptFacts,
    max_prompt_chars: int,
) -> tuple[_EpisodeRequestScope, ...]:
    whole = _EpisodeRequestScope(
        identity=identity,
        full_asset_ids=full_asset_ids,
        page_asset_ids=full_asset_ids,
        page_number=1,
        page_count=1,
    )
    if (
        len(full_asset_ids) <= max_assets_per_page
        and len(episode_prompt((whole,), facts)) <= max_prompt_chars
    ):
        return (whole,)

    pages: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    conservative_page_count = max(len(full_asset_ids), 2)
    for asset_id in full_asset_ids:
        proposed = (*current, asset_id)
        scope = _EpisodeRequestScope(
            identity=identity,
            full_asset_ids=full_asset_ids,
            page_asset_ids=proposed,
            page_number=conservative_page_count,
            page_count=conservative_page_count,
        )
        if current and (
            len(proposed) > max_assets_per_page
            or len(episode_prompt((scope,), facts)) > max_prompt_chars
        ):
            pages.append(current)
            current = (asset_id,)
        else:
            current = proposed
    if current:
        pages.append(current)

    return tuple(
        _EpisodeRequestScope(
            identity=identity,
            full_asset_ids=full_asset_ids,
            page_asset_ids=page_asset_ids,
            page_number=page_number,
            page_count=len(pages),
        )
        for page_number, page_asset_ids in enumerate(pages, start=1)
    )


def pack_episode_scopes(
    scopes: tuple[_EpisodeRequestScope, ...],
    facts: EpisodePromptFacts,
    *,
    limits: TextEpisodeRequestLimits,
) -> tuple[tuple[tuple[_EpisodeRequestScope, ...], ...], tuple[_EpisodeRequestScope, ...]]:
    packs: list[tuple[_EpisodeRequestScope, ...]] = []
    oversized: list[_EpisodeRequestScope] = []
    current: tuple[_EpisodeRequestScope, ...] = ()
    for scope in scopes:
        if (
            len(episode_prompt((scope,), facts)) > limits.max_prompt_chars
            or episode_completion_budget((scope,), limits) > limits.max_output_tokens
        ):
            oversized.append(scope)
            continue
        if scope.page_count > 1:
            if current:
                packs.append(current)
                current = ()
            packs.append((scope,))
            continue
        proposed = (*current, scope)
        if current and (
            len(episode_prompt(proposed, facts)) > limits.max_prompt_chars
            or episode_completion_budget(proposed, limits) > limits.max_output_tokens
        ):
            packs.append(current)
            current = (scope,)
        else:
            current = proposed
    if current:
        packs.append(current)
    return tuple(packs), tuple(oversized)


def episode_completion_budget(
    scopes: tuple[_EpisodeRequestScope, ...],
    limits: TextEpisodeRequestLimits,
) -> int:
    """Size generation from demanded response rows and possible Cull aliases."""
    estimated = (
        limits.output_base_tokens
        + limits.output_tokens_per_row * len(scopes)
        + limits.output_tokens_per_asset * sum(len(scope.page_asset_ids) for scope in scopes)
    )
    return max(limits.min_output_tokens, estimated)
