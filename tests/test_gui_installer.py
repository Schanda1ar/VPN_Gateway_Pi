"""Tests for the GUI-side fixed SSH bootstrap without requiring a Raspberry Pi."""

from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from vpn_gateway_gui.gateway_client import GatewayClient, RemoteGatewayError, SSH_PROBE_MARKER, SshClient, SshSettings
from vpn_gateway_gui.installer import EXECUTABLE_BUNDLE_FILES, INSTALL_BUNDLE_FILES, build_install_bundle


def test_install_bundle_contains_only_allowlisted_files() -> None:
    payload = build_install_bundle(Path(__file__).resolve().parent.parent)

    with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()

    assert [member.name for member in members] == list(INSTALL_BUNDLE_FILES)
    assert all(member.isfile() and not member.name.startswith("/") and ".." not in Path(member.name).parts for member in members)
    for member in members:
        expected_mode = 0o755 if member.name in EXECUTABLE_BUNDLE_FILES else 0o644
        assert member.mode == expected_mode


def ssh_client(tmp_path: Path) -> SshClient:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("pi.example ssh-ed25519 AAAA\n", encoding="utf-8")
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("test key", encoding="utf-8")
    settings = SshSettings(
        host="pi.example",
        username="pi",
        private_key_path=str(private_key),
        known_hosts_path=str(known_hosts),
        host_key_fingerprint="SHA256:test",
    )
    client = SshClient(settings)
    client._verify_fingerprint = lambda: None  # type: ignore[method-assign]
    return client


def test_probe_uses_plain_ssh_without_gateway_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(args, 0, f"{SSH_PROBE_MARKER}\n".encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ssh_client(tmp_path).probe()

    assert result["connected"] is True
    assert captured["input"] is None
    assert "/usr/local/bin/vpn-gateway-cli" not in captured["args"][-1]


def test_install_transfers_binary_bundle_then_checks_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], bytes | None]] = []
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess([], 0, b"/tmp/vpn-gateway-gui.Abc123\n", b""),
            subprocess.CompletedProcess([], 0, b"VPN Gateway CLI installed.\n", b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess(
                [],
                0,
                b'{"success": true, "api_version": 2, "application_version": "0.2.0"}\n',
                b"",
            ),
        )
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs.get("input")))  # type: ignore[arg-type]
        response = next(responses)
        return subprocess.CompletedProcess(args, response.returncode, response.stdout, response.stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    bundle = b"fixed tar payload"
    result = ssh_client(tmp_path).install_gateway(bundle)

    assert calls[0][0][-1] == "sudo -n /bin/true"
    assert calls[1][1] == bundle
    assert "mktemp -d /tmp/vpn-gateway-gui.XXXXXX" in calls[1][0][-1]
    assert calls[2][1] is None
    assert "sudo -n /bin/sh -c" in calls[2][0][-1]
    assert calls[3][0][-1].startswith("rm -rf -- /tmp/vpn-gateway-gui.")
    assert result == {"success": True, "installed": True, "api_version": 2, "application_version": "0.2.0"}


def test_install_requests_sudo_password_without_uploading_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args, 1, b"", b"sudo: a password is required\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RemoteGatewayError) as error:
        ssh_client(tmp_path).install_gateway(b"payload")

    assert error.value.code == "SUDO_PASSWORD_REQUIRED"
    assert calls == 1


def test_install_uses_transient_password_only_on_sudo_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], bytes | None]] = []
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, b"/tmp/vpn-gateway-gui.Secret1\n", b""),
            subprocess.CompletedProcess([], 0, b"VPN Gateway CLI installed.\n", b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess(
                [],
                0,
                b'{"success": true, "api_version": 2, "application_version": "0.2.0"}\n',
                b"",
            ),
        )
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs.get("input")))  # type: ignore[arg-type]
        response = next(responses)
        return subprocess.CompletedProcess(args, response.returncode, response.stdout, response.stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ssh_client(tmp_path).install_gateway(b"bundle", "correct horse")

    assert calls[0][1] == b"bundle"
    assert calls[1][1] == b"correct horse\n"
    assert "correct horse" not in " ".join(calls[1][0])
    assert calls[2][1] is None


def test_install_rejects_wrong_sudo_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, b"/tmp/vpn-gateway-gui.Wrong1\n", b""),
            subprocess.CompletedProcess([], 1, b"", b"Sorry, try again.\n"),
            subprocess.CompletedProcess([], 0, b"", b""),
        )
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: (lambda response: subprocess.CompletedProcess(args, response.returncode, response.stdout, response.stderr))(next(responses)),
    )

    with pytest.raises(RemoteGatewayError) as error:
        ssh_client(tmp_path).install_gateway(b"payload", "wrong")

    assert error.value.code == "SUDO_AUTHENTICATION_FAILED"


def test_gateway_client_reuses_restricted_cli_after_first_install(tmp_path: Path) -> None:
    client = GatewayClient(ssh_client(tmp_path).settings)
    calls: list[list[str]] = []
    def execute(command: list[str]) -> dict:
        calls.append(command)
        if command == ["version"]:
            return {"success": True, "api_version": 2, "application_version": "0.2.0"}
        return {"success": True, "configured": True, "api_version": 2}

    client.ssh.execute = execute  # type: ignore[method-assign]
    client.ssh.install_gateway = lambda bundle, password=None: pytest.fail("bootstrap must not run for an installed CLI")  # type: ignore[method-assign]

    result = client.install_gateway()

    assert calls == [["version"], ["system", "setup"]]
    assert result["configured"] is True


def test_gateway_client_bootstraps_when_cli_is_not_installed(tmp_path: Path) -> None:
    client = GatewayClient(ssh_client(tmp_path).settings)
    client.ssh.execute = lambda command: (_ for _ in ()).throw(RemoteGatewayError("REMOTE_COMMAND_FAILED", "missing"))  # type: ignore[method-assign]
    received: list[bytes] = []
    client.ssh.install_gateway = lambda bundle, password=None: received.append(bundle) or {"success": True, "installed": True, "api_version": 2}  # type: ignore[method-assign]

    result = client.install_gateway()

    assert result["installed"] is True
    assert received and received[0].startswith(b"\x1f\x8b")


def test_gateway_client_updates_old_cli_with_gui_supplied_password(tmp_path: Path) -> None:
    client = GatewayClient(ssh_client(tmp_path).settings)
    client.ssh.execute = lambda command: {"success": True, "api_version": 1, "application_version": "0.1.0"}  # type: ignore[method-assign]
    received: list[tuple[bytes, str | None]] = []
    client.ssh.install_gateway = lambda bundle, password=None: received.append((bundle, password)) or {"success": True, "installed": True, "api_version": 2}  # type: ignore[method-assign]

    result = client.install_gateway("pi sudo password")

    assert result["installed"] is True
    assert received[0][0].startswith(b"\x1f\x8b")
    assert received[0][1] == "pi sudo password"
