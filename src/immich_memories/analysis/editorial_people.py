"""Privacy-safe people identities for editorial prompts and lifecycle rules."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from immich_memories.people.context import PersonPromptContext
from immich_memories.people.relationships import CLOSE_FAMILY_KINDS, owner_role, reciprocal_kind


@dataclass(frozen=True, slots=True)
class EditorialPersonLink:
    """One people-graph edge addressed only by editorial tokens."""

    kind: str
    target_token: str
    target_name: str
    source: str


@dataclass(frozen=True, slots=True)
class EditorialPersonFact:
    """Prompt-safe facts for one logical person."""

    token: str
    name: str
    role: str | None
    tier: str | None
    birth_date: str | None
    relationship: str
    relationship_source: str
    first_month: str | None
    onset: str | None
    relationship_current: bool
    owner_relationship_kinds: tuple[str, ...]
    links: tuple[EditorialPersonLink, ...]


@dataclass(frozen=True, slots=True, repr=False)
class EditorialPeople(Mapping[str, EditorialPersonFact]):
    """Immutable token-keyed facts with a private Immich identity lookup."""

    _facts_by_token: Mapping[str, EditorialPersonFact]
    _token_by_person_id: Mapping[str, str]

    def __getitem__(self, token: str) -> EditorialPersonFact:
        return self._facts_by_token[token]

    def __iter__(self) -> Iterator[str]:
        return iter(self._facts_by_token)

    def __len__(self) -> int:
        return len(self._facts_by_token)

    def __repr__(self) -> str:
        return (
            f"EditorialPeople(people={len(self._facts_by_token)}, "
            f"identities={len(self._token_by_person_id)})"
        )

    def token_for_person_id(self, person_id: str) -> str | None:
        """Resolve an Immich identity without exposing it to editorial callers."""
        return self._token_by_person_id.get(person_id)

    def close_family_of(self, subjects: Sequence[str]) -> dict[str, str]:
        """Each partner, child and parent of the subjects, by name, with their relation to them.

        A subject is named as the people file or Immich names them, or by an Immich ID. The
        confirmed links are read from both sides, so a parent recorded only on the subject's
        entry is found as well as one recorded only on the parent's own.
        """
        tokens = {token for token, fact in self._facts_by_token.items() if fact.name in subjects}
        tokens |= {self._token_by_person_id[s] for s in subjects if s in self._token_by_person_id}
        found: dict[str, str] = {}
        for token, fact in self._facts_by_token.items():
            for link in fact.links:
                if link.source != "confirmed" or link.kind not in CLOSE_FAMILY_KINDS:
                    continue
                if token in tokens and link.target_token not in tokens:
                    found[link.target_name] = owner_role(reciprocal_kind(link.kind)) or link.kind
                elif link.target_token in tokens and token not in tokens:
                    found[fact.name] = owner_role(link.kind) or link.kind
        return found

    def fact_for_person_id(self, person_id: str) -> EditorialPersonFact | None:
        """Resolve every merged Immich ID to its one logical person fact."""
        token = self.token_for_person_id(person_id)
        return self._facts_by_token.get(token) if token is not None else None


def _merged_identities(
    context_by_id: Mapping[str, PersonPromptContext],
) -> tuple[dict[str, PersonPromptContext], dict[str, str]]:
    """One context per logical person, and every Immich ID that resolves to it."""
    contexts_by_canonical: dict[str, PersonPromptContext] = {}
    canonical_by_person_id: dict[str, str] = {}
    for lookup_id, context in context_by_id.items():
        person_ids = context.person_ids
        if not person_ids or any(not person_id for person_id in person_ids):
            raise ValueError("people context contains an invalid identity")
        if len(set(person_ids)) != len(person_ids) or lookup_id not in person_ids:
            raise ValueError("people context contains inconsistent merged identities")
        canonical_id = person_ids[0]
        previous = contexts_by_canonical.get(canonical_id)
        if previous is not None and previous != context:
            raise ValueError("people context contains conflicting person facts")
        contexts_by_canonical[canonical_id] = context
        for person_id in person_ids:
            owner = canonical_by_person_id.setdefault(person_id, canonical_id)
            if owner != canonical_id:
                raise ValueError("people context assigns one identity to multiple people")
    return contexts_by_canonical, canonical_by_person_id


def _links(
    context: PersonPromptContext,
    contexts_by_canonical: Mapping[str, PersonPromptContext],
    canonical_by_person_id: Mapping[str, str],
    token_by_person_id: Mapping[str, str],
) -> tuple[EditorialPersonLink, ...]:
    links: list[EditorialPersonLink] = []
    for relationship in context.relationships:
        target_token = token_by_person_id.get(relationship.target_id)
        if target_token is None:
            raise ValueError("people context contains a relationship outside its identity set")
        target_context = contexts_by_canonical[canonical_by_person_id[relationship.target_id]]
        links.append(
            EditorialPersonLink(
                kind=relationship.kind,
                target_token=target_token,
                target_name=target_context.name,
                source=relationship.source,
            )
        )
    return tuple(links)


def _fact(
    token: str, context: PersonPromptContext, links: tuple[EditorialPersonLink, ...]
) -> EditorialPersonFact:
    return EditorialPersonFact(
        token=token,
        name=context.name,
        role=context.role,
        tier=context.tier,
        birth_date=context.birth_date,
        relationship=context.relationship,
        relationship_source=context.relationship_source,
        first_month=context.first_month,
        onset=context.onset,
        relationship_current=context.relationship_current,
        owner_relationship_kinds=context.owner_relationship_kinds,
        links=links,
    )


def adapt_editorial_people(
    context_by_id: Mapping[str, PersonPromptContext],
) -> EditorialPeople:
    """Replace raw people IDs with deterministic, order-independent Pxx tokens."""
    contexts_by_canonical, canonical_by_person_id = _merged_identities(context_by_id)
    canonical_ids = sorted(contexts_by_canonical)
    width = max(2, len(str(len(canonical_ids))))
    token_by_canonical = {
        canonical_id: f"P{index:0{width}d}"
        for index, canonical_id in enumerate(canonical_ids, start=1)
    }
    token_by_person_id = {
        person_id: token_by_canonical[canonical_id]
        for person_id, canonical_id in canonical_by_person_id.items()
    }
    facts_by_token = {
        token_by_canonical[canonical_id]: _fact(
            token_by_canonical[canonical_id],
            contexts_by_canonical[canonical_id],
            _links(
                contexts_by_canonical[canonical_id],
                contexts_by_canonical,
                canonical_by_person_id,
                token_by_person_id,
            ),
        )
        for canonical_id in canonical_ids
    }
    return EditorialPeople(
        MappingProxyType(facts_by_token),
        MappingProxyType(token_by_person_id),
    )
