"""Focused tests for optional Pi-local policy routing."""

from __future__ import annotations

import json

import pytest

from vpn_gateway.command_runner import CommandResult
from vpn_gateway.errors import ValidationError
from vpn_gateway.host_routing import HOST_ROUTING_TABLE, HostRoutingConfig, HostRoutingService


class FakeRunner:
    """Capture known commands while returning deterministic host state."""

    def __init__(self, *, rules: str = "", routes: str = "") -> None:
        self.rules = rules
        self.routes = routes
        self.commands: list[tuple[str, ...]] = []

    def run(self, args, *, timeout: int = 15, check: bool = True) -> CommandResult:
        command = tuple(str(part) for part in args)
        self.commands.append(command)
        if command[:4] == ("wg", "show", "wg0", "endpoints"):
            return CommandResult(command, 0, "peer-key 45.134.212.66:51820\n", "")
        if command[:4] == ("ip", "-4", "rule", "show"):
            return CommandResult(command, 0, self.rules, "")
        if command[:5] == ("ip", "-4", "route", "show", "table"):
            return CommandResult(command, 0, self.routes, "")
        return CommandResult(command, 0, "", "")


def enabled_document() -> dict:
    """Return the smallest valid Pi-local routing configuration."""
    return {
        "local_network": "10.0.0.0/24",
        "host_routing": {
            "enabled": True,
            "source_address": "10.0.0.100",
            "lan_interface": "eth0",
            "lan_gateway": "10.0.0.138",
            "routing_table": 101,
            "rule_priority": 1000,
        },
    }


def test_missing_host_routing_is_disabled() -> None:
    """Existing installations retain their prior routing when the block is absent."""
    config = HostRoutingConfig.from_document({"local_network": "10.0.0.0/24"})
    assert config.enabled is False


def test_host_routing_cannot_use_legacy_device_table() -> None:
    """The Pi-local policy cannot overlap the existing device table 100."""
    document = enabled_document()
    document["host_routing"]["routing_table"] = 100

    with pytest.raises(ValidationError) as error:
        HostRoutingConfig.from_document(document)

    assert error.value.code == "CONFIG_INVALID"


def test_disabled_host_routing_flushes_only_its_owned_table(tmp_path) -> None:
    """A minimal disabled block removes the rule and dedicated table safely."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"host_routing": {"enabled": False}}),
        encoding="utf-8",
    )
    runner = FakeRunner()

    result = HostRoutingService(config_path, runner).restore()

    assert result == {"enabled": False, "restored": False}
    assert runner.commands == [
        ("ip", "-4", "rule", "del", "pref", "1000", "iif", "lo", "lookup", str(HOST_ROUTING_TABLE)),
        ("ip", "-4", "route", "flush", "table", str(HOST_ROUTING_TABLE)),
    ]


def test_restore_installs_lan_endpoint_default_and_local_rule(tmp_path) -> None:
    """Restore creates all exceptions before the local-output policy rule."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(enabled_document()), encoding="utf-8")
    runner = FakeRunner()

    result = HostRoutingService(config_path, runner).restore()

    assert result == {"enabled": True, "restored": True, "routing_table": 101}
    commands = runner.commands
    lan_index = commands.index(
        (
            "ip",
            "-4",
            "route",
            "replace",
            "10.0.0.0/24",
            "dev",
            "eth0",
            "src",
            "10.0.0.100",
            "table",
            "101",
        )
    )
    endpoint_index = commands.index(
        (
            "ip",
            "-4",
            "route",
            "replace",
            "45.134.212.66/32",
            "via",
            "10.0.0.138",
            "dev",
            "eth0",
            "src",
            "10.0.0.100",
            "table",
            "101",
        )
    )
    default_index = commands.index(("ip", "-4", "route", "replace", "default", "dev", "wg0", "table", "101"))
    rule_index = commands.index(("ip", "-4", "rule", "add", "pref", "1000", "iif", "lo", "lookup", "101"))
    assert lan_index < endpoint_index < default_index < rule_index


def test_restore_does_not_duplicate_existing_local_rule(tmp_path) -> None:
    """Repeated restore is idempotent for an already-installed policy selector."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(enabled_document()), encoding="utf-8")
    runner = FakeRunner(rules="1000: from all iif lo lookup 101\n")

    HostRoutingService(config_path, runner).restore()

    assert ("ip", "-4", "rule", "add", "pref", "1000", "iif", "lo", "lookup", "101") not in runner.commands
