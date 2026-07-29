"""Validated data models used by the Pi-side management services."""

from __future__ import annotations

import base64
import ipaddress
import re
from dataclasses import asdict, dataclass

from .errors import ValidationError

SERVER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
PROFILE_IDS = ("Normal", "VPN", "Sicher")


def validate_identifier(value: str, field: str = "id") -> str:
    """Validate a stable identifier allowed in fixed CLI arguments."""
    if not isinstance(value, str) or not SERVER_ID_PATTERN.fullmatch(value):
        raise ValidationError(f"{field} must contain lowercase letters, numbers, or hyphens")
    return value


def validate_profile(value: str) -> str:
    """Validate a GUI-supported gateway profile without redefining its rules."""
    if value not in PROFILE_IDS:
        raise ValidationError("Unknown profile", code="PROFILE_NOT_FOUND")
    return value


def validate_public_key(value: str) -> str:
    """Validate the base64 shape of a WireGuard peer public key."""
    if not isinstance(value, str):
        raise ValidationError("Public key must be a string", code="INVALID_PUBLIC_KEY")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValidationError("Public key is not base64", code="INVALID_PUBLIC_KEY") from error
    if len(decoded) != 32:
        raise ValidationError("Public key must decode to 32 bytes", code="INVALID_PUBLIC_KEY")
    return value


def validate_endpoint(value: str) -> str:
    """Validate a host-or-IP endpoint with an explicit WireGuard port."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValidationError("Endpoint must use host:port", code="INVALID_ENDPOINT")
    host, port_text = value.rsplit(":", 1)
    if not host or len(host) > 253 or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValidationError("Endpoint must use a valid host and port", code="INVALID_ENDPOINT")
    return value


def validate_ip_address(value: str) -> str:
    """Validate and canonicalize a device IPv4 address."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValidationError("Device IP address is invalid") from error
    if address.version != 4:
        raise ValidationError("Only IPv4 device addresses are supported")
    return str(address)


@dataclass(frozen=True)
class Server:
    """A selectable WireGuard peer without any local private key material."""

    id: str
    name: str
    country: str
    city: str
    endpoint: str
    public_key: str
    allowed_ips: tuple[str, ...] = ("0.0.0.0/0",)
    persistent_keepalive: int | None = 25
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict, *, server_id: str | None = None) -> "Server":
        """Create a validated server from JSON-compatible request data."""
        identifier = validate_identifier(server_id or data.get("id", ""), "server_id")
        name = data.get("name", "").strip()
        if not name:
            raise ValidationError("Server name is required")
        allowed_ips = tuple(data.get("allowed_ips", ["0.0.0.0/0"]))
        if not allowed_ips:
            raise ValidationError("At least one allowed IP range is required")
        try:
            for item in allowed_ips:
                ipaddress.ip_network(item, strict=False)
        except ValueError as error:
            raise ValidationError("Allowed IP range is invalid") from error
        keepalive = data.get("persistent_keepalive", 25)
        if keepalive is not None and (not isinstance(keepalive, int) or not 0 <= keepalive <= 65535):
            raise ValidationError("Persistent keepalive must be between 0 and 65535")
        return cls(
            id=identifier,
            name=name,
            country=str(data.get("country", "")).strip(),
            city=str(data.get("city", "")).strip(),
            endpoint=validate_endpoint(data.get("endpoint", "")),
            public_key=validate_public_key(data.get("public_key", "")),
            allowed_ips=allowed_ips,
            persistent_keepalive=keepalive,
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        """Return the stable JSON representation expected by the GUI."""
        data = asdict(self)
        data["allowed_ips"] = list(self.allowed_ips)
        return data


@dataclass(frozen=True)
class Device:
    """A legacy IP-based device exposed through a stable GUI identifier."""

    id: str
    name: str
    ip_address: str
    profile: str
    mac_address: str | None = None
    enabled: bool = True

    @classmethod
    def from_legacy(cls, ip_address: str, data: dict) -> "Device":
        """Map the existing devices.json shape without changing its keys."""
        address = validate_ip_address(ip_address)
        profile = data.get("profile", "")
        if profile not in (*PROFILE_IDS, "Sniff"):
            raise ValidationError("Legacy device profile is invalid", code="CONFIG_INVALID")
        return cls(
            id="legacy-" + address.replace(".", "-"),
            name=str(data.get("name", address)),
            ip_address=address,
            profile=profile,
            mac_address=data.get("mac_address"),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        """Return the structured GUI representation."""
        return asdict(self)
