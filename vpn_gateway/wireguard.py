"""Runtime WireGuard peer switching with health checks and rollback support."""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from .command_runner import CommandRunner
from .errors import GatewayError
from .models import Server


class WireGuardClient:
    """Read and atomically apply the running configuration of one WireGuard interface."""

    def __init__(self, runner: CommandRunner | None = None, interface: str = "wg0") -> None:
        self.runner = runner or CommandRunner()
        self.interface = interface

    def capture_runtime_config(self) -> str:
        """Capture the complete runtime configuration without logging sensitive contents."""
        self._ensure_interface_up()
        return self.runner.run(["wg", "showconf", self.interface]).stdout

    def apply_server(self, server: Server, current_config: str) -> None:
        """Replace the single existing peer's endpoint and public key via syncconf."""
        target_config = self._render_target_config(current_config, server)
        self._sync_config(target_config)

    def restore_runtime_config(self, config: str) -> None:
        """Restore a captured runtime configuration after a failed switch."""
        self._sync_config(config)

    def latest_handshake_epoch(self) -> int:
        """Return the newest peer handshake timestamp, or zero when none exists."""
        result = self.runner.run(["wg", "show", self.interface, "latest-handshakes"])
        timestamps: list[int] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                timestamps.append(int(parts[1]))
        return max(timestamps, default=0)

    def is_server_active(self, server: Server) -> bool:
        """Check whether the running single peer already matches a selected server."""
        result = self.runner.run(["wg", "show", self.interface, "endpoints"])
        peers = [line.split() for line in result.stdout.splitlines() if line.split()]
        return len(peers) == 1 and len(peers[0]) == 2 and peers[0][0] == server.public_key and peers[0][1] == server.endpoint

    def send_test_traffic(self) -> None:
        """Trigger traffic over the tunnel without making its success a sole health signal."""
        self.runner.run(["ping", "-I", self.interface, "-c", "1", "-W", "2", "1.1.1.1"], timeout=5, check=False)

    def _ensure_interface_up(self) -> None:
        interfaces = self.runner.run(["wg", "show", "interfaces"]).stdout.split()
        if self.interface not in interfaces:
            raise GatewayError("WG_INTERFACE_NOT_FOUND", f"WireGuard interface {self.interface} is not active")

    def _render_target_config(self, current_config: str, server: Server) -> str:
        peer_sections = re.findall(r"(?ms)^\[Peer\]\n.*?(?=^\[Peer\]\n|\Z)", current_config)
        if len(peer_sections) != 1:
            raise GatewayError("CONFIG_INVALID", "Exactly one active WireGuard peer is required for switching")
        peer = peer_sections[0]
        peer = re.sub(r"(?m)^PublicKey\s*=.*$", f"PublicKey = {server.public_key}", peer)
        peer = re.sub(r"(?m)^Endpoint\s*=.*$", f"Endpoint = {server.endpoint}", peer)
        allowed_ips = ", ".join(server.allowed_ips)
        peer = re.sub(r"(?m)^AllowedIPs\s*=.*$", f"AllowedIPs = {allowed_ips}", peer)
        if server.persistent_keepalive is None:
            peer = re.sub(r"(?m)^PersistentKeepalive\s*=.*\n?", "", peer)
        elif re.search(r"(?m)^PersistentKeepalive\s*=", peer):
            peer = re.sub(r"(?m)^PersistentKeepalive\s*=.*$", f"PersistentKeepalive = {server.persistent_keepalive}", peer)
        else:
            peer = peer.rstrip() + f"\nPersistentKeepalive = {server.persistent_keepalive}\n"
        return current_config.replace(peer_sections[0], peer)

    def _sync_config(self, config: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(config)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            self.runner.run(["wg", "syncconf", self.interface, str(temporary)], timeout=15)
        except GatewayError as error:
            raise GatewayError("WG_APPLY_FAILED", "WireGuard configuration could not be applied") from error
        finally:
            temporary.unlink(missing_ok=True)


class HealthChecker:
    """Verify a fresh WireGuard handshake after a peer switch or rollback."""

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def verify(self, wireguard: WireGuardClient) -> dict:
        """Require a handshake that happened after health verification began."""
        started_at = int(time.time())
        wireguard.send_test_traffic()
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            handshake = wireguard.latest_handshake_epoch()
            if handshake >= started_at:
                return {"latest_handshake_epoch": handshake, "timeout_seconds": self.timeout_seconds}
            time.sleep(1)
        raise GatewayError("HANDSHAKE_TIMEOUT", "WireGuard did not establish a fresh handshake")

    def verify_existing(self, wireguard: WireGuardClient, maximum_age_seconds: int = 300) -> dict:
        """Accept a recently active tunnel during one-time migration without switching it."""
        handshake = wireguard.latest_handshake_epoch()
        age = int(time.time()) - handshake if handshake else None
        if age is None or age < 0 or age > maximum_age_seconds:
            raise GatewayError("TUNNEL_HEALTHCHECK_FAILED", "The existing WireGuard tunnel has no recent handshake")
        return {"latest_handshake_epoch": handshake, "handshake_age_seconds": age, "maximum_age_seconds": maximum_age_seconds}
