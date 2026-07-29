"""Non-blocking process locks used for conflicting gateway changes."""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows GUI development only
    fcntl = None
    import msvcrt
from pathlib import Path

from .errors import GatewayError


class ProcessLock:
    """Exclusive advisory lock released automatically after a CLI operation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as error:
            self._handle.close()
            raise GatewayError("LOCKED", "Another gateway operation is already running") from error
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            else:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            self._handle.close()
