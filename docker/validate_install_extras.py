"""Resolve a validated Docker feature selector to a pip install target."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def resolve_install_target(extra: str, pyproject_path: Path = Path("pyproject.toml")) -> str:
    """Return the exact pip target for an explicit base or declared extra install."""
    with pyproject_path.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    declared_extras = pyproject["project"]["optional-dependencies"]
    if extra == "none":
        return "."
    if not extra or extra not in declared_extras:
        choices = ", ".join(sorted(declared_extras))
        raise ValueError(f"Invalid INSTALL_EXTRAS={extra!r}. Use 'none' or one of: {choices}")
    return f".[{extra}]"


def main(argv: list[str] | None = None) -> int:
    """Print a validated pip target for Docker's wheel build."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("Usage: validate_install_extras.py INSTALL_EXTRAS", file=sys.stderr)
        return 2

    try:
        install_target = resolve_install_target(arguments[0])
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    print(install_target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
