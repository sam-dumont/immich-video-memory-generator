"""Reviewable family consequences of facts the user already confirmed.

This is deliberately normative graph closure, not truth. It can suggest the
ordinary kinship implied by confirmed parent and sibling edges, but its output
is always labelled as derived context and never written to ``confirmed:``.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from immich_memories.people.companion import people_entries

_PARENT_KINDS = {"parent-of", "mother-of", "father-of"}
_CHILD_KINDS = {"child-of", "son-of", "daughter-of"}
_SIBLING_KINDS = {"sibling-of", "sister-of", "brother-of", "twin-of"}
_PARTNER_KINDS = {"partner-of", "spouse-of"}


@dataclass(frozen=True)
class ConfirmedStep:
    """One normalized confirmed edge that supports an assumption."""

    source_id: str
    kind: str
    target_id: str


@dataclass(frozen=True)
class RelationshipAssumption:
    """One proposal for review, with no invented probability."""

    source_id: str
    kind: str
    target_id: str
    reverse_kind: str
    evidence: tuple[ConfirmedStep, ...]


def family_assumptions(document: dict[str, Any]) -> tuple[RelationshipAssumption, ...]:
    """Derive conventional kinship proposals from confirmed facts only."""
    facts = _confirmed_facts(document)
    confirmed_pairs = {frozenset((fact.source_id, fact.target_id)) for fact in facts}
    parents = _parents(facts)
    siblings = _sibling_evidence(facts, parents)
    partners = _partner_evidence(facts)

    proposed: dict[
        frozenset[str],
        dict[tuple[str, str, str, str], RelationshipAssumption],
    ] = defaultdict(dict)
    for assumption in itertools.chain(
        _shared_parent_assumptions(parents),
        _grandparent_assumptions(parents),
        _aunt_or_uncle_assumptions(parents, siblings),
        _cousin_assumptions(parents, siblings),
        _parent_in_law_assumptions(parents, partners),
        _sibling_in_law_assumptions(siblings, partners),
    ):
        pair = frozenset((assumption.source_id, assumption.target_id))
        if pair in confirmed_pairs:
            continue
        key = (
            assumption.source_id,
            assumption.kind,
            assumption.target_id,
            assumption.reverse_kind,
        )
        previous = proposed[pair].get(key)
        if previous is None:
            proposed[pair][key] = assumption
        else:
            proposed[pair][key] = RelationshipAssumption(
                assumption.source_id,
                assumption.kind,
                assumption.target_id,
                assumption.reverse_kind,
                tuple(sorted(set(previous.evidence) | set(assumption.evidence), key=_step_key)),
            )

    # A pair that can be read two different ways is not a review candidate; it
    # is evidence that the normative closure does not fit this family.
    unambiguous = [next(iter(values.values())) for values in proposed.values() if len(values) == 1]
    return tuple(sorted(unambiguous, key=_assumption_key))


def _confirmed_facts(document: dict[str, Any]) -> set[ConfirmedStep]:
    facts: set[ConfirmedStep] = set()
    for entry in people_entries(document):
        source_id = str(entry["ids"][0])
        confirmed = entry.get("confirmed")
        links = confirmed.get("links") if isinstance(confirmed, dict) else None
        if not isinstance(links, list):
            continue
        for link in links:
            if (
                isinstance(link, dict)
                and link.get("with")
                and link.get("decision", "confirmed") != "rejected"
            ):
                facts.add(
                    ConfirmedStep(
                        source_id,
                        str(link.get("kind") or "link"),
                        str(link["with"]),
                    )
                )
    return facts


def _parents(facts: set[ConfirmedStep]) -> dict[str, dict[str, ConfirmedStep]]:
    """Child -> parent -> normalized parent-of evidence."""
    parents: dict[str, dict[str, ConfirmedStep]] = defaultdict(dict)
    for fact in facts:
        if fact.kind in _PARENT_KINDS:
            parent_id, child_id = fact.source_id, fact.target_id
        elif fact.kind in _CHILD_KINDS:
            parent_id, child_id = fact.target_id, fact.source_id
        else:
            continue
        parents[child_id][parent_id] = ConfirmedStep(parent_id, "parent-of", child_id)
    return parents


def _sibling_evidence(
    facts: set[ConfirmedStep],
    parents: dict[str, dict[str, ConfirmedStep]],
) -> dict[frozenset[str], tuple[ConfirmedStep, ...]]:
    evidence: dict[frozenset[str], set[ConfirmedStep]] = defaultdict(set)
    for fact in facts:
        if fact.kind in _SIBLING_KINDS and fact.source_id != fact.target_id:
            evidence[frozenset((fact.source_id, fact.target_id))].add(fact)

    children_by_parent: dict[str, set[str]] = defaultdict(set)
    for child_id, mine in parents.items():
        for parent_id in mine:
            children_by_parent[parent_id].add(child_id)

    for parent_id, children in children_by_parent.items():
        for one_id, other_id in itertools.combinations(sorted(children), 2):
            evidence[frozenset((one_id, other_id))].update(
                (parents[one_id][parent_id], parents[other_id][parent_id])
            )
    return {pair: tuple(sorted(steps, key=_step_key)) for pair, steps in evidence.items()}


def _partner_evidence(
    facts: set[ConfirmedStep],
) -> dict[frozenset[str], tuple[ConfirmedStep, ...]]:
    evidence: dict[frozenset[str], set[ConfirmedStep]] = defaultdict(set)
    for fact in facts:
        if fact.kind in _PARTNER_KINDS and fact.source_id != fact.target_id:
            evidence[frozenset((fact.source_id, fact.target_id))].add(fact)
    return {pair: tuple(sorted(steps, key=_step_key)) for pair, steps in evidence.items()}


def _shared_parent_assumptions(
    parents: dict[str, dict[str, ConfirmedStep]],
) -> list[RelationshipAssumption]:
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for child_id, mine in parents.items():
        for parent_id in mine:
            children_by_parent[parent_id].append(child_id)

    found = []
    for parent_id, children in children_by_parent.items():
        for one_id, other_id in itertools.combinations(sorted(set(children)), 2):
            found.append(
                RelationshipAssumption(
                    one_id,
                    "sibling-of",
                    other_id,
                    "sibling-of",
                    (parents[one_id][parent_id], parents[other_id][parent_id]),
                )
            )
    return found


def _grandparent_assumptions(
    parents: dict[str, dict[str, ConfirmedStep]],
) -> list[RelationshipAssumption]:
    found = []
    for child_id, direct_parents in parents.items():
        for parent_id, parent_step in direct_parents.items():
            for grandparent_id, grandparent_step in parents.get(parent_id, {}).items():
                found.append(
                    RelationshipAssumption(
                        grandparent_id,
                        "grandparent-of",
                        child_id,
                        "grandchild-of",
                        (grandparent_step, parent_step),
                    )
                )
    return found


def _aunt_or_uncle_assumptions(
    parents: dict[str, dict[str, ConfirmedStep]],
    siblings: dict[frozenset[str], tuple[ConfirmedStep, ...]],
) -> list[RelationshipAssumption]:
    found = []
    for child_id, direct_parents in parents.items():
        for parent_id, parent_step in direct_parents.items():
            for pair, sibling_steps in siblings.items():
                if parent_id not in pair:
                    continue
                relative_id = next(person_id for person_id in pair if person_id != parent_id)
                found.append(
                    RelationshipAssumption(
                        relative_id,
                        "aunt-or-uncle-of",
                        child_id,
                        "nibling-of",
                        (*sibling_steps, parent_step),
                    )
                )
    return found


def _cousin_assumptions(
    parents: dict[str, dict[str, ConfirmedStep]],
    siblings: dict[frozenset[str], tuple[ConfirmedStep, ...]],
) -> list[RelationshipAssumption]:
    children_by_parent: dict[str, list[tuple[str, ConfirmedStep]]] = defaultdict(list)
    for child_id, direct_parents in parents.items():
        for parent_id, step in direct_parents.items():
            children_by_parent[parent_id].append((child_id, step))

    found = []
    for pair, sibling_steps in siblings.items():
        if len(pair) != 2:
            continue
        one_parent, other_parent = sorted(pair)
        for (one_child, one_step), (other_child, other_step) in itertools.product(
            children_by_parent.get(one_parent, ()),
            children_by_parent.get(other_parent, ()),
        ):
            if one_child == other_child:
                continue
            one_id, other_id = sorted((one_child, other_child))
            ordered_steps = (
                (one_step, other_step) if one_child == one_id else (other_step, one_step)
            )
            found.append(
                RelationshipAssumption(
                    one_id,
                    "cousin-of",
                    other_id,
                    "cousin-of",
                    (
                        ordered_steps[0],
                        *sibling_steps,
                        ordered_steps[1],
                    ),
                )
            )
    return found


def _parent_in_law_assumptions(
    parents: dict[str, dict[str, ConfirmedStep]],
    partners: dict[frozenset[str], tuple[ConfirmedStep, ...]],
) -> list[RelationshipAssumption]:
    found = []
    for pair, partner_steps in partners.items():
        if len(pair) != 2:
            continue
        one_id, other_id = sorted(pair)
        for partner_id, their_partner_id in ((one_id, other_id), (other_id, one_id)):
            for parent_id, parent_step in parents.get(partner_id, {}).items():
                if parent_id == their_partner_id:
                    continue
                found.append(
                    RelationshipAssumption(
                        parent_id,
                        "parent-in-law-of",
                        their_partner_id,
                        "child-in-law-of",
                        (*partner_steps, parent_step),
                    )
                )
    return found


def _sibling_in_law_assumptions(
    siblings: dict[frozenset[str], tuple[ConfirmedStep, ...]],
    partners: dict[frozenset[str], tuple[ConfirmedStep, ...]],
) -> list[RelationshipAssumption]:
    sibling_links = _sibling_links(siblings)
    found = []
    for partner_pair, partner_steps in partners.items():
        if len(partner_pair) != 2:
            continue
        one_id, other_id = sorted(partner_pair)
        for partner_id, their_partner_id in ((one_id, other_id), (other_id, one_id)):
            for sibling_id, sibling_steps in sibling_links.get(partner_id, ()):
                if sibling_id == their_partner_id:
                    continue
                source_id, target_id = sorted((their_partner_id, sibling_id))
                found.append(
                    RelationshipAssumption(
                        source_id,
                        "sibling-in-law-of",
                        target_id,
                        "sibling-in-law-of",
                        (*partner_steps, *sibling_steps),
                    )
                )
    return found


def _sibling_links(
    siblings: dict[frozenset[str], tuple[ConfirmedStep, ...]],
) -> dict[str, list[tuple[str, tuple[ConfirmedStep, ...]]]]:
    links: dict[str, list[tuple[str, tuple[ConfirmedStep, ...]]]] = defaultdict(list)
    for pair, steps in siblings.items():
        if len(pair) != 2:
            continue
        one_id, other_id = sorted(pair)
        links[one_id].append((other_id, steps))
        links[other_id].append((one_id, steps))
    return links


def _assumption_key(assumption: RelationshipAssumption) -> tuple[str, str, str]:
    return assumption.kind, assumption.source_id, assumption.target_id


def _step_key(step: ConfirmedStep) -> tuple[str, str, str]:
    return step.kind, step.source_id, step.target_id
