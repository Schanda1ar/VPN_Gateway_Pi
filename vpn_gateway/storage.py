"""Atomic JSON persistence with validation-before-replacement semantics."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .errors import GatewayError


def read_json(path: Path, *, missing_code: str = "CONFIG_NOT_FOUND") -> dict:
    """Load a JSON object and turn malformed files into stable CLI errors."""
    if not path.exists():
        raise GatewayError(missing_code, f"Configuration file does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GatewayError("CONFIG_INVALID", f"Configuration is invalid: {path}") from error
    if not isinstance(value, dict):
        raise GatewayError("CONFIG_INVALID", f"Configuration root must be an object: {path}")
    return value


def atomic_write_json(path: Path, value: dict, *, backup: bool = True, mode: int = 0o600) -> None:
    """Persist a JSON object atomically and preserve the previous valid file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if backup and path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_path.write_bytes(path.read_bytes())
            os.chmod(backup_path, mode)
        os.replace(temporary, path)
        read_json(path)
    except (OSError, TypeError, ValueError) as error:
        try:
            temporary.unlink(missing_ok=True)
        except UnboundLocalError:
            pass
        raise GatewayError("STATE_WRITE_FAILED", f"Could not write state: {path}") from error
