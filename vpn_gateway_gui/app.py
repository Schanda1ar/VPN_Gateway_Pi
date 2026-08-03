"""PySide6 desktop UI for the fixed VPN gateway management API."""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .gateway_client import GatewayClient, RemoteGatewayError, SettingsService, SshSettings


class TaskSignals(QObject):
    """Signals emitted by a background gateway request."""

    completed = Signal(object)
    failed = Signal(object)


class GatewayTask(QRunnable):
    """Run one blocking SSH request outside the Qt UI thread."""

    def __init__(self, operation: Callable[[], dict]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals()

    def run(self) -> None:
        """Deliver the operation result or exception to the UI thread."""
        try:
            self.signals.completed.emit(self.operation())
        except Exception as error:
            self.signals.failed.emit(error)


class GatewayController(QObject):
    """Central asynchronous controller shared by all GUI pages."""

    busy_changed = Signal(bool)

    def __init__(self, get_client: Callable[[], GatewayClient]) -> None:
        super().__init__()
        self.get_client = get_client
        self.pool = QThreadPool.globalInstance()
        self.busy = 0
        self._active_tasks: dict[int, GatewayTask] = {}

    def request(
        self,
        operation: Callable[[GatewayClient], dict],
        completed: Callable[[dict], None],
        failed: Callable[[Exception], bool | None] | None = None,
    ) -> None:
        """Run a GatewayClient method while preventing competing UI actions."""
        self.busy += 1
        self.busy_changed.emit(True)
        task = GatewayTask(lambda: operation(self.get_client()))
        task_id = id(task)
        self._active_tasks[task_id] = task
        task.signals.completed.connect(lambda value: self._complete(task_id, value, completed))
        task.signals.failed.connect(lambda error: self._failed(task_id, error, failed))
        self.pool.start(task)

    def _complete(self, task_id: int, result: dict, callback: Callable[[dict], None]) -> None:
        self._finish_task(task_id)
        callback(result)

    def _failed(self, task_id: int, error: Exception, callback: Callable[[Exception], bool | None] | None) -> None:
        self._finish_task(task_id)
        if callback and callback(error):
            return
        if isinstance(error, RemoteGatewayError):
            details = error.details.get("stderr", "").strip()
            message = str(error) if not details else f"{error}\n\n{details}"
            QMessageBox.critical(None, error.code, message)
        else:
            QMessageBox.critical(None, "Fehler", str(error))

    def _finish_task(self, task_id: int) -> None:
        """Release a completed worker only after its UI-thread signal is processed."""
        self._active_tasks.pop(task_id, None)
        self.busy = max(0, self.busy - 1)
        self.busy_changed.emit(self.busy > 0)


class ServerDialog(QDialog):
    """Validated editor for the public server fields accepted by the Pi."""

    def __init__(self, server: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VPN-Server")
        server = server or {}
        layout = QFormLayout(self)
        self.identifier = QLineEdit(server.get("id", ""))
        self.name = QLineEdit(server.get("name", ""))
        self.country = QLineEdit(server.get("country", ""))
        self.city = QLineEdit(server.get("city", ""))
        self.endpoint = QLineEdit(server.get("endpoint", ""))
        self.public_key = QLineEdit(server.get("public_key", ""))
        self.keepalive = QSpinBox()
        self.keepalive.setRange(0, 65535)
        self.keepalive.setValue(server.get("persistent_keepalive") or 0)
        self.enabled = QComboBox()
        self.enabled.addItems(["Aktiv", "Deaktiviert"])
        self.enabled.setCurrentIndex(0 if server.get("enabled", True) else 1)
        layout.addRow("ID", self.identifier)
        layout.addRow("Name", self.name)
        layout.addRow("Land", self.country)
        layout.addRow("Stadt", self.city)
        layout.addRow("Endpoint", self.endpoint)
        layout.addRow("Public Key", self.public_key)
        layout.addRow("Keepalive", self.keepalive)
        layout.addRow("Status", self.enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def payload(self) -> dict:
        """Return a request body that remains Pi-side validated."""
        return {
            "id": self.identifier.text().strip(),
            "name": self.name.text().strip(),
            "country": self.country.text().strip(),
            "city": self.city.text().strip(),
            "endpoint": self.endpoint.text().strip(),
            "public_key": self.public_key.text().strip(),
            "persistent_keepalive": self.keepalive.value(),
            "enabled": self.enabled.currentIndex() == 0,
        }


class MainWindow(QMainWindow):
    """Top-level navigation for dashboard, servers, devices, diagnostics, and settings."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VPN Gateway Manager")
        self.settings_service = SettingsService()
        self.settings = self.settings_service.load()
        self.connection_verified = False
        self.controller = GatewayController(lambda: GatewayClient(self.settings))
        self.controller.busy_changed.connect(self._set_busy)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_dashboard()
        self._build_servers()
        self._build_devices()
        self._build_diagnostics()
        self._build_settings()
        self.statusBar().showMessage("Bitte zuerst die SSH-Verbindung prüfen")

    def _build_dashboard(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.connection_label = QLabel("Nicht geprüft")
        self.vpn_label = QLabel("–")
        self.server_label = QLabel("–")
        self.handshake_label = QLabel("–")
        self.device_count_label = QLabel("–")
        layout.addRow("Pi-Verbindung", self.connection_label)
        layout.addRow("WireGuard", self.vpn_label)
        layout.addRow("Aktiver Server", self.server_label)
        layout.addRow("Letzter Handshake", self.handshake_label)
        layout.addRow("Geräte", self.device_count_label)
        refresh = QPushButton("Aktualisieren")
        refresh.clicked.connect(self.refresh_dashboard)
        layout.addRow(refresh)
        self.tabs.addTab(page, "Dashboard")

    def _build_servers(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.server_table = QTableWidget(0, 6)
        self.server_table.setHorizontalHeaderLabels(["ID", "Name", "Land", "Stadt", "Endpoint", "Aktiv"])
        layout.addWidget(self.server_table)
        buttons = QHBoxLayout()
        for label, handler in (("Aktualisieren", self.refresh_servers), ("Hinzufügen", self.add_server), ("Bearbeiten", self.edit_server), ("Löschen", self.delete_server), ("Aktivieren", self.activate_server)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.tabs.addTab(page, "VPN-Server")

    def _build_devices(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.device_table = QTableWidget(0, 4)
        self.device_table.setHorizontalHeaderLabels(["ID", "Name", "IP-Adresse", "Profil"])
        layout.addWidget(self.device_table)
        buttons = QHBoxLayout()
        refresh = QPushButton("Aktualisieren")
        refresh.clicked.connect(self.refresh_devices)
        profile = QPushButton("Profil ändern")
        profile.clicked.connect(self.change_profile)
        details = QPushButton("Regeln anzeigen")
        details.clicked.connect(self.show_rules)
        buttons.addWidget(refresh)
        buttons.addWidget(profile)
        buttons.addWidget(details)
        layout.addLayout(buttons)
        self.tabs.addTab(page, "Geräte")

    def _build_diagnostics(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.diagnostics_text = QTextEdit()
        self.diagnostics_text.setReadOnly(True)
        layout.addWidget(self.diagnostics_text)
        self.tabs.addTab(page, "Diagnose")

    def _build_settings(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.host = QLineEdit(self.settings.host)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(self.settings.port)
        self.username = QLineEdit(self.settings.username)
        self.private_key = QLineEdit(self.settings.private_key_path)
        self.known_hosts = QLineEdit(self.settings.known_hosts_path)
        self.fingerprint = QLineEdit(self.settings.host_key_fingerprint)
        self.ssh_status = QLabel("Nicht geprüft")
        layout.addRow("Host", self.host)
        layout.addRow("SSH-Port", self.port)
        layout.addRow("Benutzer", self.username)
        layout.addRow("Privater Schlüssel", self.private_key)
        layout.addRow("known_hosts", self.known_hosts)
        layout.addRow("Host-Key SHA256", self.fingerprint)
        layout.addRow("SSH-Status", self.ssh_status)
        buttons = QHBoxLayout()
        save = QPushButton("Speichern")
        save.clicked.connect(lambda: self.save_settings())
        test = QPushButton("Verbindung testen")
        test.clicked.connect(self.test_connection)
        self.install_button = QPushButton("CLI und Gateway installieren/aktualisieren")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_gateway)
        buttons.addWidget(save)
        buttons.addWidget(test)
        layout.addRow(buttons)
        layout.addRow(self.install_button)
        for field in (self.host, self.username, self.private_key, self.known_hosts, self.fingerprint):
            field.textChanged.connect(self._connection_settings_changed)
        self.port.valueChanged.connect(self._connection_settings_changed)
        self.tabs.addTab(page, "Einstellungen")

    def _set_busy(self, busy: bool) -> None:
        """Show request progress without blocking navigation to connection settings."""
        self.statusBar().showMessage("Aktualisiere Gateway-Daten …" if busy else "Bereit")
        self.install_button.setEnabled(self.connection_verified and not busy)

    def _connection_settings_changed(self, *_: object) -> None:
        """Require a new SSH probe whenever connection-relevant input changes."""
        self.connection_verified = False
        self.ssh_status.setText("Nicht geprüft")
        self.install_button.setEnabled(False)

    def _selected_server(self) -> dict | None:
        row = self.server_table.currentRow()
        return self.server_table.item(row, 0).data(256) if row >= 0 and self.server_table.item(row, 0) else None

    def _selected_device(self) -> dict | None:
        row = self.device_table.currentRow()
        return self.device_table.item(row, 0).data(256) if row >= 0 and self.device_table.item(row, 0) else None

    def refresh_all(self) -> None:
        """Refresh dashboard and management pages after start or a change."""
        self.refresh_dashboard()
        self.refresh_servers()
        self.refresh_devices()

    def refresh_dashboard(self) -> None:
        self.controller.request(lambda client: client.get_status(), self._show_status)

    def _show_status(self, response: dict) -> None:
        data = response
        self.connection_label.setText("Verbunden")
        self.vpn_label.setText("Aktiv" if data["vpn"]["interface_up"] else "Inaktiv")
        self.server_label.setText(data["vpn"].get("server_id") or "Nicht migriert")
        self.handshake_label.setText(str(data["vpn"].get("latest_handshake_epoch", 0)))
        self.device_count_label.setText(str(data["devices"]["total"]))

    def refresh_servers(self) -> None:
        self.controller.request(lambda client: client.list_servers(), self._show_servers)

    def _show_servers(self, response: dict) -> None:
        servers = response["servers"]
        self.server_table.setRowCount(len(servers))
        for row, server in enumerate(servers):
            for column, key in enumerate(("id", "name", "country", "city", "endpoint", "enabled")):
                item = QTableWidgetItem(str(server.get(key, "")))
                if column == 0:
                    item.setData(256, server)
                self.server_table.setItem(row, column, item)

    def add_server(self) -> None:
        dialog = ServerDialog(parent=self)
        if dialog.exec():
            self.controller.request(lambda client: client.add_server(dialog.payload()), lambda _: self.refresh_servers())

    def edit_server(self) -> None:
        server = self._selected_server()
        if not server:
            return
        dialog = ServerDialog(server, self)
        dialog.identifier.setReadOnly(True)
        if dialog.exec():
            payload = dialog.payload(); payload.pop("id")
            self.controller.request(lambda client: client.update_server(server["id"], payload), lambda _: self.refresh_servers())

    def delete_server(self) -> None:
        server = self._selected_server()
        if server and QMessageBox.question(self, "Server löschen", f"{server['name']} wirklich löschen?") == QMessageBox.Yes:
            self.controller.request(lambda client: client.delete_server(server["id"]), lambda _: self.refresh_servers())

    def activate_server(self) -> None:
        server = self._selected_server()
        if server:
            self.controller.request(lambda client: client.switch_server(server["id"]), lambda _: self.refresh_all())

    def refresh_devices(self) -> None:
        self.controller.request(lambda client: client.list_devices(), self._show_devices)

    def _show_devices(self, response: dict) -> None:
        devices = response["devices"]
        self.device_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            for column, key in enumerate(("id", "name", "ip_address", "profile")):
                item = QTableWidgetItem(str(device.get(key, "")))
                if column == 0:
                    item.setData(256, device)
                self.device_table.setItem(row, column, item)

    def change_profile(self) -> None:
        device = self._selected_device()
        if not device:
            return
        dialog = QDialog(self)
        layout = QFormLayout(dialog)
        selector = QComboBox(); selector.addItems(["Normal", "VPN", "Sicher"]); selector.setCurrentText(device["profile"])
        layout.addRow("Profil", selector)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            self.controller.request(lambda client: client.set_device_profile(device["id"], selector.currentText()), lambda _: self.refresh_all())

    def show_rules(self) -> None:
        device = self._selected_device()
        if device:
            self.controller.request(lambda client: client.get_effective_rules(device["id"]), self._show_rules)

    def _show_rules(self, response: dict) -> None:
        self.diagnostics_text.setPlainText(json.dumps(response, indent=2, ensure_ascii=False))
        self.tabs.setCurrentIndex(3)

    def save_settings(self, show_confirmation: bool = True) -> None:
        self.settings = SshSettings(
            host=self.host.text().strip(), port=self.port.value(), username=self.username.text().strip(),
            private_key_path=self.private_key.text().strip(), known_hosts_path=self.known_hosts.text().strip(),
            host_key_fingerprint=self.fingerprint.text().strip(),
        )
        self.settings_service.save(self.settings)
        if show_confirmation:
            QMessageBox.information(self, "Einstellungen", "Einstellungen gespeichert")

    def test_connection(self) -> None:
        self.save_settings(show_confirmation=False)
        self.connection_verified = False
        self.ssh_status.setText("Wird geprüft …")
        self.install_button.setEnabled(False)
        self.controller.request(
            lambda client: client.probe_connection(),
            self._connection_succeeded,
            self._connection_failed,
        )

    def _connection_succeeded(self, _: dict) -> None:
        self.connection_verified = True
        self.connection_label.setText("SSH verbunden")
        self.ssh_status.setText("Verbunden")
        self.install_button.setEnabled(True)
        QMessageBox.information(
            self,
            "SSH-Verbindung",
            "Die SSH-Verbindung steht. Die Gateway-Einrichtung ist jetzt freigeschaltet.",
        )

    def _connection_failed(self, _: Exception) -> None:
        self.connection_verified = False
        self.connection_label.setText("Nicht verbunden")
        self.ssh_status.setText("Fehlgeschlagen")
        self.install_button.setEnabled(False)

    def install_gateway(self) -> None:
        if not self.connection_verified:
            return
        answer = QMessageBox.question(
            self,
            "Gateway einrichten",
            "Die CLI sowie alle benötigten Gateway-Skripte, Dienste und Ordner werden auf dem Pi installiert oder aktualisiert. "
            "Vorhandene Geräte- und Gateway-Konfigurationen bleiben erhalten. Fortfahren?",
        )
        if answer != QMessageBox.Yes:
            return
        self.statusBar().showMessage("Gateway wird eingerichtet …")
        self.controller.request(
            lambda client: client.install_gateway(),
            self._install_completed,
            self._install_failed,
        )

    def _install_failed(self, error: Exception) -> bool:
        """Request a transient sudo password only when the Pi requires it."""
        if not isinstance(error, RemoteGatewayError) or error.code != "SUDO_PASSWORD_REQUIRED":
            return False
        password, accepted = QInputDialog.getText(
            self,
            "sudo-Berechtigung benötigt",
            "sudo-Passwort des Pi-Benutzers (wird nicht gespeichert):",
            QLineEdit.Password,
        )
        if not accepted:
            self.statusBar().showMessage("Installation abgebrochen")
            return True
        if not password:
            QMessageBox.warning(self, "sudo-Passwort", "Für die Installation wurde kein Passwort eingegeben.")
            return True
        self.statusBar().showMessage("CLI und Gateway werden installiert …")
        self.controller.request(
            lambda client: client.install_gateway(password),
            self._install_completed,
        )
        return True

    def _install_completed(self, response: dict) -> None:
        self.connection_label.setText("Verbunden und eingerichtet")
        self.ssh_status.setText("Gateway eingerichtet")
        QMessageBox.information(
            self,
            "Gateway eingerichtet",
            f"Die Einrichtung wurde erfolgreich abgeschlossen (CLI API {response['api_version']}).",
        )
        self.refresh_all()


def main() -> int:
    """Start the Windows desktop application."""
    application = QApplication([])
    window = MainWindow()
    window.resize(920, 600)
    window.show()
    return application.exec()
