# VPN Gateway Pi

Dieses Repository erweitert das vorhandene Raspberry-Pi-WireGuard-Gateway um eine sichere Verwaltungs-CLI und eine Windows-GUI. Die bestehende Profil- und Firewalllogik in `main.py` bleibt der technische Regelkern.

## Komponenten

- `vpn_gateway`: Pi-Backend und `vpn-gateway-cli`; alle Antworten sind JSON.
- `vpn_gateway_gui` und `gui_main.py`: PySide6-Desktop-Anwendung für Windows.
- `pi/scripts/install.sh`: installiert die Verwaltungsoberfläche, migriert den aktuellen WireGuard-Peer und aktiviert auf Wunsch den Restore-Dienst.
- `pi/scripts/uninstall.sh`: stellt die beim Aktivieren gesicherten Legacy-Dateien wieder her und entfernt die Verwaltungsoberfläche.

## Entwicklung

```powershell
uv sync --extra dev --extra gui
uv run python -m vpn_gateway.cli version --json
uv run python gui_main.py
```

Die GUI benötigt einen SSH-Schlüssel, eine vorhandene `known_hosts`-Datei und den SHA256-Fingerprint des Pi-Host-Keys. Sie akzeptiert keine freien Shell- oder Firewall-Befehle.

## Pi-Installation

Zunächst nur installieren und den aktuellen Peer in `migrated-current` übernehmen:

```bash
sudo ./pi/scripts/install.sh
```

Nach erfolgreichen Pi-Integrationstests aktiviert der folgende Aufruf den Restore-Dienst:

```bash
sudo ./pi/scripts/install.sh --activate
```

Die sudo-Härtung ist bewusst getrennt und folgt erst nach der Abnahme:

```bash
sudo ./pi/scripts/install.sh --harden-sudo
```

Auf `pi5-marcel` ersetzt dieser Schritt die geprüfte Regel `/etc/sudoers.d/010_pi-nopasswd`; deren Backup verbleibt als root-lesbare Datei mit dem Suffix `.vpn-gateway-backup`. `--activate` ersetzt die vorhandene `vpn-gateway.service` durch einen Oneshot-Reload-Dienst und legt davor eine Sicherung mit dem Suffix `.pre-vpn-gateway-cli` an. Ebenso wird `main.py` nur nach vorheriger Sicherung ersetzt. `devices.json` und `config.json` bleiben unverändert.

### Optionales VPN für Pi-lokale Dienste

JDownloader und andere Prozesse, die direkt auf dem Pi laufen, sind keine
Gateway-Geräteprofile. Für diesen Traffic kann in der Pi-eigenen
`config.json` der Block `host_routing` aktiviert werden:

```json
"host_routing": {
  "enabled": true,
  "source_address": "10.0.0.100",
  "lan_interface": "eth0",
  "lan_gateway": "10.0.0.138",
  "routing_table": 101,
  "rule_priority": 1000
}
```

Der Restore-Dienst setzt dann die fest reservierte Policy-Tabelle `101` mit LAN-Route,
WireGuard-Endpoint-Ausnahme und `default dev wg0`. Der Endpoint wird bei
Restore und VPN-Serverwechsel aus der aktiven WireGuard-Konfiguration
übernommen. Die Option ist standardmäßig deaktiviert; fehlt der Block, bleibt
das bisherige Routing unverändert. Wird ein zuvor aktivierter Block explizit
deaktiviert, entfernt der Restore-Dienst die von ihm verwaltete Regel und
seine Routen.

## Deinstallation

```bash
sudo ./pi/scripts/uninstall.sh
```

Der Uninstaller stellt, sofern vorhanden, die gesicherten Versionen von `vpn-gateway.service` und `main.py` wieder her, lädt systemd neu und startet den Legacy-Dienst. Die verwalteten Server- und Zustandsdaten unter `/opt/vpn-gateway` bleiben erhalten. Die frühere breite `NOPASSWD`-Regel wird aus Sicherheitsgründen nicht automatisch wiederhergestellt; ihr root-lesbares Backup bleibt bestehen.
