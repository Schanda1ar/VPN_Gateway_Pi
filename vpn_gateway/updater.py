"""Pi-side signed release updater with atomic activation and rollback."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import GatewayError
from .paths import GatewayPaths
from .release import (
    FixedReleaseClient,
    ReleaseManifest,
    ReleaseValidationError,
    safe_extract_archive,
    verify_manifest_signature,
    version_is_newer,
)


class UpdateError(GatewayError):
    """Structured failure from a release check, activation, or rollback."""


@dataclass(frozen=True)
class UpdateResult:
    """Stable JSON-compatible update status."""

    installed_version: str | None
    available_version: str | None
    api_version: int
    status: str

    def to_dict(self) -> dict[str, object]:
        """Return the CLI response payload."""
        return {
            "installed_version": self.installed_version,
            "available_version": self.available_version,
            "api_version": self.api_version,
            "status": self.status,
        }


class PiUpdater:
    """Download, verify, install, and atomically activate Pi releases."""

    def __init__(
        self,
        paths: GatewayPaths | None = None,
        release_client: FixedReleaseClient | None = None,
        *,
        api_version: int = 1,
        public_key: bytes | None = None,
        service_name: str = "vpn-gateway.service",
        health_checker: Callable[[Path], bool] | None = None,
    ) -> None:
        self.paths = paths or GatewayPaths.from_environment()
        self.release_client = release_client or FixedReleaseClient()
        self.api_version = api_version
        self.public_key = public_key
        self.service_name = service_name
        self.health_checker = health_checker

    def _manifest(self, requested_version: str | None = None) -> ReleaseManifest:
        try:
            raw, signature = (
                self.release_client.fetch_versioned_manifest(requested_version)
                if requested_version
                else self.release_client.fetch_manifest()
            )
            verify_manifest_signature(raw, signature, self.public_key)
            manifest = ReleaseManifest.from_dict(raw)
            manifest.ensure_compatible(self.api_version)
            return manifest
        except ReleaseValidationError as error:
            raise UpdateError(error.code, str(error)) from error
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise UpdateError("RELEASE_INVALID", "Release manifest could not be read") from error

    def installed_version(self) -> str | None:
        """Read the active version from the current symlink without touching data."""
        current = self.paths.current_release
        if not current.exists():
            return None
        version_file = current / "version.json"
        if version_file.exists():
            try:
                payload = json.loads(version_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("version"), str):
                    return payload["version"]
            except (OSError, json.JSONDecodeError):
                pass
        return current.resolve().name

    def check(self) -> dict[str, object]:
        """Return signed release availability and API compatibility status."""
        manifest = self._manifest()
        installed = self.installed_version()
        available = manifest.version if version_is_newer(manifest.version, installed) else None
        status = "available" if available else "current"
        return UpdateResult(installed, available, self.api_version, status).to_dict()

    def apply(self, version: str) -> dict[str, object]:
        """Install one exact signed version and roll back on any health failure."""
        manifest = self._manifest(version)
        if manifest.version != version:
            raise UpdateError("VERSION_MISMATCH", "Requested version does not match the signed manifest")
        installed = self.installed_version()
        if installed == version:
            return UpdateResult(installed, None, self.api_version, "current").to_dict()

        self.paths.releases_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=self.paths.releases_dir))
        previous_target = self.paths.current_release.resolve() if self.paths.current_release.exists() else None
        target = self.paths.releases_dir / version
        target_created = False
        completed = False
        try:
            self._stage(manifest, staging)
            if target.exists() or target.is_symlink():
                raise UpdateError("VERSION_EXISTS", "The target release directory already exists")
            os.replace(staging, target)
            target_created = True
            self._activate(target)
            if self.health_checker is None:
                restart = subprocess.run(
                    ["systemctl", "restart", self.service_name],
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
                if restart.returncode != 0:
                    raise UpdateError("SERVICE_RESTART_FAILED", "The gateway service could not be restarted")
            if not self._healthy(target):
                raise UpdateError("HEALTH_CHECK_FAILED", "The new release did not pass its health check")
            completed = True
            return UpdateResult(version, None, self.api_version, "updated").to_dict()
        except UpdateError as error:
            self._rollback(previous_target)
            if target_created:
                raise UpdateError("UPDATE_ROLLED_BACK", str(error)) from error
            raise
        except ReleaseValidationError as error:
            self._rollback(previous_target)
            raise UpdateError("UPDATE_ROLLED_BACK", str(error)) from error
        except (OSError, subprocess.SubprocessError) as error:
            self._rollback(previous_target)
            raise UpdateError("UPDATE_ROLLED_BACK", "Release activation failed and was rolled back") from error
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if not completed and target_created and target.exists():
                current_link = self.paths.current_release
                active_target = current_link.resolve() if current_link.exists() or current_link.is_symlink() else None
                if active_target != target.resolve():
                    shutil.rmtree(target, ignore_errors=True)

    def _stage(self, manifest: ReleaseManifest, staging: Path) -> None:
        """Download and verify all files before making the release visible."""
        staging.mkdir(parents=True, exist_ok=True)
        wheel = self._artifact(manifest, "pi_wheel", "pi-wheel", "wheel")
        runtime = self._artifact(manifest, "pi_runtime", "pi-runtime", "runtime")
        wheel_path = staging / wheel.filename
        runtime_path = staging / runtime.filename
        self.release_client.download_artifact(wheel, wheel_path)
        self.release_client.download_artifact(runtime, runtime_path)
        runtime_dir = staging / "runtime"
        extracted = safe_extract_archive(runtime_path, runtime_dir)
        candidates = [path for path in runtime_dir.rglob("main.py") if path.is_file()]
        if len(candidates) != 1:
            raise UpdateError("RUNTIME_INVALID", "The signed Pi runtime must contain exactly one main.py")
        canonical_runtime = runtime_dir / "main.py"
        if candidates[0] != canonical_runtime:
            canonical_runtime.write_bytes(candidates[0].read_bytes())
        (staging / "version.json").write_text(json.dumps({"version": manifest.version}) + "\n", encoding="utf-8")
        runtime_wheels = [path for path in extracted if path.suffix == ".whl"]
        self._install_wheel(wheel_path, staging / "venv", runtime_wheels)

    @staticmethod
    def _artifact(manifest: ReleaseManifest, *names: str):
        for name in names:
            if name in manifest.artifacts:
                return manifest.artifacts[name]
        raise UpdateError("ARTIFACT_MISSING", f"Manifest is missing one of: {', '.join(names)}")

    def _install_wheel(self, wheel: Path, environment: Path, runtime_wheels: list[Path] | None = None) -> None:
        """Install only a previously hashed local wheel into a private venv."""
        builder = venv.EnvBuilder(with_pip=True, clear=False)
        builder.create(environment)
        pip = environment / "bin" / "pip"
        if not pip.exists():
            pip = environment / "Scripts" / "pip.exe"
        if not pip.exists():
            raise UpdateError("INSTALL_FAILED", "The release environment has no pip executable")
        wheels = [wheel, *(runtime_wheels or [])]
        result = subprocess.run(
            [str(pip), "install", "--no-index", "--no-deps", "--disable-pip-version-check", *(str(item) for item in wheels)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise UpdateError("INSTALL_FAILED", "The verified local wheel could not be installed")

    def _activate(self, target: Path) -> None:
        """Atomically point ``current`` at one fully staged release."""
        link = self.paths.releases_dir / f".current-{os.getpid()}"
        link.unlink(missing_ok=True)
        link.symlink_to(target.name, target_is_directory=True)
        os.replace(link, self.paths.current_release)

    def _healthy(self, target: Path) -> bool:
        """Check systemd and the versioned CLI before considering activation complete."""
        if self.health_checker is not None:
            return bool(self.health_checker(target))
        service = subprocess.run(
            ["systemctl", "is-active", "--quiet", self.service_name],
            capture_output=True,
            check=False,
            timeout=30,
        )
        cli = target / "venv" / "bin" / "vpn-gateway-cli"
        if not cli.exists():
            cli = target / "venv" / "Scripts" / "vpn-gateway-cli.exe"
        if not cli.exists():
            return False
        version = subprocess.run([str(cli), "version", "--json"], capture_output=True, text=True, check=False, timeout=30)
        try:
            payload = json.loads(version.stdout)
        except json.JSONDecodeError:
            return False
        return service.returncode == 0 and version.returncode == 0 and payload.get("success") is True

    def _rollback(self, previous_target: Path | None) -> None:
        """Restore the previous symlink and restart its service state."""
        if previous_target is not None and previous_target.exists():
            try:
                self._activate(previous_target)
                subprocess.run(["systemctl", "restart", self.service_name], capture_output=True, check=False, timeout=30)
            except OSError:
                pass
        elif self.paths.current_release.exists():
            try:
                if self.paths.current_release.is_symlink():
                    self.paths.current_release.unlink()
                else:
                    shutil.rmtree(self.paths.current_release)
            except OSError:
                pass
