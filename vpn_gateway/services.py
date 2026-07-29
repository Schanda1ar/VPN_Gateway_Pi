"""Gateway application services coordinating repositories and system adapters."""

from __future__ import annotations

import json
from dataclasses import replace

from loguru import logger

from .errors import GatewayError
from .gateway_adapter import GatewayAdapter
from .locking import ProcessLock
from .models import Device, Server, validate_identifier, validate_profile
from .paths import GatewayPaths
from .repositories import DeviceRepository, ServerRepository, StateRepository
from .storage import atomic_write_json
from .wireguard import HealthChecker, WireGuardClient


class ServerManagementService:
    """Validate and persist server CRUD operations under a shared lock."""

    def __init__(self, paths: GatewayPaths, servers: ServerRepository, state: StateRepository) -> None:
        self.paths = paths
        self.servers = servers
        self.state = state

    def list(self) -> list[Server]:
        """List all configured VPN servers."""
        return self.servers.list()

    def add(self, payload: dict) -> Server:
        """Add a new validated server without touching WireGuard."""
        server = Server.from_dict(payload)
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-server-config.lock"):
            existing = self.servers.list()
            if any(item.id == server.id for item in existing):
                raise GatewayError("SERVER_ALREADY_EXISTS", "Server ID already exists")
            self.servers.save([*existing, server])
        return server

    def update(self, server_id: str, payload: dict) -> Server:
        """Update mutable server attributes while preserving its stable ID."""
        identifier = validate_identifier(server_id, "server_id")
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-server-config.lock"):
            existing = self.servers.list()
            for index, server in enumerate(existing):
                if server.id == identifier:
                    merged = server.to_dict() | payload
                    merged["id"] = identifier
                    updated = Server.from_dict(merged)
                    existing[index] = updated
                    self.servers.save(existing)
                    return updated
        raise GatewayError("SERVER_NOT_FOUND", "Server does not exist")

    def delete(self, server_id: str) -> None:
        """Delete only a non-active server while preserving at least one option."""
        identifier = validate_identifier(server_id, "server_id")
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-server-config.lock"):
            existing = self.servers.list()
            if not any(server.id == identifier for server in existing):
                raise GatewayError("SERVER_NOT_FOUND", "Server does not exist")
            if self.state.get_active_server_id() == identifier:
                raise GatewayError("SERVER_IN_USE", "The active server cannot be deleted")
            if len(existing) == 1:
                raise GatewayError("LAST_SERVER_DELETE_FORBIDDEN", "The last server cannot be deleted")
            self.servers.save([server for server in existing if server.id != identifier])


class DeviceProfileService:
    """Apply device profiles transactionally through the existing rule engine."""

    def __init__(self, paths: GatewayPaths, devices: DeviceRepository, adapter: GatewayAdapter) -> None:
        self.paths = paths
        self.devices = devices
        self.adapter = adapter

    def set_profile(self, device_id: str, profile: str) -> Device:
        """Persist and verify one profile change or restore the prior state."""
        target_profile = validate_profile(profile)
        device = self.devices.get(device_id)
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-device-config.lock"):
            snapshot = self.adapter.legacy_snapshot()
            try:
                self.adapter.apply_profile(device, target_profile)
                logger.info("Applied profile {} to device {}", target_profile, device.id)
                return replace(device, profile=target_profile)
            except Exception as error:
                logger.error("Profile apply failed for {}: {}", device.id, error)
                try:
                    self.adapter.restore_snapshot(snapshot)
                    self.adapter.restore_legacy_devices()
                except GatewayError:
                    raise
                except Exception as rollback_error:
                    raise GatewayError("DEVICE_ROLLBACK_FAILED", "Device rollback failed") from rollback_error
                raise GatewayError("PROFILE_APPLY_FAILED", "Device profile could not be applied") from error

    def restore(self) -> None:
        """Reapply every persisted legacy device profile after boot."""
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-rules-apply.lock"):
            try:
                self.adapter.restore_legacy_devices()
            except Exception as error:
                raise GatewayError("PROFILE_APPLY_FAILED", "Stored device profiles could not be restored") from error


