"""Platform-specific scheduler integration (launchd, systemd, crontab)."""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from immich_memories.automation.runtime_provenance import (
    CheckoutDrift,
    checkout_drift,
    git_checkout_root,
)


@dataclass
class SchedulerInstallResult:
    """Result of installing a system scheduler."""

    platform: str
    files_written: list[Path] = field(default_factory=list)
    activate_command: str = ""
    deactivate_command: str = ""


@dataclass(frozen=True)
class SchedulerStatus:
    """Read-only facts about the external scheduler."""

    platform: str
    installed: bool | None
    active: bool | None
    paths: tuple[Path, ...] = ()


_LAUNCHD_LABEL = "com.immich-memories.auto"
_SYSTEMD_SERVICE = "immich-memories-auto.service"
_SYSTEMD_TIMER = "immich-memories-auto.timer"
_LAUNCHER_SHIM_NAME = "immich-memories-auto"

# Non-secret tuning knobs documented on the environment-variables page. Anything that can
# hold a credential is deliberately excluded — see `_scheduled_environment`.
_SCHEDULED_TUNING_VARS = (
    "ACESTEP_CHECKPOINTS_DIR",
    "ACESTEP_MLX_VAE_CHUNK",
    "IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32",
    "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
)


class StaleScheduledCodeError(RuntimeError):
    """The code this install would schedule is already frozen or already behind."""


class WorktreePinnedBinaryError(StaleScheduledCodeError):
    """The binary that would be persisted into the scheduler lives in a linked git worktree."""

    def __init__(self, binary: str, worktree: Path) -> None:
        super().__init__(
            f"Refusing to schedule {binary}: it lives in the linked git worktree {worktree}. "
            "A worktree is scratch space — it stays frozen on the commit it was left at, or "
            "gets pruned — so the scheduled job would silently keep running stale code. "
            "Install from your canonical checkout (or a system/user install) instead, "
            "or pass --force to schedule this exact path anyway."
        )


class StaleCheckoutError(StaleScheduledCodeError):
    """The checkout backing this binary is already behind the branch it tracks."""

    def __init__(self, binary: str, checkout: Path, drift: CheckoutDrift) -> None:
        super().__init__(
            f"Refusing to schedule {binary}: its checkout {checkout} is "
            f"{drift.commits_behind} commit(s) behind {drift.upstream}. Nothing updates a "
            "checkout on your behalf, so the scheduled job would re-run this exact code every "
            "night while the logs look normal. Update it (git pull) and reinstall, or pass "
            "--force to schedule it as it is."
        )


def detect_platform() -> str:
    """Return 'launchd' on macOS, 'systemd' if systemctl exists, else 'crontab'."""
    if sys.platform == "darwin":
        return "launchd"
    if shutil.which("systemctl"):
        return "systemd"
    return "crontab"


def _resolve_binary() -> str:
    binary = shutil.which("immich-memories")
    if not binary:
        msg = "immich-memories binary not found in PATH"
        raise FileNotFoundError(msg)
    return binary


def _linked_worktree_root(binary: str) -> Path | None:
    """Return the linked-worktree checkout holding this path, if it sits in one.

    A linked worktree's root holds a `.git` *file* pointing at `.../worktrees/<name>`, where a
    plain clone holds a `.git` directory and a submodule's `.git` file points at `.../modules/`.
    """
    for directory in Path(binary).parents:
        marker = directory / ".git"
        if not marker.is_file():
            continue
        try:
            gitdir = marker.read_text(errors="replace").partition("gitdir:")[2].strip()
        except OSError:
            continue
        if gitdir and "worktrees" in Path(gitdir).parts:
            return directory
    return None


