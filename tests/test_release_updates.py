"""Focused tests for trusted release metadata and update hand-offs."""

from __future__ import annotations

import base64
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from vpn_gateway.release import (
    ReleaseValidationError,
    canonical_manifest_bytes,
    safe_extract_archive,
    sign_ed25519,
    validate_manifest,
    verify_ed25519,
    verify_sha256,
)
from vpn_gateway.release import _NoRedirect, _validate_download_location
from vpn_gateway.paths import GatewayPaths
from vpn_gateway.updater import PiUpdater, UpdateError
from vpn_gateway_gui.app import MainWindow
from vpn_gateway_gui.gateway_client import RemoteGatewayError
from vpn_gateway_gui.update_helper import GuiUpdateError, UpdateHelperContract, WindowsUpdateHelper


SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
TEST_PUBLIC_KEY = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")


def test_ed25519_signature_and_manifest_compatibility() -> None:
    """The detached signature covers canonical metadata and API compatibility."""
    artifact = {
        "filename": "gateway.whl",
        "url": "https://github.com/Schanda1ar/VPN_Gateway_Pi/releases/download/v0.2.0/gateway.whl",
        "sha256": "0" * 64,
    }
    manifest = {"version": "0.2.0", "api_compatibility": {"min": 1, "max": 1}, "artifacts": {"pi_wheel": artifact}}
    signature = sign_ed25519(canonical_manifest_bytes(manifest), SEED)
    assert verify_ed25519(canonical_manifest_bytes(manifest), signature, TEST_PUBLIC_KEY)
    validate_manifest(manifest, base64.b64encode(signature).decode(), public_key=TEST_PUBLIC_KEY)
    manifest["version"] = "0.2.1"
    assert not verify_ed25519(canonical_manifest_bytes(manifest), signature, TEST_PUBLIC_KEY)


def test_production_key_boundary_fails_closed_until_provisioned() -> None:
    """The production verifier cannot silently use a test-vector trust anchor."""
    with pytest.raises(ReleaseValidationError) as error:
        from vpn_gateway.release import provisioned_public_key

        provisioned_public_key()
    assert error.value.code == "KEY_NOT_PROVISIONED"


def test_release_redirect_policy_is_bounded_and_host_restricted() -> None:
    """Only HTTPS GitHub asset infrastructure may receive bounded redirects."""
    _validate_download_location("https://release-assets.githubusercontent.com/release/file?token=x")
    assert _NoRedirect.max_redirections == 3
    with pytest.raises(ReleaseValidationError):
        _validate_download_location("https://evil.example/release/file")
    with pytest.raises(ReleaseValidationError):
        _validate_download_location("http://release-assets.githubusercontent.com/release/file")


def test_release_workflow_build_contract_is_pinned() -> None:
    """Release automation tests GUI extras and tracks the canonical spec."""
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "uv run --extra dev --extra gui pyinstaller" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "needs: [prepare, windows-gui]" in workflow
    assert "Require a strictly newer SemVer bump" in workflow
    assert "git push origin \"$TAG\"" not in workflow
    assert Path("VPN-Gateway-Manager.spec").exists()
    assert "!VPN-Gateway-Manager.spec" in Path(".gitignore").read_text(encoding="utf-8")


def test_gui_disables_incompatible_update() -> None:
    """An incompatible release is displayed but can never reach apply_update."""

    class Label:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value

    class Button:
        def __init__(self) -> None:
            self.enabled = True

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setDisabled(self, value: bool) -> None:
            self.enabled = not value

    class Controller:
        called = False

        def request(self, *_args) -> None:
            self.called = True

    window = MainWindow.__new__(MainWindow)
    window.pi_version_label = Label()
    window.available_version_label = Label()
    window.update_status_label = Label()
    window.install_update_button = Button()
    window._available_update_version = None
    window.controller = Controller()

    window._show_update_status({"installed_version": "1.0.0", "available_version": "1.1.0"})
    assert window.install_update_button.enabled
    window._show_update_error(RemoteGatewayError("INCOMPATIBLE_API", "API mismatch"))

    assert window.available_version_label.value == "Nicht kompatibel"
    assert window.update_status_label.value == "Release nicht kompatibel"
    assert not window.install_update_button.enabled
    window.install_update()
    assert not window.controller.called


def test_incompatible_api_release_is_not_accepted() -> None:
    """A valid signature cannot bypass the API compatibility gate."""
    manifest = {
        "version": "0.2.0",
        "api_compatibility": {"min": 2, "max": 2},
        "artifacts": {},
    }
    signature = sign_ed25519(canonical_manifest_bytes(manifest), SEED)
    with pytest.raises(ReleaseValidationError) as error:
        validate_manifest(manifest, signature, public_key=TEST_PUBLIC_KEY)
    assert error.value.code == "INCOMPATIBLE_API"


def test_safe_archive_rejects_traversal(tmp_path) -> None:
    """Archives cannot write outside the private staging directory."""
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../outside.txt", "no")
    with pytest.raises(ReleaseValidationError) as error:
        safe_extract_archive(archive, tmp_path / "extract")
    assert error.value.code == "ARCHIVE_UNSAFE"


