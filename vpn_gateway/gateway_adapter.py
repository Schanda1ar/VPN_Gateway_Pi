"""Adapter that delegates profile application to the existing gateway implementation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from .command_runner import CommandRunner
from .errors import GatewayError
from .models import Device, validate_profile
from .paths import GatewayPaths
from .storage import atomic_write_json


class GatewayAdapter:
    """Bridge new services to the existing Profile/iptables implementation."""

    def __init__(self, paths: GatewayPaths, runner: CommandRunner | None = None) -> None:
        self.paths = paths
        self.runner = runner or CommandRunner()

    def apply_profile(self, device: Device, profile: str) -> None:
        """Apply a supported profile through the retained GatewayManager API."""
        validate_profile(profile)
        module_path = self.paths.legacy_dir / "main.py"
        if not module_path.exists():
            raise GatewayError("CONFIG_NOT_FOUND", "Existing gateway implementation was not found")
        spec = importlib.util.spec_from_file_location("legacy_gateway_main", module_path)
        if spec is None or spec.loader is None:
            raise GatewayError("INTERNAL_ERROR", "Could not load existing gateway implementation")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manager = module.GatewayManager(self.paths.legacy_config)
        manager.apply_profile(device.ip_address, profile, device.name, update_json=True)

    def restore_legacy_devices(self) -> None:
        """Reapply all legacy profiles after a failed profile transition."""
        module_path = self.paths.legacy_dir / "main.py"
        spec = importlib.util.spec_from_file_location("legacy_gateway_main", module_path)
        if spec is None or spec.loader is None:
            raise GatewayError("INTERNAL_ERROR", "Could not load existing gateway implementation")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.GatewayManager(self.paths.legacy_config).init_all_devices()

    def legacy_snapshot(self) -> bytes:
        """Capture the exact device state before a profile transaction."""
        return self.paths.legacy_devices.read_bytes()

    def restore_snapshot(self, snapshot: bytes) -> None:
        """Restore a previously captured legacy device file before re-applying rules."""
        try:
            payload = json.loads(snapshot.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Legacy device root is not an object")
            atomic_write_json(self.paths.legacy_devices, payload)
        except (OSError, ValueError) as error:
            raise GatewayError("DEVICE_ROLLBACK_FAILED", "Could not restore device state") from error
