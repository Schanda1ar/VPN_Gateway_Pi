"""Safe, timeout-bound execution of known Pi system commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence

from .errors import GatewayError


@dataclass(frozen=True)
class CommandResult:
    """Captured result of a non-shell system command."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Run explicit argv commands without interpolation or shell execution."""

    def run(self, args: Sequence[str], *, timeout: int = 15, check: bool = True) -> CommandResult:
        """Execute a command and raise a stable error for failures or timeouts."""
        command = tuple(str(part) for part in args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise GatewayError("COMMAND_TIMEOUT", f"Command timed out: {command[0]}") from error
        except OSError as error:
            raise GatewayError("INTERNAL_ERROR", f"Could not start command: {command[0]}") from error
        result = CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode != 0:
            raise GatewayError("COMMAND_FAILED", f"Command failed: {command[0]}", details={"stderr": result.stderr.strip()})
        return result
