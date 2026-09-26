"""The three product tiers: CPU classifiers, light GPU models, and an added prose LLM.

`tier: auto` resolves inference capability and the configured LLM, then sets one contract
for preparation and selection. Only ``full`` uses an LLM for selection:

* ``nas``: inexpensive CPU heads and detectors, rules, no captions, no Laya.
* ``gpu``: every light model. The caption server, the heads and detectors, and Laya for the
  sharing question. Selection still uses the rules reader.
* ``full``: the ``gpu`` tier plus an LLM for prose and polish. It refuses to load without the
  LLM's ``base_url`` and ``model``.

Configured text features (titles and music mood) work on every tier. The sharing question
never goes to an LLM on any tier.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from immich_memories.config_compute import inference_acceleration

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

ProductTier = Literal["nas", "gpu", "full"]
TierSetting = Literal["auto", ProductTier]

# (section path, field) -> value, per tier. The section path is walked from the Config.
_READER = ("editorial",), "reader"
_PREPARATION = ("editorial", "preparation"), "tier"
_LAYA = ("editorial",), "laya_audience"

TIERS: dict[str, dict[tuple[tuple[str, ...], str], Any]] = {
    "nas": {_READER: "rules", _PREPARATION: "no_captions", _LAYA: False},
    "gpu": {_READER: "rules", _PREPARATION: "full", _LAYA: True},
    "full": {_READER: "model", _PREPARATION: "full", _LAYA: True},
}


def nas_draft_config(config: Config) -> Config:
    """Keep the film's policies while limiting its first pass to the NAS producers."""
    draft = config.model_copy(deep=True)
    draft.tier = "nas"
    draft.editorial.reader = "rules"
    draft.editorial.laya_audience = False
    if draft.editorial.preparation.demands_captions:
        draft.editorial.preparation.tier = "no_captions"
    return draft


def _section(config: Config, path: tuple[str, ...]) -> Any:
    section: Any = config
    for name in path:
        section = getattr(section, name)
    return section


def apply_tier(config: Config) -> dict[str, Any]:
    """Resolve one product tier and apply its preparation and reader contract."""
    applied = {}
    if config.tier == "auto":
        accelerated, reason = inference_acceleration(config.inference)
        config.tier = "nas"
        if accelerated:
            config.tier = "full" if _llm_configured(config) else "gpu"
        applied["tier"] = config.tier
        logger.info("Automatic selection tier: %s. %s", config.tier, reason)
    if config.tier == "full":
        _require_llm_endpoint(config)
    elif config.llm.model.strip():
        logger.warning(
            "The configured LLM can supply titles and music mood; selection stays on %s. "
            "Model refinement requires GPU capability and the full tier's caption and Laya services.",
            config.tier,
        )
    for (path, field), value in TIERS[config.tier].items():
        section = _section(config, path)
        if field in section.model_fields_set and getattr(section, field) != value:
            logger.warning(
                "Ignoring %s: selection tier %s requires %s",
                ".".join((*path, field)),
                config.tier,
                value,
            )
        setattr(section, field, value)
        applied[".".join((*path, field))] = value
    return applied


# Providers that name their own host, so stating one of them states the endpoint.
_HOSTED_PROVIDERS = frozenset({"openai", "anthropic", "zai"})


def _llm_configured(config: Config) -> bool:
    llm = config.llm
    stated = llm.model_fields_set
    endpoint = ("base_url" in stated and llm.base_url.strip()) or (
        "provider" in stated and llm.provider in _HOSTED_PROVIDERS
    )
    return bool(endpoint and llm.model.strip())


def _require_llm_endpoint(config: Config) -> None:
    if not _llm_configured(config):
        raise ValueError(
            "tier: full needs an LLM: set advanced.llm.base_url and advanced.llm.model "
            "to the server that answers it, or choose tier: gpu for every light model "
            "and no LLM"
        )


def forget_applied(data: dict[str, Any], applied: dict[str, Any]) -> None:
    """Persist the chosen product tier without a second set of preparation switches."""
    for key, value in applied.items():
        *path, field = key.split(".")
        section = data
        for name in path:
            section = section.get(name, {})
        if key != "tier" or section.get(field) == value:
            section.pop(field, None)
