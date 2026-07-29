"""Persistence adapters for servers, selected state, and legacy devices."""

from __future__ import annotations

from datetime import UTC, datetime

from .errors import GatewayError
from .models import Device, Server
from .paths import GatewayPaths
from .storage import atomic_write_json, read_json


class ServerRepository:
    """Own the validated server list stored on the Pi."""

    def __init__(self, paths: GatewayPaths) -> None:
        self.paths = paths

    def list(self) -> list[Server]:
        """Return all configured servers in persisted order."""
        document = read_json(self.paths.servers)
        if document.get("schema_version") != 1 or not isinstance(document.get("servers"), list):
            raise GatewayError("CONFIG_INVALID", "Server configuration has an unsupported schema")
        return [Server.from_dict(item) for item in document["servers"]]

    def initialize(self, server: Server) -> None:
        """Create the initial server document exactly once during migration."""
        if self.paths.servers.exists():
            return
        self.save([server])

    def save(self, servers: list[Server]) -> None:
        """Atomically persist a completely validated server list."""
        identifiers = [server.id for server in servers]
        if len(identifiers) != len(set(identifiers)):
            raise GatewayError("DUPLICATE_SERVER_ID", "Server IDs must be unique")
        atomic_write_json(
            self.paths.servers,
            {"schema_version": 1, "servers": [server.to_dict() for server in servers]},
        )


class StateRepository:
    """Persist the last successfully activated VPN server only after verification."""

    def __init__(self, paths: GatewayPaths) -> None:
        self.paths = paths

    def get_active_server_id(self) -> str | None:
        """Return the selected server ID or None before the first successful switch."""
        if not self.paths.current_server.exists():
            return None
        document = read_json(self.paths.current_server, missing_code="STATE_NOT_FOUND")
        value = document.get("server_id")
        return value if isinstance(value, str) else None

    def save_active_server(self, server_id: str) -> None:
        """Persist a verified selection with an explicit timestamp."""
        atomic_write_json(
            self.paths.current_server,
            {
                "schema_version": 1,
                "server_id": server_id,
                "switched_at": datetime.now(UTC).isoformat(),
                "verified": True,
            },
        )


class DeviceRepository:
    """Read the existing IP-keyed device file without changing its schema yet."""

    def __init__(self, paths: GatewayPaths) -> None:
        self.paths = paths

    def list(self) -> list[Device]:
        """Map legacy devices to stable IDs for the CLI and GUI."""
        document = read_json(self.paths.legacy_devices)
        return [Device.from_legacy(ip, data) for ip, data in document.items() if isinstance(data, dict)]

    def get(self, device_id: str) -> Device:
        """Resolve a stable GUI device ID to the persisted legacy device."""
        for device in self.list():
            if device.id == device_id:
                return device
        raise GatewayError("DEVICE_NOT_FOUND", "Device does not exist")
