"""Pure tests for the idempotent system setup command."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vpn_gateway.cli import setup_gateway
from vpn_gateway.errors import GatewayError
from vpn_gateway.paths import GatewayPaths


class Recorder:
    def __init__(self, result: object = None) -> None:
        self.calls = 0
        self.result = result

    def restore(self) -> object:
        self.calls += 1
        return self.result


def services_for(paths: GatewayPaths) -> SimpleNamespace:
    return SimpleNamespace(
        server_management=SimpleNamespace(paths=paths),
        vpn=Recorder({"restored": True}),
        device_profiles=Recorder(),
    )


def test_setup_creates_managed_folders_and_restores_rules(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "devices.json").write_text("{}", encoding="utf-8")
    paths = GatewayPaths(
        legacy_dir=legacy,
        config_dir=tmp_path / "opt" / "config",
        state_dir=tmp_path / "opt" / "state",
        lock_dir=tmp_path / "locks",
    )
    services = services_for(paths)

    result = setup_gateway(services)

    assert result["configured"] is True
    assert services.vpn.calls == 1
    assert services.device_profiles.calls == 1
    for directory in (paths.config_dir, paths.state_dir, paths.logs_dir, paths.backups_dir):
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o750


def test_setup_refuses_to_run_without_legacy_configuration(tmp_path) -> None:
    paths = GatewayPaths(
        legacy_dir=tmp_path / "legacy",
        config_dir=tmp_path / "opt" / "config",
        state_dir=tmp_path / "opt" / "state",
        lock_dir=tmp_path / "locks",
    )

    with pytest.raises(GatewayError) as error:
        setup_gateway(services_for(paths))

    assert error.value.code == "CONFIG_NOT_FOUND"
    assert set(error.value.details["missing"]) == {str(paths.legacy_config), str(paths.legacy_devices)}
