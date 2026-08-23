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


class WorktreePinnedBinaryError(RuntimeError):
    """The binary that would be persisted into the scheduler lives in a linked git worktree."""

    def __init__(self, binary: str, worktree: Path) -> None:
        super().__init__(
            f"Refusing to schedule {binary}: it lives in the linked git worktree {worktree}. "
            "A worktree is scratch space — it stays frozen on the commit it was left at, or "
            "gets pruned — so the scheduled job would silently keep running stale code. "
            "Install from your canonical checkout (or a system/user install) instead, "
            "or pass --force to schedule this exact path anyway."
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


def _default_log_dir() -> Path:
    return Path.home() / ".immich-memories" / "logs"


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
    # PATH from current env so FFmpeg, etc. are discoverable
    env_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    payload = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": _auto_command(binary_path, cooldown_hours, config_path),
        "StartCalendarInterval": {"Hour": schedule_hour, "Minute": schedule_minute},
        "StandardOutPath": str(log_dir / "auto.log"),
        "StandardErrorPath": str(log_dir / "auto-error.log"),
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {"PATH": env_path},
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
    service = textwrap.dedent(f"""\
        [Unit]
        Description=Immich Memories auto-generation
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=oneshot
        ExecStart={command}

        [Install]
        WantedBy=default.target
    """)

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

    Raises WorktreePinnedBinaryError when the resolved binary sits inside a linked git worktree,
    unless `force` is set — the OS never re-resolves the absolute path persisted here.
    """
    platform = detect_platform()
    binary = _resolve_binary()
    worktree = None if force else _linked_worktree_root(binary)
    if worktree is not None:
        raise WorktreePinnedBinaryError(binary, worktree)

    if platform == "launchd":
        return _install_launchd(
            binary, schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
    if platform == "systemd":
        return _install_systemd(
            binary, schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
        )
    return _install_crontab(
        binary, schedule_hour, schedule_minute, cooldown_hours, config_path=config_path
    )


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

    if platform == "launchd":
        plist = _launchd_plist_path()
        if plist.exists():
            plist.unlink()
            return True
        return False

    if platform == "systemd":
        user_dir = _systemd_user_dir()
        removed = False
        for name in (_SYSTEMD_SERVICE, _SYSTEMD_TIMER):
            path = user_dir / name
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # crontab: nothing to remove on disk
    return False


def show_scheduler_config(
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    config_path: Path | None = None,
) -> str | None:
    """Generate scheduler config for current platform without writing files."""
    binary = shutil.which("immich-memories")
    if not binary:
        return None

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
