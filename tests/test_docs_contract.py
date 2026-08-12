"""Contract checks for launch-critical Immich compatibility documentation."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PRIMARY_MANUAL_CONTRACTS = [
    pytest.param(
        "README.md",
        "### Supported Immich Versions",
        "### Optional: LLM for smart clip analysis",
        "Leave this on `auto`. The app detects the server major version and uses the matching "
        "API contract; you do not choose a version for each run.",
        "The explicit `v2` and `v3` values are manual troubleshooting overrides—escape hatches "
        "for proxies or unusual deployments that hide or rewrite the version endpoint. They force "
        "that contract, so don't use them as upgrade flags.",
        id="readme",
    ),
    pytest.param(
        "docs/USER_GUIDE.md",
        "### Immich Connection",
        "### Time Period Selection",
        "`auto` detects the server major version at runtime and selects the right API contract. "
        "You do not need to choose a version for each run.",
        "Explicit `v2` and `v3` are manual troubleshooting overrides—escape hatches for unusual "
        "proxies or deployments where version detection is wrong; each one forces that contract.",
        id="user-guide",
    ),
    pytest.param(
        "docs-site/docs/reference/config-reference.md",
        "## Immich connection",
        "## Video analysis",
        "Keep `api_version` on `auto` for normal use. The client detects and caches the server "
        "major for each runtime client; you do not choose it for each generation.",
        "Explicit `v2` or `v3` is a manual troubleshooting escape hatch for a proxy or unusual "
        "deployment that prevents correct detection. An override forces that API contract.",
        id="config-reference",
    ),
    pytest.param(
        "docs-site/docs/deploy/configuration/config-file.md",
        "## Quick start config",
        "## Clip pacing",
        "`auto` is the default runtime policy: the app detects the server major and selects the "
        "matching API contract. You do not choose a version for each run.",
        "Explicit `v2` and `v3` values are manual troubleshooting escape hatches for proxies or "
        "unusual deployments that break version detection. An override forces that contract; it "
        "is not a normal upgrade step.",
        id="config-file",
    ),
]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def _normalized_section(relative_path: str, heading: str, next_heading: str) -> str:
    text = _read(relative_path)
    section = text.split(heading, 1)[1].split(next_heading, 1)[0]
    return " ".join(section.split())


def _assert_docker_output_mounts(text: str) -> None:
    standalone = text.split("## Standalone Docker run", 1)[1].split(
        "## Adding to your existing Immich stack", 1
    )[0]
    compose = text.split("## Adding to your existing Immich stack", 1)[1].split(
        "## Environment variables", 1
    )[0]
    assert "-v ./output:/app/output" in standalone
    assert "- ./output:/app/output" in compose


def _assert_primary_manual_policy(
    section: str, runtime_claim: str, manual_override_claim: str
) -> None:
    assert "Immich v2 and v3" in section
    assert "api_version: auto # auto | v2 | v3" in section
    assert runtime_claim in section, f"Missing automatic runtime-detection claim: {runtime_claim}"
    assert manual_override_claim in section, (
        f"Missing manual force-contract claim: {manual_override_claim}"
    )


def _assert_troubleshooting_contract(section: str) -> None:
    required_claims = (
        "`auto` detects the server major at runtime; you do not pick one for each run.",
        "If a reverse proxy hides or rewrites `/api/server/version`, use `v2` or `v3` as a manual "
        "troubleshooting escape hatch. The override forces that contract",
        "The read-only `immich-memories config test` reports the server version and authentication "
        "errors; it does not test uploads.",
        "If a v3 upload fails, keep the error shown by the command doing the upload and check the "
        "relevant Immich server logs. API keys are redacted.",
    )
    assert "api_version: auto # auto | v2 | v3" in section
    for claim in required_claims:
        assert claim in section, f"Missing Immich troubleshooting claim: {claim}"


def _assert_upgrade_contract(section: str) -> None:
    required_claims = (
        "Explicit `v2` and `v3` are manual troubleshooting escape hatches for unusual proxies "
        "or deployments that prevent correct detection; they force the selected contract.",
        "**Duration:** v2 duration strings and v3 integer milliseconds are normalized to seconds.",
        "**Upload:** v2 keeps the device identity fields; v3 sends `filename` and omits the "
        "removed `deviceAssetId` and `deviceId` fields.",
        "**Search dates:** date bounds include a UTC offset, which v3 requires.",
        "immich-memories config test",
        "This is a read-only authentication and compatibility check. It does not search assets, "
        "generate a video, create an album, or upload anything.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing Immich compatibility claim: {claim}"


def _assert_auto_run_contract(section: str) -> None:
    required_claims = (
        "`auto run` performs at most one action per wake.",
        "Before cooldown or candidate discovery, it looks for the oldest retryable pending delivery.",
        "A delivery retry uploads the already-validated artifact to its original album. It does not "
        "render a new video.",
        "That retry consumes the wake whether it completes, fails, or is a dry run.",
        "`action` is `generation` or `delivery_retry` after the automation lease is acquired.",
        "It is `null` when an overlapping wake is skipped before an action starts.",
        "`outcome` is `completed`, `skipped`, `dry_run`, or `failed`.",
        "`completed`, `skipped`, and `dry_run` exit 0. `failed` exits 1.",
        '"action": "generation"',
    )
    for claim in required_claims:
        assert claim in section, f"Missing automation contract: {claim}"


def _assert_auto_status_contract(section: str) -> None:
    required_claims = (
        "`pending_delivery_count` includes every pending auto artifact, even when its file is missing.",
        "`oldest_pending_delivery` is the oldest pending artifact whose output file still exists.",
        "A missing file stays visible in the count but is skipped as retryable work.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing auto status contract: {claim}"


def _assert_variety_contract(section: str) -> None:
    required_claims = (
        "it proposes only the latest completed month.",
        "The previous category cannot repeat.",
        "A category cannot appear more than twice in the last six completed automatic runs.",
        "A monthly review cannot run twice in the same calendar month.",
        "one delivery retry or generation action per wake",
        "Automation does not quietly relax the rules just to produce another video.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing variety contract: {claim}"


def _assert_health_contract(section: str) -> None:
    required_claims = (
        "`GET /health/live` is process liveness.",
        "It returns HTTP 200 while the web process can answer and does not read configuration, "
        "Immich, or SQLite.",
        "`GET /health/ready` is dependency readiness.",
        "It returns HTTP 200 only when Immich is configured, its API contract resolves, and "
        "authentication succeeds. Otherwise it returns HTTP 503.",
        "`GET /health` is the legacy compatibility route.",
        "It returns the detailed payload with HTTP 200 even when `status` is `degraded`.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing health contract: {claim}"


def _assert_encoding_contract(section: str) -> None:
    required_claims = (
        'hdr_mode: "auto"',
        "H.264 is SDR-only and accepts MP4 or MOV.",
        "H.265 accepts MP4 or MOV and is the only codec that can preserve HLG or PQ HDR.",
        "ProRes is SDR-only and requires MOV.",
        "`auto` preserves HDR only with H.265, while `sdr` tone-maps HDR input.",
        "Unsupported combinations fail before rendering.",
        "If required HDR/SDR conversion is unavailable, generation fails without replacing a valid artifact.",
        "A finished artifact is fully decoded and checked for codec, container, pixel format, transfer function, resolution, duration, and size before an atomic publish.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing encoding contract: {claim}"


def test_auto_run_documents_one_action_and_delivery_retry_before_generation() -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## auto run",
        "## auto install",
    )

    _assert_auto_run_contract(section)


@pytest.mark.parametrize(
    "documented",
    [
        "`auto run` performs at most one action per wake.",
        "Before cooldown or candidate discovery, it looks for the oldest retryable pending delivery.",
        "A delivery retry uploads the already-validated artifact to its original album. It does not "
        "render a new video.",
        "That retry consumes the wake whether it completes, fails, or is a dry run.",
        "`action` is `generation` or `delivery_retry` after the automation lease is acquired.",
        "It is `null` when an overlapping wake is skipped before an action starts.",
        "`outcome` is `completed`, `skipped`, `dry_run`, or `failed`.",
        "`completed`, `skipped`, and `dry_run` exit 0. `failed` exits 1.",
        '"action": "generation"',
    ],
)
def test_auto_run_contract_rejects_semantic_mutations(documented: str) -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## auto run",
        "## auto install",
    )
    mutated = section.replace(documented, "[automation guarantee removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_auto_run_contract(mutated)


def test_auto_status_distinguishes_pending_count_from_retryable_artifacts() -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## auto status",
        "## auto test-notification",
    )

    _assert_auto_status_contract(section)


def test_auto_manual_documents_every_hard_variety_rule() -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## How selection works",
        "### Birthday timing",
    )

    _assert_variety_contract(section)


@pytest.mark.parametrize(
    "documented",
    [
        "it proposes only the latest completed month.",
        "The previous category cannot repeat.",
        "A category cannot appear more than twice in the last six completed automatic runs.",
        "A monthly review cannot run twice in the same calendar month.",
        "one delivery retry or generation action per wake",
        "Automation does not quietly relax the rules just to produce another video.",
    ],
)
def test_variety_contract_rejects_semantic_mutations(documented: str) -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## How selection works",
        "### Birthday timing",
    )
    mutated = section.replace(documented, "[variety rule removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_variety_contract(mutated)


@pytest.mark.parametrize(
    "documented",
    [
        "`pending_delivery_count` includes every pending auto artifact, even when its file is missing.",
        "`oldest_pending_delivery` is the oldest pending artifact whose output file still exists.",
        "A missing file stays visible in the count but is skipped as retryable work.",
    ],
)
def test_auto_status_contract_rejects_queue_semantic_mutations(documented: str) -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## auto status",
        "## auto test-notification",
    )
    mutated = section.replace(documented, "[delivery queue semantics removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_auto_status_contract(mutated)


def test_auto_install_writes_but_does_not_activate_the_daily_scheduler() -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/auto.md",
        "## auto install",
        "## auto history",
    )

    assert (
        "On macOS and Linux, `auto install` writes the platform definition but does not activate it."
        in section
    )
    assert "On the crontab fallback, it prints the entry and changes nothing." in section
    assert "Run the printed `Activate` command when you are ready to enable daily runs." in section


def test_health_manual_separates_liveness_readiness_and_legacy_status_codes() -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/health-logs-cache.md",
        "## Health endpoint",
        "## Logging",
    )

    _assert_health_contract(section)


@pytest.mark.parametrize(
    "documented",
    [
        "`GET /health/live` is process liveness.",
        "It returns HTTP 200 while the web process can answer and does not read configuration, "
        "Immich, or SQLite.",
        "`GET /health/ready` is dependency readiness.",
        "It returns HTTP 200 only when Immich is configured, its API contract resolves, and "
        "authentication succeeds. Otherwise it returns HTTP 503.",
        "`GET /health` is the legacy compatibility route.",
        "It returns the detailed payload with HTTP 200 even when `status` is `degraded`.",
    ],
)
def test_health_contract_rejects_endpoint_or_status_mutations(documented: str) -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/health-logs-cache.md",
        "## Health endpoint",
        "## Logging",
    )
    mutated = section.replace(documented, "[health guarantee removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_health_contract(mutated)


def test_docker_manual_uses_liveness_for_health_and_readiness_for_traffic() -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/installation/docker.md",
        "## Health check",
        "## Cache persistence",
    )

    assert "The image health check calls `/health/live`." in section
    assert (
        "Docker marks the container unhealthy when this probe fails; it does not restart it."
        in section
    )
    assert "An orchestrator may act on that unhealthy state." in section
    assert (
        "Use `/health/ready` for dependency readiness; it returns HTTP 503 when the configured Immich dependency is not ready."
        in section
    )
    assert (
        "The legacy `/health` route always returns HTTP 200 and is not a readiness probe."
        in section
    )


def test_config_reference_documents_the_fail_closed_encoding_matrix() -> None:
    section = _normalized_section(
        "docs-site/docs/reference/config-reference.md",
        "## Output",
        "## Photos",
    )

    _assert_encoding_contract(section)


@pytest.mark.parametrize(
    "documented",
    [
        'hdr_mode: "auto"',
        "H.264 is SDR-only and accepts MP4 or MOV.",
        "H.265 accepts MP4 or MOV and is the only codec that can preserve HLG or PQ HDR.",
        "ProRes is SDR-only and requires MOV.",
        "`auto` preserves HDR only with H.265, while `sdr` tone-maps HDR input.",
        "Unsupported combinations fail before rendering.",
        "If required HDR/SDR conversion is unavailable, generation fails without replacing a valid artifact.",
        "A finished artifact is fully decoded and checked for codec, container, pixel format, transfer function, resolution, duration, and size before an atomic publish.",
    ],
)
def test_encoding_contract_rejects_matrix_mutations(documented: str) -> None:
    section = _normalized_section(
        "docs-site/docs/reference/config-reference.md", "## Output", "## Photos"
    )
    mutated = section.replace(documented, "[encoding guarantee removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_encoding_contract(mutated)


def test_config_reference_keeps_hardware_fallback_in_the_requested_codec() -> None:
    section = _normalized_section(
        "docs-site/docs/reference/config-reference.md",
        "## Hardware acceleration",
        "## Audio and music",
    )

    assert "Turning hardware off selects the software encoder for the requested codec." in section
    assert (
        "If a hardware encoder is unavailable or fails, the app falls back to the matching software encoder."
        in section
    )
    assert "It never silently changes the requested codec." in section


def test_docker_manual_documents_explicit_features_and_persistent_paths() -> None:
    text = _read("docs-site/docs/deploy/installation/docker.md")
    section = text.split("## Resource requirements", 1)[1].split("## Standalone Docker run", 1)[0]
    section = " ".join(section.split())

    assert "Published images use `INSTALL_EXTRAS=all`." in section
    assert (
        "`none`, `face`, `mac`, `audio`, `audio-ml`, `auth`, `demucs`, `gpu`, `all`, `all-mac`, and `dev`"
        in section
    )
    assert (
        "A blank value, typo, or undeclared extra stops the build instead of installing the base package."
        in section
    )
    assert "ACE-Step is not a pip extra and is not added by `INSTALL_EXTRAS`." in section
    assert "/home/immich/.immich-memories" in text
    _assert_docker_output_mounts(text)
    assert "cache backup /app/output/cache-backup.db" in text
    assert "cache export /app/output/scores.json" in text
    assert "cache import /app/output/scores.json" in text
    assert "cache backup /output/" not in text


@pytest.mark.parametrize(
    "heading",
    ["## Standalone Docker run", "## Adding to your existing Immich stack"],
)
def test_each_runnable_docker_example_requires_its_own_output_mount(heading: str) -> None:
    text = _read("docs-site/docs/deploy/installation/docker.md")
    before, rest = text.split(heading, 1)
    section, after = rest.split("##", 1)
    mutated = (
        before
        + heading
        + section.replace("./output:/app/output", "./output:/tmp", 1)
        + "##"
        + after
    )

    with pytest.raises(AssertionError):
        _assert_docker_output_mounts(mutated)


def test_automation_recipe_names_auto_run_as_the_single_daily_entry_point() -> None:
    section = _normalized_section(
        "docs-site/docs/create/recipes/automated-generation.md",
        "## Smart Automation (Recommended)",
        "## Advanced/Legacy Scheduler",
    )

    assert "`auto run` is the recommended single daily entry point." in section
    assert (
        "Each wake does at most one action: retry a pending delivery, generate one candidate, or skip."
        in section
    )
    assert (
        "`auto install` writes launchd or systemd files without activating them; the crontab fallback only prints its entry."
        in section
    )


def test_scheduler_manual_labels_the_explicit_daemon_advanced_and_legacy() -> None:
    section = _normalized_section(
        "docs-site/docs/create/cli/scheduler.md",
        "# scheduler",
        "## scheduler list",
    )

    assert "This is the advanced/legacy scheduler." in section
    assert (
        "For normal unattended use, schedule the single daily [`auto run`](./auto.md#auto-run) decision instead."
        in section
    )


def test_published_cli_reference_matches_the_current_click_tree() -> None:
    from scripts.generate_cli_docs import DEFAULT_OUTPUT_PATH, generate_reference

    from immich_memories.cli import main as cli_main

    expected_path = Path("docs-site/docs/reference/cli-reference.md")

    assert expected_path == DEFAULT_OUTPUT_PATH
    assert (REPO_ROOT / expected_path).read_text() == generate_reference(cli_main)


def test_cli_reference_generator_includes_root_options_and_current_choices() -> None:
    from scripts.generate_cli_docs import generate_reference

    from immich_memories.cli import main as cli_main

    generated = generate_reference(cli_main)

    assert "## Global options" in generated
    assert "`--config`, `-c`" in generated
    assert "`--version`" in generated
    assert "`--help`" in generated
    assert "### `auto status`" in generated
    assert "immich-memories auto [OPTIONS] COMMAND [ARGS]..." in generated
    assert "`--reload`, `--no-reload`" in generated
    assert "`mp4`, `h265`, `prores`" in generated
    assert "\x08" not in generated
    assert "--automation-attempt-id" not in generated
    assert all(line == line.rstrip() for line in generated.splitlines())


def test_readme_names_the_canonical_daily_decision_command() -> None:
    section = _normalized_section("README.md", "## Key Features", "## Documentation")

    assert "`immich-memories auto run` is the canonical daily entry point" in section
    assert "retry one pending delivery, generate one eligible memory, or skip" in section
    assert (
        "`auto install` prepares the scheduler definition but leaves activation to you" in section
    )
    assert (
        "H.264, H.265, and SDR ProRes outputs keep the requested codec through hardware fallback"
        in section
    )


def test_homepage_claims_match_smart_automation_and_optional_network_features() -> None:
    text = _read("docs-site/src/pages/index.tsx")

    assert "title: '7 memory types'" in text
    assert "multi-person" in text
    assert "<code>auto run</code> wakes once a day" in text
    assert "retry delivery, generate one eligible memory, or skip" in text
    assert "Video encoding runs locally." in text
    assert "Remote AI, trip map tiles, and optional font downloads make outbound requests." in text
    assert "<strong>Local by default, not offline</strong>" in text
    assert (
        "Upload-back is opt-in and adds the finished video; source assets stay untouched." in text
    )
    assert "Built-in cron scheduler" not in text
    assert "Your data stays home" not in text
    assert "Zero cloud calls" not in text
    assert "No risk of data loss, ever" not in text


@pytest.mark.parametrize(
    ("relative_path", "heading", "next_heading", "required_fields"),
    [
        pytest.param(
            "docs-site/docs/deploy/configuration/config-file.md",
            "## Quick start config",
            "## Immich API compatibility",
            ('format: "mp4"', 'codec: "h264"', 'hdr_mode: "auto"'),
            id="yaml-config",
        ),
        pytest.param(
            "docs-site/docs/deploy/configuration/environment-variables.md",
            "### Output",
            "### Music generation",
            (
                'IMMICH_MEMORIES_OUTPUT__FORMAT="mp4"',
                'IMMICH_MEMORIES_OUTPUT__CODEC="h265"',
                'IMMICH_MEMORIES_OUTPUT__HDR_MODE="auto"',
            ),
            id="environment",
        ),
    ],
)
def test_primary_configuration_guides_show_codec_container_and_hdr_together(
    relative_path: str,
    heading: str,
    next_heading: str,
    required_fields: tuple[str, ...],
) -> None:
    section = _normalized_section(relative_path, heading, next_heading)

    for field in required_fields:
        assert field in section
    assert (
        "H.264 and ProRes are SDR-only; ProRes requires MOV; H.265 is the only HDR output."
        in section
    )


def test_troubleshooting_explains_pending_delivery_without_rerendering() -> None:
    section = _normalized_section(
        "docs-site/docs/reference/troubleshooting.md",
        "## Pending Immich Delivery",
        "## No Videos Found",
    )

    assert (
        "Run `immich-memories auto status --json` to inspect `pending_delivery_count` and `oldest_pending_delivery`."
        in section
    )
    assert (
        "The next `auto run` retries the existing artifact and its original album before cooldown or discovery."
        in section
    )
    assert (
        "A failed retry exits 1, keeps the run completed and pending, and does not rerender the video."
        in section
    )
    assert (
        "A missing artifact remains in the pending count but is skipped until the file is restored."
        in section
    )


@pytest.mark.parametrize(
    ("relative_path", "heading", "next_heading", "runtime_claim", "manual_override_claim"),
    PRIMARY_MANUAL_CONTRACTS,
)
def test_primary_manuals_document_the_default_immich_version_policy(
    relative_path: str,
    heading: str,
    next_heading: str,
    runtime_claim: str,
    manual_override_claim: str,
) -> None:
    section = _normalized_section(relative_path, heading, next_heading)

    _assert_primary_manual_policy(section, runtime_claim, manual_override_claim)


@pytest.mark.parametrize("claim_name", ["runtime", "manual-override"])
@pytest.mark.parametrize(
    ("relative_path", "heading", "next_heading", "runtime_claim", "manual_override_claim"),
    PRIMARY_MANUAL_CONTRACTS,
)
def test_primary_manual_contract_rejects_policy_mutations(
    relative_path: str,
    heading: str,
    next_heading: str,
    runtime_claim: str,
    manual_override_claim: str,
    claim_name: str,
) -> None:
    section = _normalized_section(relative_path, heading, next_heading)
    documented = runtime_claim if claim_name == "runtime" else manual_override_claim
    mutated = section.replace(documented, f"[{claim_name} semantics removed]", 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_primary_manual_policy(mutated, runtime_claim, manual_override_claim)


def test_immich_environment_override_is_documented() -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/configuration/environment-variables.md",
        "### Immich connection",
        "### Analysis settings",
    )

    assert 'IMMICH_MEMORIES_IMMICH__API_VERSION="auto"' in section
    assert "Keep `API_VERSION` on `auto` for default runtime detection" in section
    assert "`v2` and `v3` are manual troubleshooting overrides—escape hatches" in section


def test_upgrade_manual_explains_the_complete_v2_to_v3_contract() -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/upgrading.md",
        "## Upgrading Immich from v2 to v3",
        "## Config compatibility",
    )

    _assert_upgrade_contract(section)


@pytest.mark.parametrize(
    ("documented", "weakened"),
    [
        pytest.param(
            "`v2` and `v3` are manual troubleshooting escape hatches",
            "`v2` and `v3` are routine settings",
            id="manual-overrides-are-escape-hatches",
        ),
        pytest.param(
            "they force the selected contract",
            "they prefer the selected contract",
            id="manual-overrides-force-contract",
        ),
        pytest.param(
            "v3 integer milliseconds are normalized to seconds",
            "v3 integer durations are normalized to seconds",
            id="v3-duration-is-milliseconds",
        ),
        pytest.param("`filename`", "`name`", id="v3-upload-uses-filename"),
        pytest.param("`deviceAssetId`", "`legacyAssetId`", id="v3-upload-omits-device-asset-id"),
        pytest.param("`deviceId`", "`legacyDeviceId`", id="v3-upload-omits-device-id"),
        pytest.param(
            "UTC offset, which v3 requires",
            "serialized date value",
            id="v3-search-dates-have-utc-offset",
        ),
        pytest.param(
            "This is a read-only authentication and compatibility check.",
            "This is an authentication and compatibility check.",
            id="config-test-is-read-only",
        ),
    ],
)
def test_upgrade_contract_rejects_semantic_mutations(documented: str, weakened: str) -> None:
    section = _normalized_section(
        "docs-site/docs/deploy/maintenance/upgrading.md",
        "## Upgrading Immich from v2 to v3",
        "## Config compatibility",
    )
    mutated = section.replace(documented, weakened, 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_upgrade_contract(mutated)


def test_troubleshooting_documents_the_exact_version_probe_and_upload_diagnostics() -> None:
    section = _normalized_section(
        "docs-site/docs/reference/troubleshooting.md",
        "## Immich v2/v3 Version Mismatch",
        "## No Videos Found",
    )

    _assert_troubleshooting_contract(section)


@pytest.mark.parametrize(
    ("documented", "weakened"),
    [
        pytest.param(
            "hides or rewrites `/api/server/version`",
            "hides or rewrites `/server/version`",
            id="exact-version-probe-route",
        ),
        pytest.param(
            "it does not test uploads",
            "it tests uploads",
            id="config-test-does-not-test-uploads",
        ),
        pytest.param(
            "If a v3 upload fails, keep the error shown by the command doing the upload and check "
            "the relevant Immich server logs. API keys are redacted.",
            "If a v3 upload fails, keep the HTTP status and `X-Correlation-ID` from `config test`.",
            id="accurate-upload-diagnostics",
        ),
    ],
)
def test_troubleshooting_contract_rejects_diagnostic_mutations(
    documented: str, weakened: str
) -> None:
    section = _normalized_section(
        "docs-site/docs/reference/troubleshooting.md",
        "## Immich v2/v3 Version Mismatch",
        "## No Videos Found",
    )
    mutated = section.replace(documented, weakened, 1)

    assert mutated != section
    with pytest.raises(AssertionError):
        _assert_troubleshooting_contract(mutated)


def test_readme_does_not_claim_v1_only_or_pin_v2_5_6() -> None:
    text = _read("README.md")

    assert "Immich v2.5.6" not in text
    assert "v1.100+" not in text


def test_launch_audit_closes_the_immich_code_and_docs_blocker() -> None:
    section = _normalized_section(
        "docs/reviews/2026-08-11-launch-readiness-audit.md",
        "## Remediation status — 2026-08-11",
        "## Safety action already taken",
    )

    assert "P0.3 Immich v2/v3: fixed and independently approved" in section
    assert "closed through `144c38f`" in section
    assert "public compatibility docs landed in `59e97b2`" in section
    assert "final compatibility gate passed 465 tests" in section
    assert (
        "Live read-only `immich-memories config test` passed with both default `auto` detection"
        in section
    )
    assert "an explicit `v3` override" in section


def test_launch_audit_closes_p0_encoding_version_and_browser_findings() -> None:
    section = _normalized_section(
        "docs/reviews/2026-08-11-launch-readiness-audit.md",
        "## Remediation status — 2026-08-11",
        "## Safety action already taken",
    )

    required_claims = (
        "P0.4 encoding and delivery: fixed and independently approved after final branch integration",
        "Encoding-plan enforcement starts at `42accf2`",
        "Durable lifecycle and UI delivery truth were finalized in `bba9885`, `0ba70b6`, and `bc455b6`",
        "retry persistence in `7c5193e` and `bb2ecd6`",
        "best-effort completion observer policy in `40cc287`",
        "Exact HDR, container, trimming, music staging, and UI output semantics landed in `4dde2f3` and `9e19359`",
        "bounded same-codec hardware fallback landed in `4fc372e`",
        "P0.5 version reporting: fixed and independently approved",
        "Hatch VCS is the runtime and build source through `b78af3c`",
        "container labels and explicit build identity landed in `90ba156`",
        "P0.6 browser E2E: fixed and independently approved",
        "The real fake-Immich browser render became required in `485ff75`",
        "the required CI launch gate landed in `e5367a2`",
        "The LaunchAgent remains unloaded.",
    )
    for claim in required_claims:
        assert claim in section, f"Missing P0 closure evidence: {claim}"
    assert "P0.4–P0.6: still open" not in section