def _guard_scheduled_code_freshness(binary: str) -> None:
    """Refuse to schedule code that is already frozen, or already behind its upstream.

    Both refusals describe the same #573 failure: a scheduled job re-runs one checkout
    forever, so anything already stale at install time stays stale silently for months.
    """
    worktree = _linked_worktree_root(binary)
    if worktree is not None:
        raise WorktreePinnedBinaryError(binary, worktree)

    checkout = git_checkout_root(Path(binary))
    if checkout is None:
        return
    drift = checkout_drift(checkout)
    if drift is not None:
        raise StaleCheckoutError(binary, checkout, drift)


def _default_log_dir() -> Path:
    return Path.home() / ".immich-memories" / "logs"


def _install_time_path() -> str:
    return os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")


def _scheduled_environment() -> dict[str, str]:
    """Environment the scheduled job gets, captured from the shell that installed it.

    launchd and cron start a job from a login-less environment, so ACE-Step tuning
    exported in an interactive shell is simply absent at 03:00 — the scheduled and manual
    runs in #573 were tuned differently for exactly this reason. Only the documented
    tuning knobs are copied: `IMMICH_MEMORIES_*` also carries the Immich API key and the
    UI password, and a plist under ~/Library is no place for either.
    """
    env = {"PATH": _install_time_path()}
    env.update({name: os.environ[name] for name in _SCHEDULED_TUNING_VARS if name in os.environ})
    return env


def _launcher_shim_path() -> Path:
    return Path.home() / ".immich-memories" / "bin" / _LAUNCHER_SHIM_NAME


def _launcher_search_path(binary: str) -> str:
    """Put the install's own bin directory first, then the rest of the install-time PATH."""
    own_bin = str(Path(binary).parent)
    entries = [
        own_bin,
        *(part for part in _install_time_path().split(os.pathsep) if part != own_bin),
    ]
    return os.pathsep.join(entries)


def render_launcher_shim(path_env: str) -> str:
    """Render the launcher that schedulers store in place of a resolved binary path.

    launchd, systemd, and cron keep the path they were handed forever and never re-resolve
    it, so a `shutil.which()` result frozen at install time keeps executing whichever
    checkout happened to be first on PATH that day (#573). This shim is the durable address
    instead: it re-runs the lookup on every fire, so upgrading or reinstalling the package
    is enough to change what the scheduled job executes.
    """
    return textwrap.dedent(f"""\
        #!/bin/sh
        # Managed by `immich-memories auto install`; rewritten on every reinstall.
        PATH={shlex.quote(path_env)}
        export PATH
        if ! command -v immich-memories >/dev/null 2>&1; then
            echo "immich-memories is not on the scheduled job's PATH: $PATH" >&2
            exit 127
        fi
        exec immich-memories "$@"
    """)


def _write_launcher_shim(binary: str) -> Path:
    shim = _launcher_shim_path()
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(render_launcher_shim(_launcher_search_path(binary)))
    shim.chmod(0o755)
    return shim


def _remove_launcher_shim() -> bool:
    shim = _launcher_shim_path()
    if not shim.exists():
        return False
    shim.unlink()
    return True


def _auto_command(binary_path: str, cooldown_hours: int, config_path: Path | None) -> list[str]:
    """Build the exact root-option-aware command shared by scheduler backends."""
    command = [binary_path]
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    command.extend(["auto", "run", "--quiet", "--cooldown", str(cooldown_hours)])
    return command


def _systemd_quote_arg(value: str) -> str:
    """Encode one argv element for systemd's ExecStart grammar."""
    safe = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:@+-")
    if value and all(char in safe for char in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$").replace("%", "%%")
    return f'"{escaped}"'


def generate_launchd_plist(
    binary_path: str,
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    log_dir: Path | None = None,
    config_path: Path | None = None,
) -> str:
    """Generate a macOS launchd plist XML string for scheduled auto-generation."""
    log_dir = log_dir or _default_log_dir()

    payload = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": _auto_command(binary_path, cooldown_hours, config_path),
        "StartCalendarInterval": {"Hour": schedule_hour, "Minute": schedule_minute},
        "StandardOutPath": str(log_dir / "auto.log"),
        "StandardErrorPath": str(log_dir / "auto-error.log"),
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": _scheduled_environment(),
    }
    return plistlib.dumps(payload, sort_keys=False).decode()


