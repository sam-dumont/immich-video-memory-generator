"""Tests for platform-specific scheduler integration."""

from __future__ import annotations

import plistlib
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.automation.system_scheduler import (
    SchedulerInstallResult,
    detect_platform,
    generate_crontab_entry,
    generate_launchd_plist,
    generate_systemd_units,
    get_scheduler_status,
    install_scheduler,
    show_scheduler_config,
    uninstall_scheduler,
)


class TestDetectPlatform:
    @patch("immich_memories.automation.system_scheduler.sys")
    def test_macos_returns_launchd(self, mock_sys: object) -> None:
        # WHY: sys.platform is OS-specific
        mock_sys.platform = "darwin"  # type: ignore[attr-defined]
        assert detect_platform() == "launchd"

    @patch(
        "immich_memories.automation.system_scheduler.shutil.which",
        return_value="/usr/bin/systemctl",
    )
    @patch("immich_memories.automation.system_scheduler.sys")
    def test_linux_with_systemctl_returns_systemd(self, mock_sys: object, _which: object) -> None:
        # WHY: sys.platform and shutil.which are OS-specific
        mock_sys.platform = "linux"  # type: ignore[attr-defined]
        assert detect_platform() == "systemd"

    @patch("immich_memories.automation.system_scheduler.shutil.which", return_value=None)
    @patch("immich_memories.automation.system_scheduler.sys")
    def test_linux_without_systemctl_returns_crontab(
        self, mock_sys: object, _which: object
    ) -> None:
        # WHY: sys.platform and shutil.which are OS-specific
        mock_sys.platform = "linux"  # type: ignore[attr-defined]
        assert detect_platform() == "crontab"


class TestGenerateLaunchdPlist:
    def test_valid_xml(self) -> None:
        plist = generate_launchd_plist("/usr/local/bin/immich-memories")
        # Should parse without error (our own output, not untrusted data)
        ET.fromstring(plist)  # noqa: S314

    def test_binary_path_in_program_arguments(self) -> None:
        plist = generate_launchd_plist("/opt/bin/immich-memories")
        assert "/opt/bin/immich-memories" in plist

    def test_schedule_hour_and_minute(self) -> None:
        plist = generate_launchd_plist("/bin/im", schedule_hour=14, schedule_minute=30)
        root = ET.fromstring(plist)  # noqa: S314
        # Find the StartCalendarInterval dict
        keys = [el.text for el in root.iter("key")]
        assert "Hour" in keys
        assert "Minute" in keys
        integers = [el.text for el in root.iter("integer")]
        assert "14" in integers
        assert "30" in integers

    def test_custom_log_dir(self) -> None:
        plist = generate_launchd_plist("/bin/im", log_dir=Path("/var/log/custom"))
        assert "/var/log/custom/auto.log" in plist
        assert "/var/log/custom/auto-error.log" in plist

    def test_label(self) -> None:
        plist = generate_launchd_plist("/bin/im")
        assert "com.immich-memories.auto" in plist

    def test_cooldown_in_arguments(self) -> None:
        plist = generate_launchd_plist("/bin/im", cooldown_hours=48)
        root = ET.fromstring(plist)  # noqa: S314
        strings = [el.text for el in root.iter("string")]
        assert "--cooldown" in strings
        assert "48" in strings

    def test_path_env_included(self) -> None:
        plist = generate_launchd_plist("/bin/im")
        assert "PATH" in plist

    def test_custom_config_is_exact_xml_safe_program_argument(self) -> None:
        config_path = Path('/tmp/Config & family/one <two> "three".yaml')

        parsed = plistlib.loads(generate_launchd_plist("/bin/im", config_path=config_path).encode())

        assert parsed["ProgramArguments"] == [
            "/bin/im",
            "--config",
            str(config_path),
            "auto",
            "run",
            "--quiet",
            "--cooldown",
            "24",
        ]


