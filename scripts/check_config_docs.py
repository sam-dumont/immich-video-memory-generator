#!/usr/bin/env python3
"""Fail when the config reference page and the pydantic schema disagree.

The page is hand-written on purpose -- the prose around each YAML block is worth
more than anything a generator would emit -- so this checks the key lists instead
of regenerating them: every field of every section model must appear in a YAML
block, and every key in a YAML block must be a real field.

Usage:
    python scripts/check_config_docs.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple, get_args

from pydantic import BaseModel

from immich_memories.config_loader import Config

PAGE = "docs-site/docs/reference/config-reference.md"

_FIX_HINT = "Document the missing keys, or drop the ones the schema no longer accepts."

_YAML_BLOCK = re.compile(r"^```yaml$(.*?)^```$", re.DOTALL | re.MULTILINE)
_KEY = re.compile(r"^(?P<indent> *)(?:# )?(?P<key>[a-z_][a-z0-9_]*):(?: |$)")


def _read_block(block: str, documented: dict[str, set[str]]) -> None:
    # `advanced:` is where the app writes tier-2 sections, so a block that opens
    # with it documents the sections one level in, not a section called advanced.
    section_indent = 0
    section: str | None = None
    for line in block.splitlines():
        match = _KEY.match(line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        key = match.group("key")
        if indent == 0 and key == "advanced":
            section_indent = 2
            section = None
        elif indent == section_indent:
            section = key
            documented.setdefault(key, set())
        elif indent == section_indent + 2 and section is not None:
            documented[section].add(key)


def documented_keys(page: str) -> dict[str, set[str]]:
    """Map each section documented in a YAML block to the field names shown under it."""
    documented: dict[str, set[str]] = {}
    for block in _YAML_BLOCK.findall(page):
        _read_block(block, documented)
    return documented


class SectionSchema(NamedTuple):
    """One `Config` field: the model behind the section and the keys it accepts.

    A plain switch such as `preset` has no model of its own, so it groups under
    its own name and shares its documentation with nothing.
    """

    model: str
    fields: frozenset[str]


def _section_model(annotation: object) -> type[BaseModel] | None:
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def schema_sections() -> dict[str, SectionSchema]:
    """Enumerate the config sections by walking `Config`, whatever module each model lives in."""
    sections = {}
    for name, field in Config.model_fields.items():
        model = _section_model(field.annotation)
        if model is None:
            # A plain top-level switch (`preset`) still has to appear on the page.
            sections[name] = SectionSchema(name, frozenset())
        else:
            sections[name] = SectionSchema(model.__name__, frozenset(model.model_fields))
    return sections


def _section_drift(
    section: str, entry: SectionSchema, shown: set[str] | None, shown_for_model: set[str]
) -> list[str]:
    if shown is None:
        return [f"{section}: section is missing from the page"]
    drift = []
    if undocumented := entry.fields - shown_for_model:
        drift.append(
            f"{section}: in the schema but not on the page: {', '.join(sorted(undocumented))}"
        )
    if invented := shown - entry.fields:
        drift.append(f"{section}: on the page but not in the schema: {', '.join(sorted(invented))}")
    return drift


def find_drift(schema: dict[str, SectionSchema], documented: dict[str, set[str]]) -> list[str]:
    """Report every schema field the page omits and every key it invents.

    Two sections built from one model (`llm` and `title_llm`) need the field list
    written out once: whichever block carries it documents both.
    """
    shown_by_model: dict[str, set[str]] = {}
    for section, entry in schema.items():
        shown_by_model.setdefault(entry.model, set()).update(documented.get(section, set()))

    drift = []
    for section, entry in sorted(schema.items()):
        drift.extend(
            _section_drift(section, entry, documented.get(section), shown_by_model[entry.model])
        )
    drift.extend(
        f"{section}: on the page but not a section of Config"
        for section in sorted(documented.keys() - schema.keys())
    )
    return drift


def main() -> int:
    """Print the drift between the config reference page and the schema."""
    drift = find_drift(schema_sections(), documented_keys(Path(PAGE).read_text()))
    if not drift:
        print(f"{PAGE} matches the config schema")  # noqa: T201
        return 0
    report = "\n  ".join([f"{PAGE} has drifted from the config schema:", *drift])
    print(f"{report}\n{_FIX_HINT}")  # noqa: T201
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