def generate_systemd_units(
    binary_path: str,
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    config_path: Path | None = None,
) -> tuple[str, str]:
    """Generate systemd service and timer unit file contents."""
    command = " ".join(
        _systemd_quote_arg(arg) for arg in _auto_command(binary_path, cooldown_hours, config_path)
    )
    service = "\n".join(
        [
            "[Unit]",
            "Description=Immich Memories auto-generation",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            *(
                f"Environment={_systemd_quote_arg(f'{key}={value}')}"
                for key, value in _scheduled_environment().items()
            ),
            f"ExecStart={command}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )

    timer = textwrap.dedent(f"""\
        [Unit]
        Description=Immich Memories daily timer

        [Timer]
        OnCalendar=*-*-* {schedule_hour:02d}:{schedule_minute:02d}:00
        Persistent=true

        [Install]
        WantedBy=timers.target
    """)

    return service, timer


def generate_crontab_entry(
    binary_path: str,
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    config_path: Path | None = None,
) -> str:
    """Generate a single crontab line for daily auto-generation."""
    command = shlex.join(_auto_command(binary_path, cooldown_hours, config_path)).replace(
        "%", r"\%"
    )
    return f"{schedule_minute} {schedule_hour} * * * {command}"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _probe_active(command: list[str], inactive_codes: set[int]) -> bool | None:
    """Return active/inactive only for conclusive read-only command results."""
    try:
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode in inactive_codes:
        return False
    return None


def _has_crontab_entry(contents: str) -> bool:
    """Match the generated command only on active crontab lines."""
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            command = shlex.split(line.split(maxsplit=5)[5])
        except (IndexError, ValueError):
            continue
        if not command or Path(command[0]).name != "immich-memories":
            continue
        index = 1
        if command[index : index + 1] in (["--config"], ["-c"]):
            index += 2
        if command[index : index + 2] == ["auto", "run"]:
            return True
    return False


def get_scheduler_status() -> SchedulerStatus:
    """Inspect scheduler installation and activation without changing either."""
    platform = detect_platform()
    if platform == "launchd":
        plist = _launchd_plist_path()
        active = _probe_active(
            ["launchctl", "print", f"gui/{os.getuid()}/{_LAUNCHD_LABEL}"],
            inactive_codes={113},
        )
        return SchedulerStatus(
            platform=platform,
            installed=plist.is_file(),
            active=active,
            paths=(plist,),
        )

    if platform == "systemd":
        user_dir = _systemd_user_dir()
        paths = (
            user_dir / _SYSTEMD_SERVICE,
            user_dir / _SYSTEMD_TIMER,
        )
        active = _probe_active(
            ["systemctl", "--user", "is-active", "--quiet", _SYSTEMD_TIMER],
            inactive_codes={3},
        )
        return SchedulerStatus(
            platform=platform,
            installed=all(path.is_file() for path in paths),
            active=active,
            paths=paths,
        )

    try:
        result = subprocess.run(  # noqa: S603
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        installed = None
    else:
        if result.returncode == 0:
            installed = _has_crontab_entry(result.stdout)
        elif result.returncode == 1 and "no crontab for " in result.stderr.casefold():
            installed = False
        else:
            installed = None
    return SchedulerStatus(platform=platform, installed=installed, active=None)


def install_scheduler(
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    config_path: Path | None = None,
    force: bool = False,
) -> SchedulerInstallResult:
    """Detect platform, generate scheduler files, write them, and return result.

    Raises StaleScheduledCodeError when the resolved binary is backed by a linked git
    worktree or by a checkout already behind its tracking branch, unless `force` is set.
    """
    platform = detect_platform()
    binary = _resolve_binary()
    if not force:
        _guard_scheduled_code_freshness(binary)

    launcher = _write_launcher_shim(binary)
    if platform == "launchd":
        result = _install_launchd(
            str(launcher), schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
    elif platform == "systemd":
        result = _install_systemd(
            str(launcher), schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
    else:
        result = _install_crontab(
            str(launcher), schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
    result.files_written.insert(0, launcher)
    return result


def _install_launchd(
    binary: str,
    hour: int,
    minute: int,
    cooldown: int,
    *,
    config_path: Path | None,
) -> SchedulerInstallResult:
    content = generate_launchd_plist(binary, hour, minute, cooldown, config_path=config_path)
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    _default_log_dir().mkdir(parents=True, exist_ok=True)
    plist_path.write_text(content)

    return SchedulerInstallResult(
        platform="launchd",
        files_written=[plist_path],
        activate_command=f"launchctl load {plist_path}",
        deactivate_command=f"launchctl unload {plist_path}",
    )


def _install_systemd(
    binary: str,
    hour: int,
    minute: int,
    cooldown: int,
    *,
    config_path: Path | None,
) -> SchedulerInstallResult:
    service_content, timer_content = generate_systemd_units(
        binary, hour, minute, cooldown, config_path=config_path
    )
    user_dir = _systemd_user_dir()
    user_dir.mkdir(parents=True, exist_ok=True)

    service_path = user_dir / _SYSTEMD_SERVICE
    timer_path = user_dir / _SYSTEMD_TIMER
    service_path.write_text(service_content)
    timer_path.write_text(timer_content)

    return SchedulerInstallResult(
        platform="systemd",
        files_written=[service_path, timer_path],
        activate_command=f"systemctl --user enable --now {_SYSTEMD_TIMER}",
        deactivate_command=f"systemctl --user disable --now {_SYSTEMD_TIMER}",
    )


def _install_crontab(
    binary: str,
    hour: int,
    minute: int,
    cooldown: int,
    *,
    config_path: Path | None,
) -> SchedulerInstallResult:
    entry = generate_crontab_entry(binary, hour, minute, cooldown, config_path=config_path)
    return SchedulerInstallResult(
        platform="crontab",
        files_written=[],
        activate_command=(
            f"(crontab -l 2>/dev/null; printf '%s\\n' {shlex.quote(entry)}) | crontab -"
        ),
        deactivate_command=(f"crontab -l | grep -Fv -- {shlex.quote(entry)} | crontab -"),
    )


def uninstall_scheduler() -> bool:
    """Remove installed scheduler files. Returns True if something was removed."""
    platform = detect_platform()
    removed = _remove_launcher_shim()

    if platform == "launchd":
        plist = _launchd_plist_path()
        if plist.exists():
            plist.unlink()
            removed = True
        return removed

    if platform == "systemd":
        user_dir = _systemd_user_dir()
        for name in (_SYSTEMD_SERVICE, _SYSTEMD_TIMER):
            path = user_dir / name
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # crontab: the launcher is the only thing this app put on disk
    return removed


def show_scheduler_config(
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    config_path: Path | None = None,
) -> str | None:
    """Generate scheduler config for current platform without writing files."""
    if not shutil.which("immich-memories"):
        return None

    # Preview the launcher `install_scheduler` would write, not today's resolved path.
    binary = str(_launcher_shim_path())
    platform = detect_platform()

    if platform == "launchd":
        return generate_launchd_plist(
            binary,
            schedule_hour,
            schedule_minute,
            cooldown_hours,
            config_path=config_path,
        )
    if platform == "systemd":
        service, timer = generate_systemd_units(
            binary, schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
        return f"# {_SYSTEMD_SERVICE}\n{service}\n# {_SYSTEMD_TIMER}\n{timer}"
    return generate_crontab_entry(
        binary, schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
    )