class TestGenerateSystemdUnits:
    def test_service_has_oneshot_type(self) -> None:
        service, _ = generate_systemd_units("/bin/im")
        assert "Type=oneshot" in service

    def test_timer_has_oncalendar(self) -> None:
        _, timer = generate_systemd_units("/bin/im", schedule_hour=9, schedule_minute=0)
        assert "OnCalendar=*-*-* 09:00:00" in timer

    def test_binary_path_in_execstart(self) -> None:
        service, _ = generate_systemd_units("/opt/bin/immich-memories")
        assert "ExecStart=/opt/bin/immich-memories auto run" in service

    def test_custom_schedule(self) -> None:
        _, timer = generate_systemd_units("/bin/im", schedule_hour=22, schedule_minute=15)
        assert "OnCalendar=*-*-* 22:15:00" in timer

    def test_cooldown_in_execstart(self) -> None:
        service, _ = generate_systemd_units("/bin/im", cooldown_hours=12)
        assert "--cooldown 12" in service

    def test_timer_persistent(self) -> None:
        _, timer = generate_systemd_units("/bin/im")
        assert "Persistent=true" in timer

    def test_custom_config_is_quoted_before_subcommand(self) -> None:
        config_path = Path('/tmp/Config dir/family $HOME 50% "best".yaml')

        service, _ = generate_systemd_units("/bin/im", config_path=config_path)

        assert (
            "ExecStart=/bin/im --config "
            '"/tmp/Config dir/family $$HOME 50%% \\"best\\".yaml" auto run '
            "--quiet --cooldown 24"
        ) in service


class TestGenerateCrontabEntry:
    def test_valid_cron_format(self) -> None:
        entry = generate_crontab_entry("/bin/im")
        parts = entry.split()
        assert len(parts) >= 5
        # minute hour day month dow
        assert parts[0] == "0"
        assert parts[1] == "9"
        assert parts[2] == "*"
        assert parts[3] == "*"
        assert parts[4] == "*"

    def test_custom_hour_minute(self) -> None:
        entry = generate_crontab_entry("/bin/im", schedule_hour=17, schedule_minute=45)
        assert entry.startswith("45 17 * * *")

    def test_binary_path_in_entry(self) -> None:
        entry = generate_crontab_entry("/usr/local/bin/immich-memories")
        assert "/usr/local/bin/immich-memories" in entry

    def test_cooldown_in_entry(self) -> None:
        entry = generate_crontab_entry("/bin/im", cooldown_hours=6)
        assert "--cooldown 6" in entry

    def test_custom_config_round_trips_through_shell_parser(self) -> None:
        config_path = Path("/tmp/Config dir/family's & photos.yaml")

        entry = generate_crontab_entry("/bin/im", config_path=config_path)
        command = entry.split(maxsplit=5)[5]

        assert shlex.split(command) == [
            "/bin/im",
            "--config",
            str(config_path),
            "auto",
            "run",
            "--quiet",
            "--cooldown",
            "24",
        ]

    def test_custom_config_escapes_cron_percent_before_command_dispatch(self) -> None:
        """Cron must not turn a percent in the config path into stdin."""
        entry = generate_crontab_entry(
            "/bin/im",
            config_path=Path("/tmp/Config dir/family%archive.yaml"),
        )
        command = entry.split(maxsplit=5)[5]

        assert r"family\%archive.yaml" in command
        assert "family%archive.yaml" not in command


