"""Services and CLI for administering the Raspberry Pi VPN gateway.

The installed package metadata is the authoritative version source.  The
fallback keeps a source checkout usable before it has been built into a
wheel (for example while running the release workflow).
"""

from pathlib import Path
import tomllib
from importlib.metadata import PackageNotFoundError, version


_project_file = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _project_file.exists():
    try:
        with _project_file.open("rb") as handle:
            __version__ = str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, TypeError):
        __version__ = "0.0.0"
else:
    try:
        __version__ = version("vpn-gateway-pi")
    except PackageNotFoundError:
        __version__ = "0.0.0"
