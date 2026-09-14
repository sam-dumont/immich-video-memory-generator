"""Resume remote classification from the same missing-facts ledger as local work."""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from pathlib import Path

from immich_memories.analysis.remote_facts import FactsAnswer, RemoteFactsClient
from immich_memories.cache.embedding_cache import HeadFactStore
from immich_memories.config_models_inference import InferenceConfig

logger = logging.getLogger(__name__)

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
    concurrency: int | None = None,
) -> float | None:
    """Ask the service for every missing producer of each picture and bank the answer.

    ``pending`` maps an asset id to the head versions it still lacks. Requests leave
    several at a time, but a picture is banked only once every picture before it in
    ``pending`` has been, so the bank sees one order whatever order the answers come
    back in. The whole answer for a picture is validated before any of its producers
    is written, so a malformed reply banks nothing.

    Returns the seconds the service charged itself over the pass, or None when it
    reported none.
    """
    if client is None:
        if config is None:
            raise ValueError("prepare_remote_facts needs a config or a client")
        opened: RemoteFactsClient | nullcontext = RemoteFactsClient(config)
    else:
        opened = nullcontext(client)
    in_flight = concurrency or (config.facts_concurrency if config else 1)
    logger.info("remote facts: %d pictures, %d requests in flight", len(pending), in_flight)
    charged = 0.0
    measured = 0
    # The answers are closed before the client they ride on, which is what the
    # order of these three buys: a stop raises out of the loop, and the requests
    # still outstanding have to be let go of while there is still a client.
    with (
        opened as remote,
        closing(HeadFactStore(store_path)) as bank,
        closing(_in_order(remote, pending, preview_for, check_cancelled, in_flight)) as answers,
    ):
        for index, (asset_id, answer) in enumerate(answers, 1):
            check_cancelled()
            # Neither execution provider nor endpoint belongs in model identity:
            # the encoder key is the one the service computed over the artifact.
            for result in answer.producers.values():
                bank.remember_facts(asset_id, result.bank_facts(), encoder_key=result.encoder_key)
            if answer.service_seconds is not None:
                charged += answer.service_seconds
                measured += 1
            on_asset(asset_id)
            progress(STAGE, index, len(pending))
    return charged if measured else None


def _in_order(
    remote: RemoteFactsClient,
    pending: Mapping[str, Mapping[str, str]],
    preview_for: Callable[[str], bytes],
    check_cancelled: Callable[[], None],
    in_flight: int,
) -> Generator[tuple[str, FactsAnswer], None, None]:
    """Every picture's answer, in ``pending`` order, with ``in_flight`` requests outstanding.

    Work is submitted a window at a time rather than all at once: a stopped run must
    not have to drain a whole library's worth of queued requests before it stops.
    Previews are read here, on this thread, so an unreadable one fails where it did.
    """
    asset_ids = tuple(pending)
    with ThreadPoolExecutor(max_workers=in_flight, thread_name_prefix="facts") as pool:
        for start in range(0, len(asset_ids), in_flight):
            check_cancelled()
            window = asset_ids[start : start + in_flight]
            futures = [
                pool.submit(remote.facts, preview_for(asset_id), pending[asset_id])
                for asset_id in window
            ]
            for asset_id, future in zip(window, futures, strict=True):
                yield asset_id, future.result()
