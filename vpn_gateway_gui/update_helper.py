"""Small Windows-side helper for atomic GUI folder swaps and rollback."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from vpn_gateway.release import parse_semver, safe_extract_archive, verify_sha256


class GuiUpdateError(RuntimeError):
    """Raised when the helper cannot activate or health-check a GUI release."""


@dataclass(frozen=True)
class UpdateHelperContract:
    """Fixed argument contract shared by the GUI and the helper process."""

    package: Path
    version: str
    install_root: Path
    executable: str
    sha256: str
    health_file: Path
    parent_pid: int | None = None

    def argv(self) -> list[str]:
        """Return only the fixed helper argv; no URL or shell text is accepted."""
        parse_semver(self.version)
        result = [
            "--package", str(self.package),
            "--version", self.version,
            "--install-root", str(self.install_root),
            "--executable", self.executable,
            "--sha256", self.sha256,
        ]
        if self.parent_pid is not None:
            result += ["--parent-pid", str(self.parent_pid)]
        result += ["--health-file", str(self.health_file)]
        return result


class WindowsUpdateHelper:
    """Wait for the GUI to exit, switch its version directory, and recover it."""

    def __init__(
        self,
        *,
        launcher: Callable[[Path, str], object] | None = None,
        health_checker: Callable[[Path], bool] | None = None,
        wait_seconds: float = 30.0,
    ) -> None:
        self.launcher = launcher or self._launch
        self.health_checker = health_checker
        self.wait_seconds = wait_seconds
        self._active_health_file: Path | None = None

    def install(
        self,
        package: Path,
        version: str,
        install_root: Path,
        executable: str,
        *,
        expected_sha256: str | None = None,
        parent_pid: int | None = None,
        health_file: Path | None = None,
    ) -> Path:
        """Install a verified ZIP and return the active GUI directory."""
        parse_semver(version)
        if not expected_sha256:
            raise GuiUpdateError("The GUI artifact digest is required")
        if health_file is None:
            raise GuiUpdateError("The GUI startup health file is required")
        package = package.resolve()
        install_root = install_root.resolve()
        if parent_pid is not None:
            self._wait_for_exit(parent_pid)

        versions = install_root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        health_file = health_file.resolve()
        health_file.unlink(missing_ok=True)
        staging = Path(tempfile_name(versions, version))
        target = versions / version
        current = install_root / "current"
        backup = install_root / f".previous-{version}"
        package_copy = versions / f".{version}.package"
        had_current = current.exists()
        backup_created = False
        try:
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            package_copy.unlink(missing_ok=True)
            shutil.copyfile(package, package_copy)
            verify_sha256(package_copy, expected_sha256)
            safe_extract_archive(package_copy, staging)
            package_copy.unlink(missing_ok=True)
            if target.exists():
                raise GuiUpdateError("The GUI target version already exists")
            os.replace(staging, target)
            if backup.exists():
                shutil.rmtree(backup)
            if current.exists():
                os.replace(current, backup)
                backup_created = True
            os.replace(target, current)
            self._active_health_file = health_file
            process = self.launcher(current, executable)
            if not self._healthy(current, process, health_file):
                raise GuiUpdateError("The new GUI did not send its startup health signal")
            if backup_created and backup.exists():
                shutil.rmtree(backup)
            return current
        except Exception as error:
            if backup_created and backup.exists():
                if current.exists() or current.is_symlink():
                    if current.is_symlink():
                        current.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(current, ignore_errors=True)
                os.replace(backup, current)
            elif not had_current and current.exists():
                if current.is_symlink():
                    current.unlink(missing_ok=True)
                else:
                    shutil.rmtree(current, ignore_errors=True)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            package_copy.unlink(missing_ok=True)
            if isinstance(error, GuiUpdateError):
                raise GuiUpdateError(f"GUI update rolled back: {error}") from error
            raise GuiUpdateError("GUI update failed and was rolled back") from error

    def _wait_for_exit(self, pid: int) -> None:
        """Wait for the old GUI process without invoking a shell."""
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        raise GuiUpdateError("The previous GUI did not exit in time")

    def _healthy(self, current: Path, process: object, health_file: Path) -> bool:
        if self.health_checker is not None:
            return bool(self.health_checker(current))
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if health_file.exists() and health_file.read_text(encoding="utf-8").strip() == "ready":
                return True
            time.sleep(0.1)
        return False

    def _launch(self, current: Path, executable: str) -> subprocess.Popen:
        """Launch the fixed executable from the newly active directory."""
        executable_path = (current / executable).resolve()
        if current.resolve() not in executable_path.parents:
            raise GuiUpdateError("GUI executable escapes the active release")
        environment = os.environ.copy()
        if self._active_health_file is not None:
            environment["VPN_GATEWAY_GUI_HEALTH_FILE"] = str(self._active_health_file)
        return subprocess.Popen([str(executable_path)], cwd=current, close_fds=True, env=environment)


def tempfile_name(parent: Path, version: str) -> Path:
    """Return a validated staging name without exposing arbitrary paths."""
    parse_semver(version)
    return parent / f".{version}.staging"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vpn-gateway-gui-update-helper")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--health-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one fixed helper operation for the GUI hand-off contract."""
    arguments = _build_parser().parse_args(argv)
    WindowsUpdateHelper().install(
        arguments.package,
        arguments.version,
        arguments.install_root,
        arguments.executable,
        expected_sha256=arguments.sha256,
        parent_pid=arguments.parent_pid,
        health_file=arguments.health_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