class TestInstallScheduler:
    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    @patch("immich_memories.automation.system_scheduler._resolve_binary", return_value="/bin/im")
    def test_launchd_writes_plist(self, _bin: object, _plat: object, tmp_path: Path) -> None:
        # WHY: _resolve_binary checks PATH, detect_platform checks OS
        plist_path = tmp_path / "LaunchAgents" / "com.immich-memories.auto.plist"
        log_dir = tmp_path / "logs"

        with (
            patch(
                "immich_memories.automation.system_scheduler._launchd_plist_path",
                return_value=plist_path,
            ),
            patch(
                "immich_memories.automation.system_scheduler._default_log_dir",
                return_value=log_dir,
            ),
        ):
            result = install_scheduler(schedule_hour=10, schedule_minute=30)

        assert result.platform == "launchd"
        assert plist_path in result.files_written
        assert plist_path.exists()
        assert "launchctl load" in result.activate_command

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="systemd")
    @patch("immich_memories.automation.system_scheduler._resolve_binary", return_value="/bin/im")
    def test_systemd_writes_units(self, _bin: object, _plat: object, tmp_path: Path) -> None:
        # WHY: _resolve_binary checks PATH, detect_platform checks OS
        with patch(
            "immich_memories.automation.system_scheduler._systemd_user_dir",
            return_value=tmp_path,
        ):
            result = install_scheduler()

        assert result.platform == "systemd"
        assert len(result.files_written) == 2
        assert (tmp_path / "immich-memories-auto.service").exists()
        assert (tmp_path / "immich-memories-auto.timer").exists()
        assert "systemctl --user enable" in result.activate_command

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    @patch("immich_memories.automation.system_scheduler._resolve_binary", return_value="/bin/im")
    def test_crontab_returns_command(self, _bin: object, _plat: object) -> None:
        # WHY: _resolve_binary checks PATH, detect_platform checks OS
        result = install_scheduler()
        assert result.platform == "crontab"
        assert result.files_written == []
        assert "crontab" in result.activate_command

    @patch(
        "immich_memories.automation.system_scheduler._resolve_binary", side_effect=FileNotFoundError
    )
    def test_missing_binary_raises(self, _bin: object) -> None:
        # WHY: _resolve_binary checks PATH
        with pytest.raises(FileNotFoundError):
            install_scheduler()


class TestUninstallScheduler:
    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    def test_launchd_removes_plist(self, _plat: object, tmp_path: Path) -> None:
        # WHY: detect_platform checks OS
        plist = tmp_path / "com.immich-memories.auto.plist"
        plist.write_text("<plist/>")

        with patch(
            "immich_memories.automation.system_scheduler._launchd_plist_path",
            return_value=plist,
        ):
            assert uninstall_scheduler() is True
            assert not plist.exists()

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    def test_launchd_returns_false_when_no_plist(self, _plat: object, tmp_path: Path) -> None:
        # WHY: detect_platform checks OS
        with patch(
            "immich_memories.automation.system_scheduler._launchd_plist_path",
            return_value=tmp_path / "nonexistent.plist",
        ):
            assert uninstall_scheduler() is False

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_crontab_returns_false(self, _plat: object) -> None:
        # WHY: detect_platform checks OS
        assert uninstall_scheduler() is False


class TestShowSchedulerConfig:
    @patch("immich_memories.automation.system_scheduler.shutil.which", return_value="/bin/im")
    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    def test_launchd_returns_plist(self, _plat: object, _which: object) -> None:
        # WHY: shutil.which checks PATH, detect_platform checks OS
        result = show_scheduler_config()
        assert result is not None
        assert "com.immich-memories.auto" in result

    @patch("immich_memories.automation.system_scheduler.shutil.which", return_value="/bin/im")
    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="systemd")
    def test_systemd_returns_both_units(self, _plat: object, _which: object) -> None:
        # WHY: shutil.which checks PATH, detect_platform checks OS
        result = show_scheduler_config()
        assert result is not None
        assert "immich-memories-auto.service" in result
        assert "immich-memories-auto.timer" in result

    @patch("immich_memories.automation.system_scheduler.shutil.which", return_value=None)
    def test_returns_none_when_binary_missing(self, _which: object) -> None:
        # WHY: shutil.which checks PATH
        assert show_scheduler_config() is None


class TestSchedulerInstallResult:
    def test_defaults(self) -> None:
        result = SchedulerInstallResult(platform="test")
        assert result.files_written == []
        assert result.activate_command == ""
        assert result.deactivate_command == ""


