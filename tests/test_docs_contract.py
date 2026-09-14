"""Source-backed manual examples and operator entry points.

The dedicated config and CLI drift gates own reference completeness. These checks
validate runnable examples and discoverability without freezing explanatory prose.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import click
import pytest
import yaml
from pydantic import BaseModel

from immich_memories.automation.models import AutoAction, AutoOutcome, AutoRunResult
from immich_memories.cli import main
from immich_memories.cli.auto_cmd import _auto_result_to_json
from immich_memories.config_loader import Config
from immich_memories.operations.phases import OperationalPhase

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs-site/docs"


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def _blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"^```{language}\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


def _options(command: click.Command) -> set[str]:
    options = {
        option
        for param in command.params
        if isinstance(param, click.Option)
        for option in (*param.opts, *param.secondary_opts)
    }
    if isinstance(command, click.Group):
        for child in command.commands.values():
            options.update(_options(child))
    return options


@pytest.mark.parametrize(
    "page,command",
    [
        (f"create/cli/{name}.md", name)
        for name in (
            "generate",
            "prepare",
            "discover-days",
            "music",
            "titles",
            "people",
            "runs",
            "auto",
        )
    ],
)
def test_cli_option_tables_only_name_registered_options(page: str, command: str) -> None:
    """Catch a renamed or invented flag in the hand-written task references."""
    allowed = _options(main.commands[command])
    for line in (DOCS / page).read_text().splitlines():
        if not line.startswith("| "):
            continue
        cell = line.split("|", 2)[1]
        for flag in re.findall(r"`(--[a-z][a-z0-9-]*)`", cell):
            assert flag in allowed, f"{page}: {flag} is not registered under {command}"


def _check_config_keys(values: dict, model: type[BaseModel]) -> None:
    for key, value in values.items():
        if key == "advanced" and model is Config:
            _check_config_keys(value, model)
            continue
        assert key in model.model_fields, f"Unknown {model.__name__}.{key}"
        annotation = model.model_fields[key].annotation
        if (
            isinstance(value, dict)
            and isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
        ):
            _check_config_keys(value, annotation)


@pytest.mark.parametrize(
    "page",
    [
        "create/cli/auto.md",
        "create/cli/generate.md",
        "create/recipes/automated-generation.md",
        "create/recipes/trigger-endpoint.md",
    ],
)
def test_usage_config_examples_use_current_schema(page: str, tmp_path: Path) -> None:
    blocks = _blocks((DOCS / page).read_text(), "yaml")
    assert blocks, page
    for block in blocks:
        values = yaml.safe_load(block)
        _check_config_keys(values, Config)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(block)
        Config.from_yaml(config_file)


def test_auto_json_example_matches_the_result_contract() -> None:
    examples = _blocks((DOCS / "create/cli/auto.md").read_text(), "json")
    assert examples
    serialized = json.loads(
        _auto_result_to_json(
            AutoRunResult(
                outcome=AutoOutcome.DRY_RUN, action=AutoAction.GENERATION, reason="dry run"
            ),
            runtime={},
        )
    )
    for example in examples:
        payload = json.loads(example)
        assert payload.keys() <= serialized.keys()
        assert AutoOutcome(payload["outcome"])
        assert payload["action"] is None or AutoAction(payload["action"])
        for rejection in payload.get("rejections", []):
            assert rejection.keys() == {"category", "memory_key", "rule"}


def test_trigger_progress_example_uses_real_states_and_phases() -> None:
    examples = _blocks((DOCS / "create/recipes/trigger-endpoint.md").read_text(), "json")
    progress = next(json.loads(block) for block in examples if '"state"' in block)
    assert AutoOutcome(progress["state"])
    assert OperationalPhase(progress["phase"])
    accepted = next(json.loads(block) for block in examples if '"status_url"' in block)
    assert accepted["status_url"] == f"/api/trigger/{accepted['attempt_id']}"


def test_retired_guides_and_command_examples_are_absent() -> None:
    assert not (DOCS / "create/cli/scheduler.md").exists()
    retired = re.compile(
        r"immich-memories\s+(?:scheduler|analyze|export-project)\b|immich-memories\s+cache\s+(?:stats|export|import)\b"
    )
    for path in DOCS.rglob("*"):
        if path.suffix in {".md", ".mdx"}:
            assert not retired.search(path.read_text()), path.relative_to(REPO_ROOT)


@pytest.mark.parametrize(
    "page",
    [
        "README.md",
        "docs-site/docs/deploy/installation/docker.md",
        "docs-site/docs/deploy/installation/kubernetes.md",
        "docs-site/docs/deploy/installation/terraform.md",
        "docs-site/docs/deploy/configuration/authentication.mdx",
    ],
)
def test_deployment_entry_points_explain_authentication_and_replica_limits(page: str) -> None:
    text = _read(page).lower()
    assert "auth" in text
    assert "single-user" in text
    assert "single-replica" in text


@pytest.mark.parametrize(
    "page",
    [
        "docs-site/docs/reference/config-reference.md",
        "docs-site/docs/deploy/configuration/config-file.md",
        "docs-site/docs/deploy/maintenance/upgrading.md",
    ],
)
def test_api_version_override_remains_discoverable(page: str) -> None:
    text = _read(page)
    assert "api_version" in text
    assert all(version in text for version in ("auto", "v2", "v3"))


def test_automation_recipe_includes_activation_and_cluster_entry_point() -> None:
    text = (DOCS / "create/recipes/automated-generation.md").read_text()
    assert "immich-memories auto run" in text
    assert "systemctl --user enable --now immich-memories-auto.timer" in text
    manifest = "deploy/kubernetes/base/job.yaml"
    assert manifest in text
    assert (REPO_ROOT / manifest).is_file()


def test_docs_check_preserves_the_underlying_build_exit_status(tmp_path: Path) -> None:
    fake_npm = tmp_path / "npm"
    fake_npm.write_text("#!/bin/sh\nprintf '%s\\n' 'synthetic nonzero build'\nexit 23\n")
    fake_npm.chmod(0o755)
    environment = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    result = subprocess.run(
        ["make", "docs-check"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert "synthetic nonzero build" in result.stdout
    assert result.returncode != 0
