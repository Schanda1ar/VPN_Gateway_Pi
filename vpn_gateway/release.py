"""Trusted release metadata and artifact validation.

This module deliberately uses only the Python standard library.  The Pi is
updated over SSH in environments where adding a crypto package to the
bootstrap image is undesirable, so Ed25519 verification is implemented
directly from RFC 8032.  Release creation is performed by GitHub Actions with
the matching private key and is not part of the runtime package.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RELEASE_REPOSITORY = "Schanda1ar/VPN_Gateway_Pi"
RELEASE_BASE_URL = f"https://github.com/{RELEASE_REPOSITORY}/releases/download"
RELEASE_LATEST_API_URL = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest"
GITHUB_ASSET_HOSTS = frozenset(
    {
        "github.com",
        "api.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)
MANIFEST_NAME = "manifest.json"
MANIFEST_SIGNATURE_NAME = "manifest.sig"
SUPPORTED_API_VERSION = 1

# Provisioning boundary: replace this empty value with the production
# Ed25519 public key before deployment. An empty value fails closed; tests
# must inject their own key through the public_key argument.
RELEASE_PUBLIC_KEY_HEX = ""
RELEASE_PUBLIC_KEY = b""

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseValidationError(ValueError):
    """Raised when release metadata or an artifact is not trusted."""

    def __init__(self, message: str, code: str = "RELEASE_INVALID") -> None:
        super().__init__(message)
        self.code = code


def provisioned_public_key() -> bytes:
    """Return the configured production key or fail closed when absent."""
    if not RELEASE_PUBLIC_KEY_HEX:
        raise ReleaseValidationError("Release signing public key is not provisioned", "KEY_NOT_PROVISIONED")
    try:
        key = bytes.fromhex(RELEASE_PUBLIC_KEY_HEX)
    except ValueError as error:
        raise ReleaseValidationError("Release signing public key is not hexadecimal", "KEY_INVALID") from error
    if len(key) != 32:
        raise ReleaseValidationError("Release signing public key has invalid length", "KEY_INVALID")
    return key


def parse_semver(value: str) -> tuple[int, int, int]:
    """Parse a strict ``X.Y.Z`` release version."""
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise ReleaseValidationError("Release version must be strict SemVer X.Y.Z", "INVALID_VERSION")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def is_semver(value: str) -> bool:
    """Return whether *value* is an accepted release version."""
    return isinstance(value, str) and _SEMVER.fullmatch(value) is not None


def version_is_newer(candidate: str, installed: str | None) -> bool:
    """Return whether a candidate is newer than an installed version."""
    candidate_tuple = parse_semver(candidate)
    return installed is None or candidate_tuple > parse_semver(installed)


def _canonical_document(value: dict[str, Any]) -> bytes:
    """Serialize a manifest deterministically for signature verification."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def unsigned_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a copy without accepted signature envelope fields."""
    document = copy.deepcopy(manifest)
    for key in ("signature", "signature_ed25519", "manifest_signature"):
        document.pop(key, None)
    return document


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the exact byte representation that GitHub Actions signs."""
    return _canonical_document(unsigned_manifest(manifest))


# RFC 8032 Ed25519 arithmetic.  Keeping this local avoids a runtime crypto
# dependency while preserving strict detached-signature verification.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_B_Y = 4 * pow(5, _Q - 2, _Q) % _Q
_B_X = pow((_B_Y * _B_Y - 1) * pow(_D * _B_Y * _B_Y + 1, _Q - 2, _Q), (_Q + 3) // 8, _Q)
if (_B_X * _B_X - (_B_Y * _B_Y - 1) * pow(_D * _B_Y * _B_Y + 1, _Q - 2, _Q)) % _Q:
    _B_X = (_B_X * _I) % _Q
if _B_X & 1:
    _B_X = _Q - _B_X
_B = (_B_X, _B_Y)


def _point_add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    denominator_x = pow(1 + _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    denominator_y = pow(1 - _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    return ((x1 * y2 + x2 * y1) * denominator_x % _Q, (y1 * y2 + x1 * x2) * denominator_y % _Q)


def _scalar_mult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise ReleaseValidationError("Ed25519 point has invalid length", "SIGNATURE_INVALID")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        raise ReleaseValidationError("Ed25519 point is out of range", "SIGNATURE_INVALID")
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q:
        x = x * _I % _Q
    if (x * x - xx) % _Q:
        raise ReleaseValidationError("Ed25519 point is not on the curve", "SIGNATURE_INVALID")
    if (x & 1) != sign:
        x = _Q - x
    return x, y


def _signature_bytes(signature: str | bytes) -> bytes:
    if isinstance(signature, bytes):
        result = signature
    else:
        try:
            result = base64.b64decode(signature, validate=True)
        except (ValueError, TypeError):
            try:
                result = bytes.fromhex(signature)
            except ValueError as error:
                raise ReleaseValidationError("Signature encoding is invalid", "SIGNATURE_INVALID") from error
    if len(result) != 64:
        raise ReleaseValidationError("Ed25519 signature has invalid length", "SIGNATURE_INVALID")
    return result


def verify_ed25519(message: bytes, signature: str | bytes, public_key: bytes | None = None) -> bool:
    """Verify a detached Ed25519 signature without external dependencies."""
    try:
        public_key = public_key if public_key is not None else provisioned_public_key()
        signature_bytes = _signature_bytes(signature)
        if len(public_key) != 32:
            raise ReleaseValidationError("Ed25519 public key has invalid length", "SIGNATURE_INVALID")
        r = _decode_point(signature_bytes[:32])
        a = _decode_point(public_key)
        scalar = int.from_bytes(signature_bytes[32:], "little")
        if scalar >= _L:
            return False
        challenge = int.from_bytes(hashlib.sha512(signature_bytes[:32] + public_key + message).digest(), "little") % _L
        return _encode_point(_scalar_mult(_B, scalar)) == _encode_point(_point_add(r, _scalar_mult(a, challenge)))
    except ReleaseValidationError:
        return False


def sign_ed25519(message: bytes, seed: bytes) -> bytes:
    """Create a detached Ed25519 signature for local release tooling/tests."""
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must contain 32 bytes")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    public_key = _encode_point(_scalar_mult(_B, scalar))
    nonce = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % _L
    r = _encode_point(_scalar_mult(_B, nonce))
    challenge = int.from_bytes(hashlib.sha512(r + public_key + message).digest(), "little") % _L
    return r + ((nonce + challenge * scalar) % _L).to_bytes(32, "little")


def verify_manifest_signature(manifest: dict[str, Any], signature: str | bytes | None = None, public_key: bytes | None = None) -> bool:
    """Verify a manifest signature or raise a structured validation error."""
    if signature is None:
        signature = manifest.get("signature") or manifest.get("signature_ed25519") or manifest.get("manifest_signature")
    if not isinstance(signature, (str, bytes)):
        raise ReleaseValidationError("Manifest signature is missing", "SIGNATURE_INVALID")
    if public_key is None:
        public_key = provisioned_public_key()
    if not verify_ed25519(canonical_manifest_bytes(manifest), signature, public_key):
        raise ReleaseValidationError("Manifest signature verification failed", "SIGNATURE_INVALID")
    return True


def validate_release_url(url: str, version: str | None = None, filename: str | None = None) -> str:
    """Accept only the repository's immutable tag download endpoint."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        raise ReleaseValidationError("Release URL is not a fixed GitHub endpoint", "URL_NOT_ALLOWED")
    path = parsed.path.rstrip("/")
    expected = f"/{RELEASE_REPOSITORY}/releases/download/"
    if not path.startswith(expected):
        raise ReleaseValidationError("Release URL is not a fixed GitHub release endpoint", "URL_NOT_ALLOWED")
    parts = path[len(expected):].split("/")
    if len(parts) != 2 or (parts[0] != "latest" and (not parts[0].startswith("v") or not is_semver(parts[0][1:]))) or not _SAFE_FILENAME.fullmatch(parts[1]):
        raise ReleaseValidationError("Release URL contains an invalid tag or filename", "URL_NOT_ALLOWED")
    if version is not None and parts[0] != f"v{parse_semver(version)[0]}.{parse_semver(version)[1]}.{parse_semver(version)[2]}":
        raise ReleaseValidationError("Release URL version does not match the manifest", "URL_NOT_ALLOWED")
    if filename is not None and parts[1] != filename:
        raise ReleaseValidationError("Release URL filename does not match the manifest", "URL_NOT_ALLOWED")
    return url


