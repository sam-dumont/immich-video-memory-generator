"""Bounded immutable AND/OR expressions over person names or face IDs."""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar

MAX_DEPTH = 16
MAX_NODES = 256
MAX_LEAVES = 64
MAX_LEAF_CHARACTERS = 1024
MAX_EXPRESSION_CHARACTERS = 16384

PersonOperator = Literal["person", "all", "any"]
Item = TypeVar("Item", bound=Hashable)


@dataclass(frozen=True, slots=True)
class PersonExpression:
    """A structural expression; leaf resolution is an explicit caller operation."""

    kind: PersonOperator
    value: str | None = None
    children: tuple[PersonExpression, ...] = ()

    def __post_init__(self) -> None:
        stack = [(self, 1)]
        leaves: set[str] = set()
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if depth > MAX_DEPTH or count > MAX_NODES:
                raise ValueError("person expression exceeds depth or node limit")
            if not isinstance(node.children, tuple):
                raise ValueError("person expression children must be an immutable tuple")
            if node.kind == "person":
                _admit_leaf(node, leaves)
            elif node.kind in ("all", "any"):
                _check_group(node)
                stack.extend((child, depth + 1) for child in node.children)
            else:
                raise ValueError("person expression operator must be person, all or any")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> PersonExpression:
        """Read the strict JSON AST; reject extra keys, empty groups and runaway input."""
        visited = 0

        def read(value: object, depth: int) -> PersonExpression:
            nonlocal visited
            visited += 1
            if depth > MAX_DEPTH or visited > MAX_NODES:
                raise ValueError("person expression exceeds depth or node limit")
            if not isinstance(value, Mapping) or len(value) != 1:
                raise ValueError("person expression must contain exactly one operator")
            kind = next(iter(value))
            payload = value[kind]
            if kind == "person" and isinstance(payload, str):
                return cls("person", value=payload)
            if kind in ("all", "any") and isinstance(payload, list) and payload:
                return cls(kind, children=tuple(read(child, depth + 1) for child in payload))
            raise ValueError("expected a person string or a nonempty all/any list")

        return read(data, 1)

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible value with deterministic structural order."""
        if self.kind == "person":
            return {"person": self.value}
        return {self.kind: [child.to_dict() for child in self.children]}

    @property
    def leaf_values(self) -> tuple[str, ...]:
        """Distinct leaves in first-seen expression order."""
        seen: dict[str, None] = {}
        stack = [self]
        while stack:
            node = stack.pop()
            if node.kind == "person":
                assert node.value is not None
                seen.setdefault(node.value, None)
            else:
                stack.extend(reversed(node.children))
        return tuple(seen)

    def map_leaves(self, mapper: Callable[[str], str | PersonExpression]) -> PersonExpression:
        """Resolve each unique name once, possibly to an OR group of merged face IDs."""
        replacements = {}
        for value in self.leaf_values:
            mapped = mapper(value)
            if isinstance(mapped, str):
                mapped = PersonExpression("person", value=mapped)
            if not isinstance(mapped, PersonExpression):
                raise ValueError("leaf mapper must return a person string or expression")
            replacements[value] = mapped

        def replace(node: PersonExpression) -> PersonExpression:
            if node.kind == "person":
                assert node.value is not None
                return replacements[node.value]
            return PersonExpression(node.kind, children=tuple(replace(c) for c in node.children))

        return replace(self)

    def evaluate(self, fetch: Callable[[str], Iterable[Item]]) -> frozenset[Item]:
        """Evaluate sets after fetching every distinct leaf exactly once."""
        answers = {value: frozenset(fetch(value)) for value in self.leaf_values}

        def visit(node: PersonExpression) -> frozenset[Item]:
            if node.kind == "person":
                assert node.value is not None
                return answers[node.value]
            groups = [visit(child) for child in node.children]
            if node.kind == "all":
                return groups[0].intersection(*groups[1:])
            return groups[0].union(*groups[1:])

        return visit(self)

    @property
    def display_label(self) -> str:
        """Quote leaf names and retain every meaningful group boundary."""
        if self.kind == "person":
            return json.dumps(self.value, ensure_ascii=False)
        operator = " AND " if self.kind == "all" else " OR "
        return "(" + operator.join(child.display_label for child in self.children) + ")"

    @classmethod
    def parse(cls, text: str) -> PersonExpression:
        return parse_person_expression(text)

    def __str__(self) -> str:
        return self.display_label


def _admit_leaf(node: PersonExpression, leaves: set[str]) -> None:
    if (
        not isinstance(node.value, str)
        or not node.value.strip()
        or len(node.value) > MAX_LEAF_CHARACTERS
        or node.children
    ):
        raise ValueError("person leaf requires a nonempty bounded string and no children")
    leaves.add(node.value)
    if len(leaves) > MAX_LEAVES:
        raise ValueError("person expression exceeds distinct leaf limit")


def _check_group(node: PersonExpression) -> None:
    if node.value is not None or not node.children:
        raise ValueError("person group requires children and no leaf value")
    if any(not isinstance(child, PersonExpression) for child in node.children):
        raise ValueError("person group children must be expressions")


def _tokens(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str) or len(text) > MAX_EXPRESSION_CHARACTERS:
        raise ValueError("person expression must be a bounded string")
    result: list[tuple[str, str]] = []
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        if char == '"':
            token, position = _quoted_person(decoder, text, position)
            result.append(token)
        elif char in "()":
            result.append((char, char))
            position += 1
        else:
            token, position = _operator_word(text, position)
            result.append(token)
        if len(result) > MAX_NODES * 4:
            raise ValueError("person expression has too many tokens")
    return result


def _quoted_person(
    decoder: json.JSONDecoder, text: str, position: int
) -> tuple[tuple[str, str], int]:
    try:
        value, end = decoder.raw_decode(text, position)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid quoted person at character {position}") from exc
    return ("person", value), end


def _operator_word(text: str, position: int) -> tuple[tuple[str, str], int]:
    end = position
    while end < len(text) and text[end].isalpha():
        end += 1
    word = text[position:end].upper()
    if word not in ("AND", "OR"):
        raise ValueError(f"expected quoted person, AND, OR or parenthesis at {position}")
    return (word, word), end


class _TokenReader:
    """A cursor over the token list; AND binds tighter than OR."""

    __slots__ = ("position", "tokens")

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.position = 0

    def accept(self, kind: str) -> bool:
        if self.position < len(self.tokens) and self.tokens[self.position][0] == kind:
            self.position += 1
            return True
        return False

    def primary(self, depth: int) -> PersonExpression:
        if depth > MAX_DEPTH:
            raise ValueError("person expression exceeds parenthesis depth limit")
        if self.accept("("):
            result = self.disjunction(depth + 1)
            if not self.accept(")"):
                raise ValueError("unclosed person expression parenthesis")
            return result
        if self.position >= len(self.tokens) or self.tokens[self.position][0] != "person":
            raise ValueError("expected a JSON-quoted person name or parenthesized expression")
        value = self.tokens[self.position][1]
        self.position += 1
        return PersonExpression("person", value=value)

    def conjunction(self, depth: int) -> PersonExpression:
        children = [self.primary(depth)]
        while self.accept("AND"):
            children.append(self.primary(depth))
        return (
            children[0] if len(children) == 1 else PersonExpression("all", children=tuple(children))
        )

    def disjunction(self, depth: int) -> PersonExpression:
        children = [self.conjunction(depth)]
        while self.accept("OR"):
            children.append(self.conjunction(depth))
        return (
            children[0] if len(children) == 1 else PersonExpression("any", children=tuple(children))
        )


def parse_person_expression(text: str) -> PersonExpression:
    """Parse JSON-quoted names, AND before OR, and parentheses; never evaluate code."""
    reader = _TokenReader(_tokens(text))
    result = reader.disjunction(1)
    if reader.position != len(reader.tokens):
        raise ValueError("unexpected token after person expression")
    return result