def test_hash_verification_and_helper_contract(tmp_path) -> None:
    """Hash checks and the helper argv keep updates local and versioned."""
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"artifact")
    assert verify_sha256(artifact, hashlib.sha256(b"artifact").hexdigest())
    with pytest.raises(ReleaseValidationError):
        verify_sha256(artifact, "f" * 64)
    contract = UpdateHelperContract(
        artifact,
        "1.2.3",
        tmp_path / "install",
        "VPN-Gateway-Manager.exe",
        hashlib.sha256(b"artifact").hexdigest(),
        health_file=tmp_path / "ready",
    )
    assert contract.argv()[:4] == ["--package", str(artifact), "--version", "1.2.3"]
    assert "--sha256" in contract.argv()
    assert "--health-file" in contract.argv()
    with pytest.raises(GuiUpdateError, match="health file"):
        WindowsUpdateHelper().install(
            artifact,
            "1.2.3",
            tmp_path / "install",
            "VPN-Gateway-Manager.exe",
            expected_sha256=hashlib.sha256(b"artifact").hexdigest(),
        )


def test_gui_update_rolls_back_when_startup_health_is_missing(tmp_path) -> None:
    """The helper restores the old folder and removes the failed target."""
    package = tmp_path / "gui.zip"
    with zipfile.ZipFile(package, "w") as handle:
        handle.writestr("VPN-Gateway-Manager.exe", b"new")
    install_root = tmp_path / "install"
    current = install_root / "current"
    current.mkdir(parents=True)
    (current / "VPN-Gateway-Manager.exe").write_bytes(b"old")
    health_file = tmp_path / "ready"
    helper = WindowsUpdateHelper(launcher=lambda *_: object(), health_checker=lambda _: False, wait_seconds=0)

    with pytest.raises(GuiUpdateError, match="rolled back"):
        helper.install(
            package,
            "1.2.3",
            install_root,
            "VPN-Gateway-Manager.exe",
            expected_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            health_file=health_file,
        )

    assert (current / "VPN-Gateway-Manager.exe").read_bytes() == b"old"
    assert not (install_root / "versions" / "1.2.3").exists()
    with pytest.raises(GuiUpdateError):
        helper.install(package, "1.2.4", install_root, "VPN-Gateway-Manager.exe", health_file=health_file)


def test_pi_update_rolls_back_and_allows_retry(tmp_path) -> None:
    """A failed activation removes only the candidate and preserves data."""
    wheel_bytes = b"verified wheel"
    runtime_buffer = io.BytesIO()
    with tarfile.open(fileobj=runtime_buffer, mode="w:gz") as archive:
        payload = b"print('release')\n"
        info = tarfile.TarInfo("runtime/main.py")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    runtime_bytes = runtime_buffer.getvalue()
    manifest = {
        "version": "1.2.3",
        "api_compatibility": {"min": 1, "max": 1},
        "artifacts": {
            "pi_wheel": {"filename": "gateway.whl", "url": "https://github.com/Schanda1ar/VPN_Gateway_Pi/releases/download/v1.2.3/gateway.whl", "sha256": hashlib.sha256(wheel_bytes).hexdigest()},
            "pi_runtime": {"filename": "runtime.tar.gz", "url": "https://github.com/Schanda1ar/VPN_Gateway_Pi/releases/download/v1.2.3/runtime.tar.gz", "sha256": hashlib.sha256(runtime_bytes).hexdigest()},
        },
    }
    signature = sign_ed25519(canonical_manifest_bytes(manifest), SEED)

    class FakeReleaseClient:
        def fetch_versioned_manifest(self, _version):
            return manifest, signature

        def fetch_manifest(self):
            return manifest, signature

        def download_artifact(self, artifact, destination):
            destination.write_bytes(wheel_bytes if artifact.name == "pi_wheel" else runtime_bytes)
            return destination

    paths = GatewayPaths(tmp_path / "legacy", tmp_path / "config", tmp_path / "state", tmp_path / "locks")
    paths.legacy_dir.mkdir()
    (paths.legacy_config).write_text("{}", encoding="utf-8")
    (paths.legacy_devices).write_text("{}", encoding="utf-8")
    paths.releases_dir.mkdir(parents=True)
    old = paths.releases_dir / "1.0.0"
    old.mkdir()
    (old / "version.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    try:
        paths.current_release.symlink_to(old.name, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    healthy = False
    updater = PiUpdater(paths, FakeReleaseClient(), public_key=TEST_PUBLIC_KEY, health_checker=lambda _: healthy)
    updater._install_wheel = lambda *_args: None
    with pytest.raises(UpdateError) as error:
        updater.apply("1.2.3")
    assert error.value.code == "UPDATE_ROLLED_BACK"
    assert updater.installed_version() == "1.0.0"
    assert paths.legacy_devices.read_text(encoding="utf-8") == "{}"
    assert not (paths.releases_dir / "1.2.3").exists()

    healthy = True
    assert updater.apply("1.2.3")["status"] == "updated"