def release_asset_url(version: str, filename: str) -> str:
    """Build the only URL accepted for an artifact download."""
    parse_semver(version)
    if not _SAFE_FILENAME.fullmatch(filename):
        raise ReleaseValidationError("Artifact filename is unsafe", "PATH_INVALID")
    return f"{RELEASE_BASE_URL}/v{version}/{filename}"


def verify_sha256(path: Path, expected: str) -> str:
    """Hash one local artifact and require the declared SHA-256 digest."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ReleaseValidationError("Manifest contains an invalid SHA-256 digest", "HASH_INVALID")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise ReleaseValidationError("Artifact SHA-256 digest does not match", "HASH_MISMATCH")
    return digest


def _safe_destination(root: Path, name: str) -> Path:
    destination = (root / name).resolve()
    if destination != root.resolve() and root.resolve() not in destination.parents:
        raise ReleaseValidationError("Archive entry escapes staging directory", "ARCHIVE_UNSAFE")
    return destination


def safe_extract_archive(archive: Path, destination: Path) -> list[Path]:
    """Extract a ZIP or tar archive without traversal or link entries."""
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                target = _safe_destination(destination, info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ReleaseValidationError("Archive contains a symbolic link", "ARCHIVE_UNSAFE")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read(info))
                extracted.append(target)
        return extracted
    try:
        with tarfile.open(archive, "r:*") as handle:
            for member in handle.getmembers():
                target = _safe_destination(destination, member.name)
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ReleaseValidationError("Archive contains an unsafe entry", "ARCHIVE_UNSAFE")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = handle.extractfile(member)
                if source is None:
                    raise ReleaseValidationError("Archive entry could not be read", "ARCHIVE_UNSAFE")
                target.write_bytes(source.read())
                extracted.append(target)
    except tarfile.TarError as error:
        raise ReleaseValidationError("Artifact is not a supported archive", "ARCHIVE_INVALID") from error
    return extracted


@dataclass(frozen=True)
class ManifestArtifact:
    """One signed release artifact declaration."""

    name: str
    filename: str
    url: str
    sha256: str


@dataclass(frozen=True)
class ReleaseManifest:
    """Validated release manifest used by both Pi and Windows clients."""

    version: str
    api_compatibility: dict[str, int]
    artifacts: dict[str, ManifestArtifact]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReleaseManifest":
        """Parse and validate the signed manifest payload."""
        if not isinstance(value, dict):
            raise ReleaseValidationError("Manifest root must be an object", "MANIFEST_INVALID")
        version = value.get("version")
        parse_semver(version)
        compatibility = value.get("api_compatibility", value.get("api", value.get("api_version", {})))
        if isinstance(compatibility, int):
            compatibility = {"min": compatibility, "max": compatibility}
        if not isinstance(compatibility, dict):
            raise ReleaseValidationError("Manifest API compatibility is invalid", "COMPATIBILITY_INVALID")
        try:
            minimum = int(compatibility.get("min", compatibility.get("min_api", SUPPORTED_API_VERSION)))
            maximum = int(compatibility.get("max", compatibility.get("max_api", SUPPORTED_API_VERSION)))
        except (TypeError, ValueError) as error:
            raise ReleaseValidationError("Manifest API compatibility is invalid", "COMPATIBILITY_INVALID") from error
        if minimum > maximum:
            raise ReleaseValidationError("Manifest API compatibility range is invalid", "COMPATIBILITY_INVALID")
        raw_artifacts = value.get("artifacts")
        if isinstance(raw_artifacts, list):
            raw_artifacts = {
                item.get("name"): item
                for item in raw_artifacts
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
        if not isinstance(raw_artifacts, dict):
            raise ReleaseValidationError("Manifest artifacts are invalid", "MANIFEST_INVALID")
        artifacts: dict[str, ManifestArtifact] = {}
        for name, raw in raw_artifacts.items():
            if not isinstance(raw, dict):
                raise ReleaseValidationError("Manifest artifact is invalid", "MANIFEST_INVALID")
            filename = raw.get("filename")
            url = raw.get("url") or release_asset_url(version, filename)
            if not isinstance(filename, str) or not _SAFE_FILENAME.fullmatch(filename):
                raise ReleaseValidationError("Artifact filename is unsafe", "PATH_INVALID")
            validate_release_url(url, version, filename)
            sha256 = raw.get("sha256") or raw.get("sha-256")
            if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
                raise ReleaseValidationError("Artifact SHA-256 digest is invalid", "HASH_INVALID")
            artifacts[name] = ManifestArtifact(name, filename, url, sha256.lower())
        return cls(version, {"min": minimum, "max": maximum}, artifacts)

    def ensure_compatible(self, api_version: int = SUPPORTED_API_VERSION) -> None:
        """Require that this release can communicate with the installed API."""
        if not self.api_compatibility["min"] <= api_version <= self.api_compatibility["max"]:
            raise ReleaseValidationError("Release API compatibility is not supported", "INCOMPATIBLE_API")

    def artifact(self, name: str) -> ManifestArtifact:
        """Return a named artifact or fail closed."""
        try:
            return self.artifacts[name]
        except KeyError as error:
            raise ReleaseValidationError(f"Manifest artifact {name!r} is missing", "ARTIFACT_MISSING") from error


def validate_manifest(
    manifest: dict[str, Any],
    signature: str | bytes | None = None,
    api_version: int = SUPPORTED_API_VERSION,
    public_key: bytes | None = None,
) -> ReleaseManifest:
    """Verify signature, version, compatibility, URLs, and artifact metadata."""
    verify_manifest_signature(manifest, signature, public_key)
    parsed = ReleaseManifest.from_dict(manifest)
    parsed.ensure_compatible(api_version)
    return parsed


class FixedReleaseClient:
    """Fetch signed release metadata and fixed-endpoint artifacts."""

    def __init__(self, opener: urllib.request.OpenerDirector | None = None) -> None:
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def fetch_bytes(self, url: str) -> bytes:
        """Fetch one fixed endpoint with bounded, HTTPS-only GitHub redirects."""
        validate_release_url(url)
        try:
            with self.opener.open(urllib.request.Request(url, method="GET"), timeout=30) as response:
                final_url = response.geturl()
                _validate_download_location(final_url)
                return response.read()
        except (OSError, urllib.error.URLError) as error:
            raise ReleaseValidationError("Release endpoint could not be downloaded", "DOWNLOAD_FAILED") from error

    def fetch_manifest(self) -> tuple[dict[str, Any], str | bytes]:
        """Fetch and parse the signed manifest and detached signature."""
        try:
            with self.opener.open(urllib.request.Request(RELEASE_LATEST_API_URL, method="GET"), timeout=30) as response:
                if response.geturl() != RELEASE_LATEST_API_URL:
                    raise ReleaseValidationError("GitHub API redirected unexpectedly", "URL_NOT_ALLOWED")
                release = json.loads(response.read())
        except ReleaseValidationError:
            raise
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise ReleaseValidationError("Latest GitHub release could not be discovered", "DOWNLOAD_FAILED") from error
        tag = release.get("tag_name") if isinstance(release, dict) else None
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ReleaseValidationError("Latest GitHub release has no SemVer tag", "MANIFEST_INVALID")
        version = tag[1:]
        if not is_semver(version):
            raise ReleaseValidationError("Latest GitHub release has an invalid tag", "INVALID_VERSION")
        return self.fetch_versioned_manifest(version)

    def fetch_versioned_manifest(self, version: str) -> tuple[dict[str, Any], str | bytes]:
        """Fetch a manifest and detached signature for one exact version."""
        manifest = json.loads(self.fetch_bytes(release_asset_url(version, MANIFEST_NAME)))
        signature_bytes = self.fetch_bytes(release_asset_url(version, MANIFEST_SIGNATURE_NAME))
        try:
            signature: str | bytes = signature_bytes.decode("ascii").strip()
        except UnicodeDecodeError:
            signature = signature_bytes
        if not isinstance(manifest, dict):
            raise ReleaseValidationError("Manifest root must be an object", "MANIFEST_INVALID")
        return manifest, signature

    def download_artifact(self, artifact: ManifestArtifact, destination: Path) -> Path:
        """Download one verified artifact into a caller-owned staging path."""
        validate_release_url(artifact.url, filename=artifact.filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.fetch_bytes(artifact.url))
        verify_sha256(destination, artifact.sha256)
        return destination


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow only bounded redirects to known GitHub asset infrastructure."""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        from urllib.parse import urlsplit

        source_host = urlsplit(req.full_url).hostname
        target_host = urlsplit(newurl).hostname
        if (source_host == "api.github.com") != (target_host == "api.github.com"):
            raise urllib.error.HTTPError(req.full_url, code, "redirect crossed release/API boundary", headers, fp)
        _validate_download_location(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_download_location(url: str) -> None:
    """Validate a redirect target without permitting credential or HTTP downgrade."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ReleaseValidationError("Release redirect target has an invalid port", "URL_NOT_ALLOWED") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in GITHUB_ASSET_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ReleaseValidationError("Release redirect target is not an allowed GitHub asset host", "URL_NOT_ALLOWED")
    if parsed.hostname == "github.com":
        validate_release_url(url)
