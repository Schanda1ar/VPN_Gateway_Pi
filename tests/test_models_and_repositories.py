"""Unit tests for persisted gateway data that require no Pi system commands."""

from __future__ import annotations

import base64

import pytest

from vpn_gateway.errors import GatewayError, ValidationError
from vpn_gateway.models import Server
from vpn_gateway.paths import GatewayPaths
from vpn_gateway.repositories import ServerRepository, StateRepository


def server_payload(identifier: str = "at-vie-001") -> dict:
    """Return a minimal valid server request without secret material."""
    return {
        "id": identifier,
        "name": "Austria Vienna",
        "country": "Austria",
        "city": "Vienna",
        "endpoint": "203.0.113.10:51820",
        "public_key": base64.b64encode(b"p" * 32).decode(),
    }


def test_server_rejects_invalid_endpoint() -> None:
    """Server endpoints always require an explicit valid port."""
    payload = server_payload()
    payload["endpoint"] = "not-an-endpoint"
    with pytest.raises(ValidationError) as error:
        Server.from_dict(payload)
    assert error.value.code == "INVALID_ENDPOINT"


def test_server_repository_writes_and_reads_schema(tmp_path) -> None:
    """Server state is stored with schema version and round-trips exactly."""
    paths = GatewayPaths(tmp_path / "legacy", tmp_path / "config", tmp_path / "state", tmp_path / "locks")
    repository = ServerRepository(paths)
    server = Server.from_dict(server_payload())
    repository.save([server])
    assert repository.list() == [server]
    assert paths.servers.with_suffix(".json.bak").exists() is False


def test_state_repository_records_only_verified_server(tmp_path) -> None:
    """Selected-server state starts empty and records the last verified ID."""
    paths = GatewayPaths(tmp_path / "legacy", tmp_path / "config", tmp_path / "state", tmp_path / "locks")
    state = StateRepository(paths)
    assert state.get_active_server_id() is None
    state.save_active_server("at-vie-001")
    assert state.get_active_server_id() == "at-vie-001"
