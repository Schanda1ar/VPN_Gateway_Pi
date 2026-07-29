"""Stable errors returned by the gateway CLI."""


class GatewayError(Exception):
    """Expected operational error with a stable public error code."""

    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ValidationError(GatewayError):
    """Raised when a request or persistent configuration is invalid."""

    def __init__(self, message: str, *, code: str = "INVALID_ARGUMENT") -> None:
        super().__init__(code, message)
