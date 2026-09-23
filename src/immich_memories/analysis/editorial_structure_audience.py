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

AUDIENCE_BANK_NAME = "audience-verdicts.private.json"

# Holds that say nothing was looked at, not that something was seen. Kept, they would hold a
# picture forever for want of a reader the install may add later.
_NOTHING_SEEN = frozenset(
    {"unavailable_evidence", "unread_private_activity", "no_content_evidence"}
)


class AudienceBank:
    """The library's audience answers and holds, kept across cuts, scopes and audiences.

    An answer is named by who gave it (the check and the replying model) and by its evidence
    key, which already carries the prompt version, so an answer from another reader or an
    older prompt is never served as this one's. Only answers a model was asked for are kept:
    the rule checks cost nothing to run again.

    A hold is kept per picture and split by what cast it. One cast by the nsfw head, a body
    observation or a rule is permanent: nothing later lifts it. One cast by a model reading
    text (a private activity) is stamped with the audience prompt and check policy it was
    given under. Under that same prompt a later read never lifts it either; once the prompt
    changes it is not applied, the picture is asked again, and the new answer replaces it.
    """

    def __init__(self, path: Path | None, *, answerer: str) -> None:
        self._path = path
        stored = _read_bank(path)
        answers = _section(stored, "answers")
        self._stored = {"answers": answers, "holds": _section(stored, "holds")}
        self._answers = answers[answerer] = _section(answers, answerer)
        self._holds = self._stored["holds"]

    def answer(self, key: str) -> dict[str, Any] | None:
        banked = self._answers.get(key)
        return banked if isinstance(banked, dict) and "verdict" in banked else None

    def keep(self, key: str, record: dict[str, Any]) -> None:
        if record.get("parsed") is True:
            self._answers[key] = record
            self._save()

    def held(self, asset_id: str) -> dict[str, Any] | None:
        return standing_hold(self._holds.get(asset_id))

    def hold(self, asset_id: str, record: dict[str, Any]) -> None:
        """Keep a refusal that something seen caused, in its source's slot; stricter wins."""
        slots = dict(self._holds.get(asset_id) or {})
        changed = _stale(slots.get("text")) and _answers_current_prompt(record)
        if changed:
            del slots["text"]
        if _keepable(record):
            kind = "text" if record.get("finding") == _TEXT_FINDING else "permanent"
            existing = (
                _current_text(slots)
                if kind == "text"
                else standing_hold({"permanent": slots.get("permanent")})
            )
            if existing is None or (
                _share.tighten(existing["verdict"], record["verdict"]) != existing["verdict"]
            ):
                slots[kind] = {
                    "verdict": record["verdict"],
                    "finding": record.get("finding"),
                    "policy": record.get("policy") or record.get("source"),
                } | ({"text_version": _text_version()} if kind == "text" else {})
                changed = True
        if changed:
            self._holds[asset_id] = slots
            self._save()

    def _save(self) -> None:
        if self._path is not None:
            write_secret_file(self._path, json.dumps(self._stored, indent=1))


_TEXT_FINDING = "private_activity"


def _text_version() -> str:
    return f"{_share.AUDIENCE_PROMPT_VERSION}|{_share.AUDIENCE_CHECK_POLICY_VERSION}"


def _stale(hold: Any) -> bool:
    return isinstance(hold, dict) and hold.get("text_version", _text_version()) != _text_version()


def _current_text(slots: dict[str, Any]) -> dict[str, Any] | None:
    text = slots.get("text")
    return text if isinstance(text, dict) and "verdict" in text and not _stale(text) else None


def _answers_current_prompt(record: dict[str, Any]) -> bool:
    return (
        record.get("parsed") is True
        and record.get("activity") is not None
        and record.get("policy") == _share.AUDIENCE_PROMPT_VERSION
    )


def _keepable(record: dict[str, Any]) -> bool:
    return (
        record.get("verdict") != "share"
        and record.get("parsed", True) is True
        and record.get("finding") not in _NOTHING_SEEN
    )


def standing_hold(slots: Any) -> dict[str, Any] | None:
    """The hold a picture's banked slots still impose: every permanent one, and a text one
    only while the audience prompt and check policy it was cast under are the current ones."""
    if not isinstance(slots, dict):
        return None
    permanent = slots.get("permanent")
    holds = [
        hold
        for hold in (permanent if isinstance(permanent, dict) else None, _current_text(slots))
        if hold is not None and "verdict" in hold
    ]
    return max(holds, key=lambda hold: _share.VERDICTS.index(hold["verdict"]), default=None)


def library_refusals(path: Path, audience: str) -> frozenset[str]:
    """Every picture the library's audience bank still holds back from this audience."""
    return frozenset(
        asset_id
        for asset_id, slots in _section(_read_bank(path), "holds").items()
        if (hold := standing_hold(slots)) is not None
        and not _share.allowed(str(hold["verdict"]), audience)
    )


def _section(stored: dict[str, Any], name: str) -> dict[str, Any]:
    section = stored.get(name)
    return section if isinstance(section, dict) else {}


def _read_bank(path: Path | None) -> dict[str, Any]:
    try:
        stored = json.loads(path.read_text()) if path is not None else {}
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


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
        library: AudienceBank,
        check_audience=_share.check_audience,
    ) -> None:
        self._judge = judge
        self.audience = audience
        self._check_audience = check_audience
        self._pictures = picture_evidence
        self._flag_rows = flag_rows
        self._lines = lines
        self._bank_path = bank_path
        self._library = library
        self.bank: dict[str, dict[str, Any]] = {}
        self.verdicts: dict[str, dict[str, Any]] = {}
        self.rejected_members: set[str] = set()
        self.requests = 0

    def check(self, evidence, terminal=None) -> tuple[str, dict[str, Any]]:
        """The banked decision for this evidence, asking the judge only for a new key."""
        key = _share.audience_check_key(evidence)
        if key not in self.bank:
            banked = self._library.answer(key)
            first_call = len(self._judge.calls)
            self.bank[key] = (
                terminal
                if terminal is not None
                else banked
                if banked is not None
                else self._check_audience(
                    self._judge, evidence, f"shareability-{len(self.bank) + 1:02d}"
                )
            )
            asked = len(self._judge.calls) - first_call
            self.requests += asked
            if asked:
                self._library.keep(key, self.bank[key])
            write_secret_file(self._bank_path, json.dumps(self.bank, indent=1))
        return key, self.bank[key]

    def keep_hold(self, asset_id: str, record: dict[str, Any]) -> None:
        """Bank a refusal of this picture so no later cut, scope or prompt version lifts it."""
        self._library.hold(asset_id, record)

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
            self.keep_hold(u["asset_id"], self.verdicts[u["asset_id"]])
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
        held = self._library.held(u["asset_id"])
        if (
            held is not None
            and _share.tighten(record["verdict"], held["verdict"]) != record["verdict"]
        ):
            record = record | {"verdict": held["verdict"], "banked_hold": held}
        self.keep_hold(u["asset_id"], record)
        self.verdicts[u["asset_id"]] = record | {"evidence_key": key}
        return record["verdict"]

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
        for sample_id in sample_ids:
            gate.keep_hold(carrier["asset_id"], attached_audience[sample_id])
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
