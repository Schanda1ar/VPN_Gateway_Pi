"""Strict SSH transport and fixed-command client for the Pi gateway CLI."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from vpn_gateway.models import validate_identifier, validate_profile

WINDOWS_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
        self._validate_settings()
        self._verify_fingerprint()
        remote = "sudo -n " + shlex.quote(self.settings.cli_path)
        remote += " " + " ".join(shlex.quote(part) for part in command)
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
            remote,
        ]
        try:
            result = subprocess.run(
                args,
                input=json.dumps(stdin_json) if stdin_json is not None else None,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds + 5,
                check=False,
                creationflags=WINDOWS_NO_CONSOLE,
            )
        except subprocess.TimeoutExpired as error:
            raise RemoteGatewayError("REMOTE_TIMEOUT", "The Raspberry Pi did not respond in time") from error
        except OSError as error:
            raise RemoteGatewayError("SSH_CONNECTION_FAILED", "OpenSSH could not be started") from error
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            code = "REMOTE_COMMAND_FAILED" if result.returncode else "REMOTE_RESPONSE_INVALID"
            raise RemoteGatewayError(code, "The Pi did not return valid JSON", {"stderr": result.stderr.strip()}) from error
        if not isinstance(payload, dict) or "success" not in payload:
            raise RemoteGatewayError("REMOTE_RESPONSE_INVALID", "The Pi response has no success flag")
        if result.returncode != 0 or not payload["success"]:
            raise RemoteGatewayError(payload.get("error_code", "REMOTE_COMMAND_FAILED"), payload.get("message", "Remote command failed"), payload.get("details"))
        return payload

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
