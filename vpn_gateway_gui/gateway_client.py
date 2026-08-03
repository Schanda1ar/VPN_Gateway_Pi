"""Strict SSH transport and fixed-command client for the Pi gateway CLI."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from vpn_gateway import __version__ as BUNDLED_APPLICATION_VERSION
from vpn_gateway.models import validate_identifier, validate_profile

WINDOWS_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SSH_PROBE_MARKER = "VPN_GATEWAY_SSH_OK"
SSH_PROBE_COMMAND = f"printf '%s\\n' {SSH_PROBE_MARKER}"
PASSWORDLESS_SUDO_CHECK = "sudo -n /bin/true"
REMOTE_UPLOAD_COMMAND = """set -eu
upload_dir="$(mktemp -d /tmp/vpn-gateway-gui.XXXXXX)"
chmod 0700 "$upload_dir"
cat > "$upload_dir/bundle.tar.gz"
printf '%s\\n' "$upload_dir"
"""
REMOTE_UPLOAD_PATTERN = re.compile(r"^/tmp/vpn-gateway-gui\.[A-Za-z0-9]+$")


class RemoteGatewayError(RuntimeError):
    """Structured failure returned by the Pi CLI or SSH transport."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class SshSettings:
    """User-local SSH connection settings; no gateway state is stored here."""

    host: str = ""
    port: int = 22
    username: str = "pi"
    private_key_path: str = ""
    known_hosts_path: str = str(Path.home() / ".ssh" / "known_hosts")
    host_key_fingerprint: str = ""
    timeout_seconds: int = 15
    cli_path: str = "/usr/local/bin/vpn-gateway-cli"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SshSettings":
        """Load tolerant local settings while retaining secure defaults."""
        allowed = {field: value[field] for field in cls.__dataclass_fields__ if field in value}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe local settings."""
        return asdict(self)


class SettingsService:
    """Persist GUI-local connection settings independently from the gateway."""

    def __init__(self) -> None:
        base = Path(os.environ.get("APPDATA", Path.home())) / "VpnGatewayManager"
        self.path = base / "settings.json"

    def load(self) -> SshSettings:
        """Load settings or return an empty secure configuration."""
        if not self.path.exists():
            return SshSettings()
        try:
            return SshSettings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return SshSettings()

    def save(self, settings: SshSettings) -> None:
        """Write connection settings atomically in the user profile."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


