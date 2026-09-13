"""Resume remote classification from the same missing-facts ledger as local work."""

from collections.abc import Callable, Mapping
from contextlib import closing, nullcontext
from pathlib import Path

from immich_memories.analysis.remote_facts import RemoteFactsClient
from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.config_models_inference import InferenceConfig

STAGE = "remote_facts"


def prepare_remote_facts(
    *,
    pending: Mapping[str, Mapping[str, str]],
    store_path: Path,
    preview_for: Callable[[str], bytes],
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
    on_asset: Callable[[str], None],
    config: InferenceConfig | None = None,
    client: RemoteFactsClient | None = None,
) -> None:
    """Ask the service for every missing producer of each picture and bank the answer.

    ``pending`` maps an asset id to the head versions it still lacks. The whole
    answer for a picture is validated before any of its producers is written, so a
    malformed reply banks nothing.
    """
    if client is None:
        if config is None:
            raise ValueError("prepare_remote_facts needs a config or a client")
        opened: RemoteFactsClient | nullcontext = RemoteFactsClient(config)
    else:
        opened = nullcontext(client)
    with opened as remote, closing(HeadFactStore(store_path)) as bank:
        for index, (asset_id, versions) in enumerate(pending.items(), 1):
            check_cancelled()
            results = remote.facts(preview_for(asset_id), versions)
            check_cancelled()
            # Neither execution provider nor endpoint belongs in model identity:
            # the encoder key is the one the service computed over the artifact.
            for result in results.values():
                bank.remember_facts(asset_id, result.bank_facts(), encoder_key=result.encoder_key)
            on_asset(asset_id)
            progress(STAGE, index, len(pending))
