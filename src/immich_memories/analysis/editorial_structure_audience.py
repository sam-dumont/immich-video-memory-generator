"""Who may see a picture: the audience verdict per candidate, banked by its evidence key.

The evidence is what ingest banked about each picture (its caption, its heads and its flags);
no model looks at a picture here. The carrier rules run again on the candidate's line, so a
refused picture's moment offers its other pictures through the same replacement path as any
refusal.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis.editorial_audience_batch import ask_activity_batches
from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.analysis.editorial_exposure_chains import ChainHold
from immich_memories.locked_file import file_lock
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

    A hold is kept per picture and split by what cast it. One cast by a detector head or a rule
    is permanent: nothing later lifts it, including a body-observation hold an older bank
    carries from before pictures stopped being read at film time. One cast by a model reading
    text (a private activity) is stamped with the audience prompt and check policy it was
    given under. Under that same prompt a later read never lifts it either; once the prompt
    changes it is not applied, the picture is asked again, and the new answer replaces it.
    """

    def __init__(self, path: Path | None, *, answerer: str) -> None:
        self._path = path
        self._answerer = answerer
        self._load(_read_bank(path))

    def _load(self, stored: dict[str, Any]) -> None:
        answers = _section(stored, "answers")
        self._stored = {"answers": answers, "holds": _section(stored, "holds")}
        self._answers = answers[self._answerer] = _section(answers, self._answerer)
        self._holds = self._stored["holds"]

    def answer(self, key: str) -> dict[str, Any] | None:
        banked = self._answers.get(key)
        return banked if isinstance(banked, dict) and "verdict" in banked else None

    def keep(self, key: str, record: dict[str, Any]) -> None:
        if record.get("parsed") is True:
            self._update(lambda: self._answer(key, record))

    def _answer(self, key: str, record: dict[str, Any]) -> bool:
        self._answers[key] = record
        return True

    def held(self, asset_id: str) -> dict[str, Any] | None:
        return standing_hold(self._holds.get(asset_id))

    def hold(self, asset_id: str, record: dict[str, Any]) -> None:
        """Keep a refusal that something seen caused, in its source's slot; stricter wins."""
        self._update(lambda: self._hold(asset_id, record))

    def _hold(self, asset_id: str, record: dict[str, Any]) -> bool:
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
        return changed

    def _update(self, change: Callable[[], bool]) -> None:
        """Apply one change to the bank as it stands on disk now, so another run's rows stay."""
        if self._path is None:
            change()
            return
        with file_lock(self._path):
            self._load(_read_bank(self._path))
            if change():
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
        annotations,
        flag_rows,
        lines,
        bank_path: Path,
        library: AudienceBank,
        check_audience=_share.check_audience,
        chains: Mapping[str, ChainHold] | None = None,
        companion_heads: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._judge = judge
        self.audience = audience
        self._check_audience = check_audience
        self._annotations = annotations
        self._flag_rows = flag_rows
        self._lines = lines
        self._chains = chains or {}
        self._companion_heads = companion_heads or {}
        self._bank_path = bank_path
        self._library = library
        self.bank: dict[str, dict[str, Any]] = {}
        self.verdicts: dict[str, dict[str, Any]] = {}
        self.rejected_members: set[str] = set()
        self.requests = 0
        # Activity answers a batch already paid for, by evidence key, until `check` reads them.
        self._answered: dict[str, str] = {}

    def check(self, evidence) -> tuple[str, dict[str, Any]]:
        """The banked decision for this evidence, asking the judge only for a new key."""
        key = _share.audience_check_key(evidence)
        if key not in self.bank:
            banked = self._library.answer(key)
            if banked is not None:
                # An answer banked before a floor existed must not reach past it.
                banked = _share.floors_under(evidence, banked)
            answered = self._answered.pop(key, None)
            if banked is not None:
                answered = None
            first_call = len(self._judge.calls)
            self.bank[key] = (
                banked
                if banked is not None
                else self._check_audience(
                    self._judge,
                    evidence,
                    f"shareability-{len(self.bank) + 1:02d}",
                    **({} if answered is None else {"activity_answer": answered}),
                )
            )
            asked = len(self._judge.calls) - first_call
            self.requests += asked
            if asked or answered is not None:
                self._library.keep(key, self.bank[key])
            write_secret_file(self._bank_path, json.dumps(self.bank, indent=1))
        return key, self.bank[key]

    def keep_hold(self, asset_id: str, record: dict[str, Any]) -> None:
        """Bank a refusal of this picture so no later cut, scope or prompt version lifts it."""
        self._library.hold(asset_id, record)

    def verdict_of(self, u) -> str:
        observed_reason, evidence = self._evidence(u)
        if observed_reason:
            self.verdicts[u["asset_id"]] = {
                "verdict": "do_not_show",
                "finding": observed_reason,
                "source": "carrier-rule-on-observations",
                "evidence_key": "",
            }
            self.keep_hold(u["asset_id"], self.verdicts[u["asset_id"]])
            return "do_not_show"
        standing = self._held_already(evidence)
        if standing is not None:
            record = {
                "verdict": standing["verdict"],
                "finding": standing.get("finding"),
                "source": "held-before-asking",
                "evidence_key": "",
            }
            self.keep_hold(u["asset_id"], record)
            self.verdicts[u["asset_id"]] = record
            return record["verdict"]
        key, record = self.check(evidence)
        held = self._library.held(u["asset_id"])
        if (
            held is not None
            and _share.tighten(record["verdict"], held["verdict"]) != record["verdict"]
        ):
            record = record | {"verdict": held["verdict"], "banked_hold": held}
        self.keep_hold(u["asset_id"], record)
        self.verdicts[u["asset_id"]] = record | {"evidence_key": key}
        return record["verdict"]

    def prefetch(self, units, *, batch: int) -> None:
        """Ask the activity question of every carrier here that still needs one, `batch` a request.

        A carrier a rule, a detector's floor or a banked answer already decides is not sent. The answers wait in the gate, and `verdict_of` reads each carrier exactly
        as before, asking alone any carrier the batch left unanswered. Only the full check has
        an activity question to batch; any other check is left as it is.
        """
        if batch < 2 or self._check_audience is not _share.check_audience:
            return
        pending: dict[str, tuple[dict[str, Any], bool]] = {}
        for u in units:
            observed_reason, evidence = self._evidence(u)
            if observed_reason or self._held_already(evidence):
                continue
            key = _share.audience_check_key(evidence)
            if key in self.bank or key in self._answered or self._library.answer(key):
                continue
            allow_nudity = _share.activity_question(evidence)
            if allow_nudity is not None:
                pending[key] = (evidence, allow_nudity)
        first_call = len(self._judge.calls)
        self._answered.update(ask_activity_batches(self._judge, pending, size=batch))
        self.requests += len(self._judge.calls) - first_call

    def _evidence(self, u) -> tuple[str | None, dict[str, Any]]:
        """The carrier rule's refusal if one applies, and the evidence the audience question is
        asked on."""
        asset_id = u["asset_id"]
        observed_reason = excluded_carrier_sources({asset_id: self._lines.get(asset_id, "")}).get(
            asset_id
        )
        evidence = _share.evidence_for_unit(
            u,
            self._annotations,
            self._flag_rows,
            self._lines,
            chains=self._chains,
            companion_heads=self._companion_heads,
        )
        return observed_reason, evidence

    def _held_already(self, evidence) -> dict[str, Any] | None:
        """A detector's floor that already refuses this carrier for this audience.

        The floor is read off the evidence itself, and an answer could only ever tighten it, so
        asking changes nothing about this cut. A family film, which a detector floor does not
        refuse, still asks: a private activity is refused there too.
        """
        floor = _share.floors_under(evidence, {"verdict": "share"})
        return None if _share.allowed(str(floor["verdict"]), self.audience) else floor

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