class SshClient:
    """Use the system OpenSSH client with strict host-key verification."""

    def __init__(self, settings: SshSettings) -> None:
        self.settings = settings

    def execute(self, command: list[str], stdin_json: dict | None = None) -> dict:
        """Execute a fixed CLI command and validate exactly one JSON response."""
        remote = "sudo -n " + shlex.quote(self.settings.cli_path)
        remote += " " + " ".join(shlex.quote(part) for part in command)
        request = json.dumps(stdin_json).encode("utf-8") if stdin_json is not None else None
        result = self._run_ssh(remote, request, self.settings.timeout_seconds + 5)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode == 255:
            raise self._connection_error(stderr)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as error:
            code = "REMOTE_COMMAND_FAILED" if result.returncode else "REMOTE_RESPONSE_INVALID"
            raise RemoteGatewayError(code, "The Pi did not return valid JSON", {"stderr": stderr.strip()}) from error
        if not isinstance(payload, dict) or "success" not in payload:
            raise RemoteGatewayError("REMOTE_RESPONSE_INVALID", "The Pi response has no success flag")
        if result.returncode != 0 or not payload["success"]:
            raise RemoteGatewayError(payload.get("error_code", "REMOTE_COMMAND_FAILED"), payload.get("message", "Remote command failed"), payload.get("details"))
        return payload

    def probe(self) -> dict:
        """Verify SSH authentication and host identity without requiring an installed Pi CLI."""
        result = self._run_ssh(SSH_PROBE_COMMAND, None, self.settings.timeout_seconds + 5)
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode != 0:
            raise self._connection_error(stderr)
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if stdout != SSH_PROBE_MARKER:
            raise RemoteGatewayError("REMOTE_RESPONSE_INVALID", "The SSH connection returned an unexpected response")
        return {"success": True, "connected": True, "host": self.settings.host}

    def install_gateway(self, bundle: bytes, sudo_password: str | None = None) -> dict:
        """Upload the fixed bundle and install it with passwordless or transient sudo."""
        if sudo_password is not None and any(character in sudo_password for character in ("\n", "\r", "\0")):
            raise RemoteGatewayError("SUDO_PASSWORD_INVALID", "Das sudo-Passwort enthält ungültige Steuerzeichen")
        install_timeout = max(300, self.settings.timeout_seconds + 5)
        if sudo_password is None:
            sudo_check = self._run_ssh(PASSWORDLESS_SUDO_CHECK, None, self.settings.timeout_seconds + 5)
            sudo_stderr = sudo_check.stderr.decode("utf-8", errors="replace")
            if sudo_check.returncode == 255:
                raise self._connection_error(sudo_stderr)
            if sudo_check.returncode != 0:
                raise RemoteGatewayError(
                    "SUDO_PASSWORD_REQUIRED",
                    "Für die CLI-Installation wird einmalig das sudo-Passwort des Pi-Benutzers benötigt",
                )

        upload = self._run_ssh(REMOTE_UPLOAD_COMMAND, bundle, install_timeout)
        upload_stderr = upload.stderr.decode("utf-8", errors="replace")
        if upload.returncode == 255:
            raise self._connection_error(upload_stderr)
        remote_directory = upload.stdout.decode("utf-8", errors="replace").strip()
        if upload.returncode != 0 or not REMOTE_UPLOAD_PATTERN.fullmatch(remote_directory):
            raise RemoteGatewayError(
                "REMOTE_INSTALL_UPLOAD_FAILED",
                "Das Installationspaket konnte nicht sicher auf den Pi übertragen werden",
                {"stderr": upload_stderr[-4000:]},
            )

        try:
            root_script = self._root_install_script(remote_directory)
            sudo_mode = "-S -p ''" if sudo_password is not None else "-n"
            remote_command = f"sudo {sudo_mode} /bin/sh -c {shlex.quote(root_script)}"
            password_input = f"{sudo_password}\n".encode("utf-8") if sudo_password is not None else None
            result = self._run_ssh(remote_command, password_input, install_timeout)
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if result.returncode == 255:
                raise self._connection_error(stderr)
            if result.returncode != 0:
                lowered = stderr.casefold()
                authentication_markers = ("incorrect password", "sorry, try again", "authentifizierungsfehler", "falsches passwort")
                if sudo_password is not None and any(marker in lowered for marker in authentication_markers):
                    raise RemoteGatewayError(
                        "SUDO_AUTHENTICATION_FAILED",
                        "Das eingegebene sudo-Passwort wurde vom Pi abgelehnt",
                    )
                raise RemoteGatewayError(
                    "REMOTE_INSTALL_FAILED",
                    "Die CLI- und Gateway-Installation auf dem Pi ist fehlgeschlagen",
                    {"stderr": stderr[-4000:], "stdout": stdout[-4000:]},
                )
        finally:
            self._cleanup_remote_upload(remote_directory)
        version = self.execute(["version"])
        return {
            "success": True,
            "installed": True,
            "api_version": version["api_version"],
            "application_version": version["application_version"],
        }

    @staticmethod
    def _root_install_script(remote_directory: str) -> str:
        """Build the fixed root script around one validated temporary directory."""
        archive = shlex.quote(f"{remote_directory}/bundle.tar.gz")
        return (
            "set -eu\n"
            'staging="$(mktemp -d /tmp/vpn-gateway-root.XXXXXX)"\n'
            'trap \'rm -rf "$staging"\' EXIT HUP INT TERM\n'
            f'tar -xzf {archive} -C "$staging"\n'
            '/bin/sh "$staging/pi/scripts/install.sh" --activate --harden-sudo\n'
        )

    def _cleanup_remote_upload(self, remote_directory: str) -> None:
        """Best-effort removal of the unprivileged temporary upload directory."""
        try:
            self._run_ssh(
                f"rm -rf -- {shlex.quote(remote_directory)}",
                None,
                self.settings.timeout_seconds + 5,
            )
        except RemoteGatewayError:
            pass

    def _run_ssh(self, remote_command: str, input_data: bytes | None, timeout: int) -> subprocess.CompletedProcess[bytes]:
        """Run one internally defined SSH command with binary-safe stdin."""
        self._validate_settings()
        self._verify_fingerprint()
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.settings.known_hosts_path}",
            "-o",
            f"ConnectTimeout={self.settings.timeout_seconds}",
            "-p",
            str(self.settings.port),
            "-i",
            self.settings.private_key_path,
            f"{self.settings.username}@{self.settings.host}",
            remote_command,
        ]
        try:
            return subprocess.run(
                args,
                input=input_data,
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=WINDOWS_NO_CONSOLE,
            )
        except subprocess.TimeoutExpired as error:
            raise RemoteGatewayError("REMOTE_TIMEOUT", "The Raspberry Pi did not respond in time") from error
        except OSError as error:
            raise RemoteGatewayError("SSH_CONNECTION_FAILED", "OpenSSH could not be started") from error

    @staticmethod
    def _connection_error(stderr: str) -> RemoteGatewayError:
        """Map common OpenSSH failures to the stable GUI error surface."""
        lowered = stderr.casefold()
        if "host key verification failed" in lowered or "remote host identification has changed" in lowered:
            return RemoteGatewayError("SSH_HOST_KEY_MISMATCH", "The Raspberry Pi host key could not be verified")
        if "permission denied" in lowered:
            return RemoteGatewayError("SSH_AUTHENTICATION_FAILED", "SSH authentication failed")
        return RemoteGatewayError("SSH_CONNECTION_FAILED", "Could not connect to the Raspberry Pi", {"stderr": stderr.strip()})

    def _validate_settings(self) -> None:
        if not self.settings.host or not self.settings.private_key_path:
            raise RemoteGatewayError("SSH_CONNECTION_FAILED", "Host and private key path are required")
        if not Path(self.settings.known_hosts_path).exists():
            raise RemoteGatewayError("SSH_HOST_KEY_MISMATCH", "Configured known_hosts file does not exist")
        if not self.settings.host_key_fingerprint.startswith("SHA256:"):
            raise RemoteGatewayError("SSH_HOST_KEY_MISMATCH", "A SHA256 host-key fingerprint is required")

    def _verify_fingerprint(self) -> None:
        lookup_host = self.settings.host if self.settings.port == 22 else f"[{self.settings.host}]:{self.settings.port}"
        result = subprocess.run(
            ["ssh-keygen", "-l", "-F", lookup_host, "-f", self.settings.known_hosts_path],
            capture_output=True,
            text=True,
            check=False,
            creationflags=WINDOWS_NO_CONSOLE,
        )
        if result.returncode != 0 or self.settings.host_key_fingerprint not in result.stdout:
            raise RemoteGatewayError("SSH_HOST_KEY_MISMATCH", "The configured host key fingerprint was not found")


