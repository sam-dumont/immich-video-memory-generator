"""The three product tiers: CPU classifiers, light GPU models, and an added prose LLM.

`tier:` is the one choice a user makes. It sets three advanced knobs, and a knob the user
states in the file can reduce preparation. Only ``full`` may enable an LLM:

* ``nas``: inexpensive CPU heads and detectors, rules, no captions, no Laya.
* ``gpu``: every light model. The caption server, the heads and detectors, and Laya for the
  sharing question. Still the rules reader and zero LLM calls.
* ``full``: the ``gpu`` tier plus an LLM for prose and polish. It refuses to load without the
  LLM's ``base_url`` and ``model``.

The sharing question never goes to an LLM on any tier.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

ProductTier = Literal["nas", "gpu", "full"]

# (section path, field) -> value, per tier. The section path is walked from the Config.
_READER = ("editorial",), "reader"
_PREPARATION = ("editorial", "preparation"), "tier"
_LAYA = ("editorial",), "laya_audience"

TIERS: dict[str, dict[tuple[tuple[str, ...], str], Any]] = {
    "nas": {_READER: "rules", _PREPARATION: "no_captions", _LAYA: False},
    "gpu": {_READER: "rules", _PREPARATION: "full", _LAYA: True},
    "full": {_READER: "model", _PREPARATION: "full", _LAYA: True},
}


def _section(config: Config, path: tuple[str, ...]) -> Any:
    section: Any = config
    for name in path:
        section = getattr(section, name)
    return section


def apply_tier(config: Config) -> dict[str, Any]:
    """Set the tier defaults and its reader boundary; return the values it supplied."""
    if config.tier == "full":
        _require_llm_endpoint(config)
    elif config.llm.model.strip():
        logger.warning(
            "llm.model is set but the tier is %s, so the editor calls no LLM. "
            "Set `tier: full` to use it.",
            config.tier,
        )
    applied = {}
    for (path, field), value in TIERS[config.tier].items():
        section = _section(config, path)
        requires_rules = (path, field) == _READER and config.tier != "full"
        if field in section.model_fields_set and not requires_rules:
            continue
        setattr(section, field, value)
        applied[".".join((*path, field))] = value
    return applied


# Providers that name their own host, so stating one of them states the endpoint.
_HOSTED_PROVIDERS = frozenset({"openai", "anthropic", "zai"})


def _require_llm_endpoint(config: Config) -> None:
    llm = config.llm
    stated = llm.model_fields_set
    endpoint = ("base_url" in stated and llm.base_url.strip()) or (
        "provider" in stated and llm.provider in _HOSTED_PROVIDERS
    )
    if not endpoint or not llm.model.strip():
        raise ValueError(
            "tier: full needs an LLM: set advanced.llm.base_url and advanced.llm.model "
            "to the server that answers it, or choose tier: gpu for every light model "
            "and no LLM"
        )


def forget_applied(data: dict[str, Any], applied: dict[str, Any]) -> None:
    """Omit unchanged tier defaults, preserving choices edited since the config was loaded."""
    for key, value in applied.items():
        *path, field = key.split(".")
        section = data
        for name in path:
            section = section.get(name, {})
        if section.get(field) == value:
            section.pop(field, None)
