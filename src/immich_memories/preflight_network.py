"""What this install is allowed to contact outside the network it runs on."""

from immich_memories.config import Config
from immich_memories.config_models_network import GEOCODING_HOST, MAP_TILE_HOST
from immich_memories.preflight import CheckResult, CheckStatus

_OUTSIDE_CALLS = (
    ("geocoding", GEOCODING_HOST, "trip names and place names in the film's language"),
    ("map_tiles", MAP_TILE_HOST, "the trip fly-over, the static map and location cards"),
)


def outside_call_checks(config: Config) -> list[CheckResult]:
    """One row per third-party host this config allows, and nothing when none is on.

    A default install produces no rows at all: the absence is the statement.
    """
    return [
        CheckResult(
            name=f"Outside call: {switch}",
            status=CheckStatus.WARNING,
            message=f"{host} will be contacted for {bought}",
            details=f"Set network.{switch}: false to keep this run on your own network",
        )
        for switch, host, bought in _OUTSIDE_CALLS
        if getattr(config.network, switch)
    ]