class EffectiveRulesService:
    """Read the effective routing and firewall effects for one device."""

    def __init__(self, devices: DeviceRepository, runner) -> None:
        self.devices = devices
        self.runner = runner

    def get(self, device_id: str) -> dict:
        """Return expected effects and read-only system evidence for a device."""
        device = self.devices.get(device_id)
        ip_rules = self.runner.run(["ip", "rule", "show"]).stdout.splitlines()
        routes = self.runner.run(["ip", "route", "show", "table", "100"], check=False).stdout.splitlines()
        filter_rules = self.runner.run(["iptables", "-S"]).stdout.splitlines()
        nat_rules = self.runner.run(["iptables", "-t", "nat", "-S"]).stdout.splitlines()
        has_policy_rule = any(device.ip_address in line and "lookup 100" in line for line in ip_rules)
        has_dns_rule = any(device.ip_address in line and "--dport 53" in line for line in nat_rules)
        has_filter_rule = any(device.ip_address in line for line in filter_rules)
        expected_vpn = device.profile in ("VPN", "Sicher")
        expected_kill_switch = device.profile == "Sicher"
        return {
            "device": device.to_dict(),
            "expected": {
                "vpn_required": expected_vpn,
                "routing_table": 100 if expected_vpn else None,
                "dns_target": "10.64.0.1" if expected_vpn else "1.1.1.1",
                "kill_switch": expected_kill_switch,
            },
            "actual": {
                "ip_rule_present": has_policy_rule,
                "table_100_default_present": any(line.startswith("default") and "wg" in line for line in routes),
                "dns_rule_present": has_dns_rule,
                "firewall_rule_present": has_filter_rule,
                "consistent": (not expected_vpn or has_policy_rule) and has_dns_rule and has_filter_rule,
            },
            "raw": {"ip_rules": ip_rules, "routes": routes, "filter_rules": [line for line in filter_rules if device.ip_address in line], "nat_rules": [line for line in nat_rules if device.ip_address in line]},
        }


class VpnService:
    """Coordinate server selection, WireGuard switch, health check, and state."""

    def __init__(self, paths: GatewayPaths, servers: ServerRepository, state: StateRepository, wireguard: WireGuardClient, health: HealthChecker) -> None:
        self.paths = paths
        self.servers = servers
        self.state = state
        self.wireguard = wireguard
        self.health = health

    def switch(self, server_id: str) -> dict:
        """Switch the active peer and persist only a verified result."""
        identifier = validate_identifier(server_id, "server_id")
        with ProcessLock(self.paths.lock_dir / "vpn-gateway-vpn-switch.lock"):
            server = next((item for item in self.servers.list() if item.id == identifier), None)
            if server is None:
                raise GatewayError("SERVER_NOT_FOUND", "Server does not exist")
            if not server.enabled:
                raise GatewayError("SERVER_DISABLED", "Server is disabled")
            previous = self.wireguard.capture_runtime_config()
            try:
                self.wireguard.apply_server(server, previous)
                health = self.health.verify(self.wireguard)
                self.state.save_active_server(server.id)
                return {"server": server.to_dict(), "health": health, "rolled_back": False}
            except GatewayError as error:
                try:
                    self.wireguard.restore_runtime_config(previous)
                    rollback_health = self.health.verify(self.wireguard)
                except GatewayError as rollback_error:
                    raise GatewayError("ROLLBACK_FAILED", "VPN switch and rollback both failed") from rollback_error
                raise GatewayError(error.code, str(error), details={"rolled_back": True, "rollback_health": rollback_health}) from error

    def restore(self) -> dict:
        """Restore the last verified server after WireGuard starts."""
        server_id = self.state.get_active_server_id()
        if server_id is None:
            return {"restored": False, "reason": "No verified server state"}
        server = next((item for item in self.servers.list() if item.id == server_id), None)
        if server is None:
            raise GatewayError("SERVER_NOT_FOUND", "The saved active server no longer exists")
        if self.wireguard.is_server_active(server):
            health = self.health.verify_existing(self.wireguard)
            return {"restored": True, "already_active": True, "server": server.to_dict(), "health": health}
        result = self.switch(server_id)
        return result | {"restored": True}