class GatewayClient:
    """Only public application API used by GUI views and controllers."""

    def __init__(self, settings: SshSettings) -> None:
        self.ssh = SshClient(settings)

    def get_version(self) -> dict:
        """Verify SSH and CLI protocol compatibility."""
        return self.ssh.execute(["version"])

    def probe_connection(self) -> dict:
        """Verify SSH before any gateway software is expected to exist."""
        return self.ssh.probe()

    def install_gateway(self, sudo_password: str | None = None) -> dict:
        """Install/update the bundled CLI, then configure the complete gateway."""
        from .installer import build_install_bundle

        try:
            remote_version = self.ssh.execute(["version"])
        except RemoteGatewayError as error:
            if error.code not in {"REMOTE_COMMAND_FAILED", "REMOTE_RESPONSE_INVALID"}:
                raise
        else:
            remote_application_version = str(remote_version.get("application_version", ""))
            remote_api_version = int(remote_version.get("api_version", 0))
            if remote_api_version >= 2 and _version_key(remote_application_version) >= _version_key(BUNDLED_APPLICATION_VERSION):
                return self.ssh.execute(["system", "setup"])
        return self.ssh.install_gateway(build_install_bundle(), sudo_password)

    def get_status(self) -> dict:
        """Fetch dashboard state."""
        return self.ssh.execute(["status"])

    def list_servers(self) -> dict:
        """Fetch Pi-owned VPN server data."""
        return self.ssh.execute(["server", "list"])

    def add_server(self, payload: dict) -> dict:
        """Add a server with request data sent only through stdin."""
        return self.ssh.execute(["server", "add"], payload)

    def update_server(self, server_id: str, payload: dict) -> dict:
        """Update a validated existing server."""
        return self.ssh.execute(["server", "update", "--server-id", validate_identifier(server_id, "server_id")], payload)

    def delete_server(self, server_id: str) -> dict:
        """Delete a validated non-active server."""
        return self.ssh.execute(["server", "delete", "--server-id", validate_identifier(server_id, "server_id")])

    def switch_server(self, server_id: str) -> dict:
        """Activate a known server through the rollback-protected Pi service."""
        return self.ssh.execute(["vpn", "switch", "--server-id", validate_identifier(server_id, "server_id")])

    def list_devices(self) -> dict:
        """Fetch configured devices and their requested profiles."""
        return self.ssh.execute(["device", "list"])

    def set_device_profile(self, device_id: str, profile: str) -> dict:
        """Apply a supported profile without exposing firewall commands."""
        return self.ssh.execute(["device", "set-profile", "--device-id", validate_identifier(device_id, "device_id"), "--profile", validate_profile(profile)])

    def get_effective_rules(self, device_id: str) -> dict:
        """Fetch read-only effective-rule diagnostics for one device."""
        return self.ssh.execute(["diagnostics", "rules", "--device-id", validate_identifier(device_id, "device_id")])


def _version_key(value: str) -> tuple[int, int, int]:
    """Return a conservative numeric key for the project's semantic versions."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)
