"""Pure tests for WireGuard target configuration rendering."""

from __future__ import annotations

import base64

import pytest

from vpn_gateway.errors import GatewayError
from vpn_gateway.models import Server
from vpn_gateway.wireguard import WireGuardClient


CURRENT_CONFIG = """[Interface]
PrivateKey = hidden
ListenPort = 51820

[Peer]
PublicKey = old-key
AllowedIPs = 0.0.0.0/0
Endpoint = 198.51.100.10:51820
PersistentKeepalive = 25
"""


def test_render_target_config_replaces_complete_peer_identity() -> None:
    """Switching changes peer key, endpoint, allowed ranges, and keepalive together."""
    server = Server.from_dict(
        {
            "id": "new-server",
            "name": "New server",
            "endpoint": "203.0.113.10:51820",
            "public_key": base64.b64encode(b"n" * 32).decode(),
            "allowed_ips": ["0.0.0.0/0"],
            "persistent_keepalive": 15,
        }
    )
    rendered = WireGuardClient()._render_target_config(CURRENT_CONFIG, server)
    assert server.public_key in rendered
    assert "Endpoint = 203.0.113.10:51820" in rendered
    assert "PersistentKeepalive = 15" in rendered
    assert "old-key" not in rendered


def test_render_target_config_rejects_multiple_peers() -> None:
    """The v1 switch path never changes an ambiguous multi-peer tunnel."""
    server = Server.from_dict(
        {"id": "server", "name": "Server", "endpoint": "203.0.113.10:51820", "public_key": base64.b64encode(b"n" * 32).decode()}
    )
    with pytest.raises(GatewayError) as error:
        WireGuardClient()._render_target_config(CURRENT_CONFIG + "\n[Peer]\nPublicKey = another\n", server)
    assert error.value.code == "CONFIG_INVALID"
