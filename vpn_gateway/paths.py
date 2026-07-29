"""Centralized filesystem locations for gateway state and legacy configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GatewayPaths:
    """Locations configurable for tests while retaining safe Pi defaults."""

    legacy_dir: Path
    config_dir: Path
    state_dir: Path
    lock_dir: Path

    @classmethod
    def from_environment(cls) -> "GatewayPaths":
        """Create paths from explicit overrides or the production defaults."""
        repository_root = Path(__file__).resolve().parent.parent
        legacy_default = repository_root if (repository_root / "main.py").exists() else Path("/home/pi/vpn-gateway")
        return cls(
            legacy_dir=Path(os.environ.get("VPN_GATEWAY_LEGACY_DIR", legacy_default)),
            config_dir=Path(os.environ.get("VPN_GATEWAY_CONFIG_DIR", "/opt/vpn-gateway/config")),
            state_dir=Path(os.environ.get("VPN_GATEWAY_STATE_DIR", "/opt/vpn-gateway/state")),
            lock_dir=Path(os.environ.get("VPN_GATEWAY_LOCK_DIR", "/run/lock")),
        )

    @property
    def legacy_config(self) -> Path:
        """Return the existing gateway base configuration path."""
        return self.legacy_dir / "config.json"

    @property
    def legacy_devices(self) -> Path:
        """Return the existing device-profile persistence path."""
        return self.legacy_dir / "devices.json"

    @property
    def servers(self) -> Path:
        """Return the managed server-list location."""
        return self.config_dir / "servers.json"

    @property
    def current_server(self) -> Path:
        """Return the selected-server state location."""
        return self.state_dir / "current_vpn_server.json"
