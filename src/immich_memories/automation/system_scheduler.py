"""Platform-specific scheduler integration (launchd, systemd, crontab)."""

from __future__ import annotations

import os
import shutil
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


_LAUNCHD_LABEL = "com.immich-memories.auto"
_SYSTEMD_SERVICE = "immich-memories-auto.service"
_SYSTEMD_TIMER = "immich-memories-auto.timer"


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


def _default_log_dir() -> Path:
    return Path.home() / ".immich-memories" / "logs"


def generate_launchd_plist(
    binary_path: str,
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
    log_dir: Path | None = None,
) -> str:
    """Generate a macOS launchd plist XML string for scheduled auto-generation."""
    log_dir = log_dir or _default_log_dir()
    # PATH from current env so FFmpeg, etc. are discoverable
    env_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_LAUNCHD_LABEL}</string>

            <key>ProgramArguments</key>
            <array>
                <string>{binary_path}</string>
                <string>auto</string>
                <string>run</string>
                <string>--quiet</string>
                <string>--cooldown</string>
                <string>{cooldown_hours}</string>
            </array>

            <key>StartCalendarInterval</key>
            <dict>
                <key>Hour</key>
                <integer>{schedule_hour}</integer>
                <key>Minute</key>
                <integer>{schedule_minute}</integer>
            </dict>

            <key>StandardOutPath</key>
            <string>{log_dir}/auto.log</string>
            <key>StandardErrorPath</key>
            <string>{log_dir}/auto-error.log</string>

            <key>WorkingDirectory</key>
            <string>{Path.home()}</string>

            <key>EnvironmentVariables</key>
            <dict>
                <key>PATH</key>
                <string>{env_path}</string>
            </dict>
        </dict>
        </plist>
    """)


def generate_systemd_units(
    binary_path: str,
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
) -> tuple[str, str]:
    """Generate systemd service and timer unit file contents."""
    service = textwrap.dedent(f"""\
        [Unit]
        Description=Immich Memories auto-generation
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=oneshot
        ExecStart={binary_path} auto run --quiet --cooldown {cooldown_hours}

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
) -> str:
    """Generate a single crontab line for daily auto-generation."""
    return f"{schedule_minute} {schedule_hour} * * * {binary_path} auto run --quiet --cooldown {cooldown_hours}"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def install_scheduler(
    schedule_hour: int = 9,
    schedule_minute: int = 0,
    cooldown_hours: int = 24,
) -> SchedulerInstallResult:
    """Detect platform, generate scheduler files, write them, and return result."""
    platform = detect_platform()
    binary = _resolve_binary()

    if platform == "launchd":
        return _install_launchd(binary, schedule_hour, schedule_minute, cooldown_hours)
    if platform == "systemd":
        return _install_systemd(binary, schedule_hour, schedule_minute, cooldown_hours)
    return _install_crontab(binary, schedule_hour, schedule_minute, cooldown_hours)


def _install_launchd(binary: str, hour: int, minute: int, cooldown: int) -> SchedulerInstallResult:
    content = generate_launchd_plist(binary, hour, minute, cooldown)
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


def _install_systemd(binary: str, hour: int, minute: int, cooldown: int) -> SchedulerInstallResult:
    service_content, timer_content = generate_systemd_units(binary, hour, minute, cooldown)
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


def _install_crontab(binary: str, hour: int, minute: int, cooldown: int) -> SchedulerInstallResult:
    entry = generate_crontab_entry(binary, hour, minute, cooldown)
    return SchedulerInstallResult(
        platform="crontab",
        files_written=[],
        activate_command=f'(crontab -l 2>/dev/null; echo "{entry}") | crontab -',
        deactivate_command="crontab -l | grep -v 'immich-memories auto run' | crontab -",
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
) -> str | None:
    """Generate scheduler config for current platform without writing files."""
    binary = shutil.which("immich-memories")
    if not binary:
        return None

    platform = detect_platform()

    if platform == "launchd":
        return generate_launchd_plist(binary, schedule_hour, schedule_minute, cooldown_hours)
    if platform == "systemd":
        service, timer = generate_systemd_units(
            binary, schedule_hour, schedule_minute, cooldown_hours
        )
        return f"# {_SYSTEMD_SERVICE}\n{service}\n# {_SYSTEMD_TIMER}\n{timer}"
    return generate_crontab_entry(binary, schedule_hour, schedule_minute, cooldown_hours)
