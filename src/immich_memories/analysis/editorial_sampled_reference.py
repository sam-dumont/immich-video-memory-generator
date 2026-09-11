"""Bind duplicate nominations to actual, already observed single-source material."""

from copy import deepcopy
from dataclasses import asdict
from typing import Any


def sampled_source_relation(port, *, picture_records):
    """Conserve the same source relation across local reviews and final discovery."""
    if port is None:
        return None
    # These are relations between source pictures, independent of the review's
    # aliases and nominated keeper. Conserve negative/unavailable results too.
    outcomes: dict[tuple[str, ...], dict[str, Any]] = {}

    def confirm(remove_asset_id, kept_asset_id):
        if remove_asset_id == kept_asset_id:
            return {"same": True, "scope": "identical source identity"}
        pair = tuple(sorted((remove_asset_id, kept_asset_id)))
        if pair in outcomes:
            return deepcopy(outcomes[pair])
        if any(picture_records.get(asset, {}).get("status") != "available" for asset in pair):
            outcomes[pair] = {"same": None, "status": "own_preview_unavailable"}
            return deepcopy(outcomes[pair])
        decisions, audit = port((pair,), picture_records)
        if len(decisions) != 1:
            raise ValueError("sampled comparison must account for its exact nominated pair")
        decision = decisions[0]
        if (decision.earlier_asset_id, decision.later_asset_id) != pair:
            raise ValueError("sampled comparison changed the nominated source identities")
        if type(decision.same) is not bool:
            raise ValueError("sampled comparison must return a boolean decision")
        outcomes[pair] = {
            "same": decision.same if decision.warning is None else None,
            "decision": asdict(decision),
            # Runtime counters belong in top-level sampled_pair_metrics. They
            # must not alter a semantic decision when the same answer is reused.
            "evidence": {
                key: deepcopy(audit[key]) for key in ("scope", "pair_votes", "rows") if key in audit
            },
            "scope": "sampled picture confirmation; material and unseen video are unchanged",
        }
        return deepcopy(outcomes[pair])

    return confirm