class TestSchedulerStatus:
    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    def test_launchd_detection_is_read_only_and_distinguishes_active(
        self, _platform: object, tmp_path: Path
    ) -> None:
        plist = tmp_path / "com.immich-memories.auto.plist"
        plist.write_text("<plist/>")

        with (
            patch(
                "immich_memories.automation.system_scheduler._launchd_plist_path",
                return_value=plist,
            ),
            patch("immich_memories.automation.system_scheduler.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            status = get_scheduler_status()

        assert status.platform == "launchd"
        assert status.installed is True
        assert status.active is True
        assert status.paths == (plist,)
        command = run.call_args.args[0]
        assert command[:2] == ["launchctl", "print"]
        assert not {"load", "unload", "bootstrap", "bootout"} & set(command)

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="launchd")
    def test_launchd_installed_file_does_not_imply_active(
        self, _platform: object, tmp_path: Path
    ) -> None:
        plist = tmp_path / "com.immich-memories.auto.plist"
        plist.write_text("<plist/>")

        with (
            patch(
                "immich_memories.automation.system_scheduler._launchd_plist_path",
                return_value=plist,
            ),
            patch("immich_memories.automation.system_scheduler.subprocess.run") as run,
        ):
            run.return_value.returncode = 113
            status = get_scheduler_status()

        assert status.installed is True
        assert status.active is False

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="systemd")
    def test_systemd_checks_timer_file_and_active_state(
        self, _platform: object, tmp_path: Path
    ) -> None:
        (tmp_path / "immich-memories-auto.service").write_text("[Service]")
        (tmp_path / "immich-memories-auto.timer").write_text("[Timer]")

        with (
            patch(
                "immich_memories.automation.system_scheduler._systemd_user_dir",
                return_value=tmp_path,
            ),
            patch("immich_memories.automation.system_scheduler.subprocess.run") as run,
        ):
            run.return_value.returncode = 3
            status = get_scheduler_status()

        assert status.installed is True
        assert status.active is False
        assert status.paths == (
            tmp_path / "immich-memories-auto.service",
            tmp_path / "immich-memories-auto.timer",
        )
        assert run.call_args.args[0] == [
            "systemctl",
            "--user",
            "is-active",
            "--quiet",
            "immich-memories-auto.timer",
        ]

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_crontab_detection_reads_but_does_not_edit_entries(self, _platform: object) -> None:
        with patch("immich_memories.automation.system_scheduler.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = (
                "0 9 * * * /usr/bin/immich-memories auto run --quiet --cooldown 24\n"
            )
            status = get_scheduler_status()

        assert status.platform == "crontab"
        assert status.installed is True
        assert status.active is None
        assert status.paths == ()
        assert run.call_args.args[0] == ["crontab", "-l"]

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_commented_crontab_entry_is_not_installed(self, _platform: object) -> None:
        with patch("immich_memories.automation.system_scheduler.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "\n  # 0 9 * * * /usr/bin/immich-memories auto run --quiet\n"
            status = get_scheduler_status()

        assert status.installed is False

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_known_missing_crontab_is_not_installed(self, _platform: object) -> None:
        with patch("immich_memories.automation.system_scheduler.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "no crontab for test-user\n"
            status = get_scheduler_status()

        assert status.installed is False

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_crontab_unreadable_installation_is_unknown(self, _platform: object) -> None:
        with patch(
            "immich_memories.automation.system_scheduler.subprocess.run",
            side_effect=PermissionError,
        ):
            status = get_scheduler_status()

        assert status.installed is None
        assert status.active is None

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_crontab_timeout_is_unknown(self, _platform: object) -> None:
        with patch(
            "immich_memories.automation.system_scheduler.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["crontab", "-l"], 5),
        ):
            status = get_scheduler_status()

        assert status.installed is None

    @patch("immich_memories.automation.system_scheduler.detect_platform", return_value="crontab")
    def test_crontab_unrecognized_error_is_unknown(self, _platform: object) -> None:
        with patch("immich_memories.automation.system_scheduler.subprocess.run") as run:
            run.return_value.returncode = 2
            run.return_value.stdout = ""
            run.return_value.stderr = "permission denied"
            status = get_scheduler_status()

        assert status.installed is None
