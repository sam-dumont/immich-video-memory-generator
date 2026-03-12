"""User-friendly formatting for configuration errors."""

from __future__ import annotations

import yaml
from pydantic import ValidationError


def format_validation_error(error: ValidationError) -> str:
    """Format a Pydantic ValidationError into a user-friendly message.

    Args:
        error: The validation error from Pydantic.

    Returns:
        Human-readable error description.
    """
    lines = ["Configuration error:"]
    for err in error.errors():
        field_path = " -> ".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        lines.append(f"  {field_path}: {msg}")

        if "input" in err and err["input"] is not None:
            lines.append(f"    Got: {err['input']!r}")

    return "\n".join(lines)


def format_yaml_error(error: yaml.YAMLError) -> str:
    """Format a YAML parsing error into a user-friendly message.

    Args:
        error: The YAML error.

    Returns:
        Human-readable error description with line/column if available.
    """
    if hasattr(error, "problem_mark") and error.problem_mark is not None:
        mark = error.problem_mark
        problem = getattr(error, "problem", "unknown error")
        return (
            f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}:\n"
            f"  {problem}\n"
            f"  Check your config file for correct YAML formatting."
        )

    return f"YAML syntax error: {error}\n  Check your config file for correct YAML formatting."
