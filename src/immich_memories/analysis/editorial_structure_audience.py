"""Who may see a picture: the audience verdict per candidate, banked by its evidence key.

The carrier rules run again here, on the fullest line a candidate has: the picture observations
(composition, grids, care items) exist only after this demand, so a face close-up or a sheet of
identical portraits is refused and the moment's other pictures take its place through the same
replacement path as any refusal. Attached sampled material can only tighten a verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.analysis.editorial_final_attached import sample_audience_evidence
from immich_memories.security import write_secret_file


class AudienceGate:
    """One audience decision per distinct evidence key, kept for the run and written for audit."""

    def __init__(
        self,
        judge,
        *,
        audience: str,
        picture_evidence,
        flag_rows,
        lines,
        bank_path: Path,
        check_audience=_share.check_audience,
    ) -> None:
        self._judge = judge
        self.audience = audience
        self._check_audience = check_audience
        self._pictures = picture_evidence
        self._flag_rows = flag_rows
        self._lines = lines
        self._bank_path = bank_path
        self.bank: dict[str, dict[str, Any]] = {}
        self.verdicts: dict[str, dict[str, Any]] = {}
        self.rejected_members: set[str] = set()
        self.requests = 0

    def check(self, evidence, terminal=None) -> tuple[str, dict[str, Any]]:
        """The banked decision for this evidence, asking the judge only for a new key."""
        key = _share.audience_check_key(evidence)
        if key not in self.bank:
            first_call = len(self._judge.calls)
            self.bank[key] = (
                terminal
                if terminal is not None
                else self._check_audience(
                    self._judge, evidence, f"shareability-{len(self.bank) + 1:02d}"
                )
            )
            self.requests += len(self._judge.calls) - first_call
            write_secret_file(self._bank_path, json.dumps(self.bank, indent=1))
        return key, self.bank[key]

    def verdict_of(self, u) -> str:
        witness = self._pictures.enrich(u, stop_on_body_yes=self.audience == "sendable")
        observed_reason = excluded_carrier_sources({u["asset_id"]: self._pictures.line(u)}).get(
            u["asset_id"]
        )
        if observed_reason:
            self.verdicts[u["asset_id"]] = {
                "verdict": "do_not_show",
                "finding": observed_reason,
                "source": "carrier-rule-on-observations",
                "evidence_key": "",
            }
            return "do_not_show"
        terminal = (
            _share.terminal_body_hold(u, self._pictures.records, witness)
            if witness is not None
            else None
        )
        evidence = (
            terminal
            if terminal is not None
            else _share.evidence_for_unit(
                u,
                self._pictures.annotations,
                self._flag_rows,
                self._lines,
                picture_records=self._pictures.records,
            )
        )
        key, record = self.check(evidence, terminal)
        self.verdicts[u["asset_id"]] = record | {"evidence_key": key}
        return _share.tighten(record["verdict"])

    def proposed_picture_line(self, u) -> str:
        # One actual primary preview per contested shortlist choice. This is
        # not certification of unsampled motion or the unit's other members.
        primary = {"asset_id": u["asset_id"]}
        self._pictures.enrich(primary)
        return self._pictures.line(primary)

    def exclude_refused_members(self, candidates) -> None:
        """Exclude the whole refused carrier, including alternate members, from later offers."""
        self.rejected_members.update(
            member
            for candidate in candidates
            if not _share.allowed(self.verdicts[candidate["asset_id"]]["verdict"], self.audience)
            for member in _share.unit_members(candidate)
        )


def open_share_log(share_log: dict, *, funded_acquisition: dict) -> None:
    share_log["replacement_policy"] = "bounded editorial contribution review"
    share_log["funded_acquisition"] = funded_acquisition


def close_share_log(
    share_log: dict,
    gate: AudienceGate,
    *,
    never_auto_excluded: dict[str, list],
    anchor_label,
    ineligible: dict[str, str],
) -> None:
    share_log["prompt_version"] = _share.AUDIENCE_PROMPT_VERSION
    share_log["check_policy"] = _share.AUDIENCE_CHECK_POLICY_VERSION
    share_log["verdicts"] = gate.verdicts
    share_log["never_auto_excluded_units"] = sum(len(v) for v in never_auto_excluded.values())
    share_log["never_auto_excluded_anchors"] = sorted(anchor_label[f] for f in never_auto_excluded)
    share_log["anchors_without_shareable_picture"] = sorted(
        anchor_label[f] for f, r in ineligible.items() if r.startswith("no shareable")
    )
    share_log["judgment_requests"] = gate.requests


def _sample_verdicts(gate: AudienceGate, attached_evidence) -> dict[str, dict]:
    audience = {}
    for sample_id, record in attached_evidence.records.items():
        key, banked = gate.check(sample_audience_evidence(record))
        audience[sample_id] = banked | {"evidence_key": key}
    return audience


def tighten_with_attached_samples(
    carriers: list[dict],
    *,
    gate: AudienceGate,
    attached_evidence,
    attached_audience: dict[str, dict],
    share_log: dict,
    cut_carriers: list[dict],
) -> list[dict]:
    """Sampled attached material may only tighten what the primary picture was allowed."""
    attached_audience.update(_sample_verdicts(gate, attached_evidence))
    retained = []
    for carrier in carriers:
        sample_ids = attached_evidence.observed_members.get(carrier["asset_id"], ())
        previous = gate.verdicts.get(carrier["asset_id"], {})
        tightened = _share.tighten(
            previous.get("verdict"),
            *(attached_audience[sample_id]["verdict"] for sample_id in sample_ids),
        )
        if sample_ids:
            gate.verdicts[carrier["asset_id"]] = previous | {
                "verdict": tightened,
                "prior_verdict": previous,
                "attached_sample_checks": list(sample_ids),
            }
        if _share.allowed(tightened, gate.audience):
            retained.append(carrier)
            continue
        cut_carriers.append(
            carrier
            | {
                "reason": "Attached material tightens audience hold",
                "review_stage": "final-attached-audience",
            }
        )
        share_log["dropped"].append(
            {"asset_id": carrier["asset_id"], "event": carrier["event"], "verdict": tightened}
        )
    share_log["judgment_requests"] = gate.requests
    return retained
