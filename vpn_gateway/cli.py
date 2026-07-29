"""Versioned JSON-only command line interface for the VPN gateway GUI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

from loguru import logger

from . import __version__
from .command_runner import CommandRunner
from .errors import GatewayError
from .gateway_adapter import GatewayAdapter
from .models import Server
from .paths import GatewayPaths
from .repositories import DeviceRepository, ServerRepository, StateRepository
from .services import DeviceProfileService, EffectiveRulesService, ServerManagementService, VpnService
from .wireguard import HealthChecker, WireGuardClient

API_VERSION = 1


@dataclass
class Services:
    """Constructed service graph for one isolated CLI request."""

    server_management: ServerManagementService
    devices: DeviceRepository
    device_profiles: DeviceProfileService
    effective_rules: EffectiveRulesService
    vpn: VpnService
    state: StateRepository
    wireguard: WireGuardClient


def build_services() -> Services:
    """Compose the Pi services around the actual legacy deployment paths."""
    paths = GatewayPaths.from_environment()
    runner = CommandRunner()
    servers = ServerRepository(paths)
    state = StateRepository(paths)
    devices = DeviceRepository(paths)
    adapter = GatewayAdapter(paths, runner)
    wireguard = WireGuardClient(runner)
    return Services(
        server_management=ServerManagementService(paths, servers, state),
        devices=devices,
        device_profiles=DeviceProfileService(paths, devices, adapter),
        effective_rules=EffectiveRulesService(devices, runner),
        vpn=VpnService(paths, servers, state, wireguard, HealthChecker()),
        state=state,
        wireguard=wireguard,
    )


def parse_request_json() -> dict:
    """Read exactly one JSON object from stdin for server mutations."""
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError as error:
        raise GatewayError("INVALID_JSON", "Request body is not valid JSON") from error
    if not isinstance(value, dict):
        raise GatewayError("INVALID_JSON", "Request body must be a JSON object")
    return value


def require_root() -> None:
    """Protect system-mutating and privileged diagnostic operations on Linux."""
    if os.name == "posix" and os.geteuid() != 0:
        raise GatewayError("NOT_ROOT", "Run this command through the installed sudo wrapper")


def migrate_current_server(services: Services) -> dict:
    """Create the initial editable server from the one current WireGuard peer."""
    repository = services.server_management.servers
    if repository.paths.servers.exists():
        existing = next((item for item in repository.list() if item.id == "migrated-current"), None)
        if existing is None:
            return {"migrated": False, "reason": "Server configuration already exists"}
        if services.state.get_active_server_id() is None:
            health = HealthChecker().verify_existing(services.wireguard)
            services.state.save_active_server(existing.id)
            return {"migrated": False, "resumed": True, "server": existing.to_dict(), "health": health}
        return {"migrated": False, "reason": "Server configuration already exists"}
    endpoints = services.wireguard.runner.run(["wg", "show", "wg0", "endpoints"]).stdout.split()
    allowed = services.wireguard.runner.run(["wg", "show", "wg0", "allowed-ips"]).stdout.splitlines()
    keepalive = services.wireguard.runner.run(["wg", "show", "wg0", "persistent-keepalive"]).stdout.split()
    if len(endpoints) != 2 or len(allowed) != 1:
        raise GatewayError("CONFIG_INVALID", "The running tunnel must have exactly one peer to migrate")
    peer_key, endpoint = endpoints
    allowed_parts = allowed[0].split(maxsplit=1)
    if len(allowed_parts) != 2:
        raise GatewayError("CONFIG_INVALID", "The running peer has no allowed IP ranges")
    allowed_ips = allowed_parts[1].split()
    keepalive_value = None
    if len(keepalive) == 2 and keepalive[1].isdigit():
        keepalive_value = int(keepalive[1])
    server = Server.from_dict(
        {
            "id": "migrated-current",
            "name": "Migrated current server",
            "country": "",
            "city": "",
            "endpoint": endpoint,
            "public_key": peer_key,
            "allowed_ips": allowed_ips,
            "persistent_keepalive": keepalive_value,
            "enabled": True,
        }
    )
    health = HealthChecker().verify_existing(services.wireguard)
    repository.initialize(server)
    services.state.save_active_server(server.id)
    return {"migrated": True, "server": server.to_dict(), "health": health}


def status(services: Services) -> dict:
    """Collect the dashboard status without exposing peer or private-key secrets."""
    interfaces = services.wireguard.runner.run(["wg", "show", "interfaces"], check=False).stdout.split()
    interface_up = "wg0" in interfaces
    handshake = services.wireguard.latest_handshake_epoch() if interface_up else 0
    devices = services.devices.list()
    counts = {"normal": 0, "vpn": 0, "secure": 0}
    for device in devices:
        if device.profile == "Normal":
            counts["normal"] += 1
        elif device.profile == "VPN":
            counts["vpn"] += 1
        elif device.profile == "Sicher":
            counts["secure"] += 1
    return {
        "gateway": {"reachable": True, "routing_table": 100},
        "vpn": {
            "interface": "wg0",
            "interface_up": interface_up,
            "server_id": services.state.get_active_server_id(),
            "latest_handshake_epoch": handshake,
        },
        "devices": {"total": len(devices), **counts},
    }


def create_parser() -> argparse.ArgumentParser:
    """Create the fixed, non-shell CLI command surface."""
    def json_option(command: argparse.ArgumentParser) -> argparse.ArgumentParser:
        command.add_argument("--json", action="store_true", help="Return the mandatory JSON response")
        return command

    parser = argparse.ArgumentParser(prog="vpn-gateway-cli")
    subparsers = parser.add_subparsers(dest="resource", required=True)
    json_option(subparsers.add_parser("version"))
    json_option(subparsers.add_parser("status"))

    server = subparsers.add_parser("server")
    server_sub = server.add_subparsers(dest="action", required=True)
    json_option(server_sub.add_parser("list"))
    json_option(server_sub.add_parser("add"))
    update = json_option(server_sub.add_parser("update"))
    update.add_argument("--server-id", required=True)
    delete = json_option(server_sub.add_parser("delete"))
    delete.add_argument("--server-id", required=True)
    json_option(server_sub.add_parser("migrate-current"))

    vpn = subparsers.add_parser("vpn")
    vpn_sub = vpn.add_subparsers(dest="action", required=True)
    switch = json_option(vpn_sub.add_parser("switch"))
    switch.add_argument("--server-id", required=True)
    json_option(vpn_sub.add_parser("restore"))

    device = subparsers.add_parser("device")
    device_sub = device.add_subparsers(dest="action", required=True)
    json_option(device_sub.add_parser("list"))
    show = json_option(device_sub.add_parser("show"))
    show.add_argument("--device-id", required=True)
    set_profile = json_option(device_sub.add_parser("set-profile"))
    set_profile.add_argument("--device-id", required=True)
    set_profile.add_argument("--profile", required=True)
    json_option(device_sub.add_parser("restore"))

    diagnostics = subparsers.add_parser("diagnostics")
    diagnostics_sub = diagnostics.add_subparsers(dest="action", required=True)
    rules = json_option(diagnostics_sub.add_parser("rules"))
    rules.add_argument("--device-id", required=True)
    return parser


def dispatch(arguments: argparse.Namespace, services: Services) -> dict:
    """Route one fixed command to its service without dynamic command execution."""
    if arguments.resource == "version":
        return {"api_version": API_VERSION, "application_version": __version__}
    require_root()
    if arguments.resource == "status":
        return status(services)
    if arguments.resource == "server":
        if arguments.action == "list":
            return {"servers": [server.to_dict() for server in services.server_management.list()], "active_server_id": services.state.get_active_server_id()}
        if arguments.action == "add":
            return {"server": services.server_management.add(parse_request_json()).to_dict()}
        if arguments.action == "update":
            return {"server": services.server_management.update(arguments.server_id, parse_request_json()).to_dict()}
        if arguments.action == "delete":
            services.server_management.delete(arguments.server_id)
            return {"deleted_server_id": arguments.server_id}
        return migrate_current_server(services)
    if arguments.resource == "vpn":
        return services.vpn.switch(arguments.server_id) if arguments.action == "switch" else services.vpn.restore()
    if arguments.resource == "device":
        if arguments.action == "list":
            return {"devices": [device.to_dict() for device in services.devices.list()]}
        if arguments.action == "show":
            return {"device": services.devices.get(arguments.device_id).to_dict()}
        if arguments.action == "restore":
            services.device_profiles.restore()
            return {"restored": True}
        return {"device": services.device_profiles.set_profile(arguments.device_id, arguments.profile).to_dict()}
    return services.effective_rules.get(arguments.device_id)


def emit(payload: dict, exit_code: int = 0) -> int:
    """Write exactly one JSON response to stdout."""
    print(json.dumps({"success": exit_code == 0, "api_version": API_VERSION, **payload}, ensure_ascii=False))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and convert expected exceptions to stable JSON errors."""
    try:
        arguments = create_parser().parse_args(argv)
        result = dispatch(arguments, build_services())
        return emit(result)
    except GatewayError as error:
        logger.error("Gateway CLI error {}: {}", error.code, error)
        return emit({"error_code": error.code, "message": str(error), "details": error.details}, 1)
    except Exception:
        logger.exception("Unhandled gateway CLI error")
        return emit({"error_code": "INTERNAL_ERROR", "message": "Unexpected gateway error"}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
