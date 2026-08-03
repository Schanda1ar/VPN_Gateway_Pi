"""Build the fixed, version-controlled payload used for one-click Pi setup."""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path


# Deliberately fixed: no user-selected paths or files are ever sent to the Pi.
INSTALL_BUNDLE_FILES = (
    "README.md",
    "main.py",
    "pyproject.toml",
    "pi/scripts/install.sh",
    "pi/scripts/uninstall.sh",
    "pi/scripts/vpn-gateway-cli",
    "pi/systemd/vpn-gateway-restore.service",
    "vpn_gateway/__init__.py",
    "vpn_gateway/cli.py",
    "vpn_gateway/command_runner.py",
    "vpn_gateway/errors.py",
    "vpn_gateway/gateway_adapter.py",
    "vpn_gateway/locking.py",
    "vpn_gateway/models.py",
    "vpn_gateway/paths.py",
    "vpn_gateway/repositories.py",
    "vpn_gateway/services.py",
    "vpn_gateway/storage.py",
    "vpn_gateway/wireguard.py",
)

EXECUTABLE_BUNDLE_FILES = {
    "pi/scripts/install.sh",
    "pi/scripts/uninstall.sh",
    "pi/scripts/vpn-gateway-cli",
}


def deployment_root() -> Path:
    """Return the source root or PyInstaller extraction root containing deployment data."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def build_install_bundle(root: Path | None = None) -> bytes:
    """Create a deterministic allowlisted tar.gz payload for the remote installer."""
    source_root = (root or deployment_root()).resolve()
    missing = [relative for relative in INSTALL_BUNDLE_FILES if not (source_root / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"Installationsdateien fehlen: {', '.join(missing)}")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative in INSTALL_BUNDLE_FILES:
            source = (source_root / relative).resolve()
            if source_root not in source.parents:
                raise ValueError(f"Ungültiger Installationspfad: {relative}")
            data = source.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            info.mode = 0o755 if relative in EXECUTABLE_BUNDLE_FILES else 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
