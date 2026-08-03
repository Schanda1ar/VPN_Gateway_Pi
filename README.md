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

### Ein-Klick-Einrichtung über die GUI

Auf der Seite **Einstellungen** wird der Button **Gateway vollständig einrichten** erst nach einem erfolgreichen SSH-Test freigeschaltet.

Der Ablauf ist fest vorgegeben und läuft im Hintergrund:

1. Die GUI prüft SSH unabhängig davon, ob `vpn-gateway-cli` bereits installiert ist.
2. Bei einer vorhandenen CLI wird `system setup` idempotent ausgeführt. Dabei werden die verwalteten Ordner geprüft beziehungsweise angelegt und VPN- sowie Geräteprofile erneut angewendet.
3. Auf einem noch nicht eingerichteten Pi überträgt die GUI ausschließlich die fest im Build enthaltenen Projektdateien in ein temporäres Verzeichnis.
4. `install.sh --activate --harden-sudo` installiert Skripte und Dienste, migriert den aktiven WireGuard-Peer, aktiviert den Restore-Dienst und begrenzt anschließend die sudo-Regel wieder auf die feste CLI.

`config.json` und `devices.json` des vorhandenen Gateways werden dabei nicht ersetzt. Für die erstmalige Installation muss der SSH-Benutzer `sudo -n` verwenden dürfen. Nach der Härtung laufen erneute Klicks über den begrenzten CLI-Befehl `system setup` und benötigen keine freie Root-Shell.

Für einen Windows-Single-File-Build inklusive des geprüften Pi-Payloads:

```powershell
uv run pyinstaller --clean vpn_gateway_gui.spec
```

Die fertige Datei liegt anschließend unter `dist/VpnGatewayManager.exe`.

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

## Deinstallation

```bash
sudo ./pi/scripts/uninstall.sh
```

Der Uninstaller stellt, sofern vorhanden, die gesicherten Versionen von `vpn-gateway.service` und `main.py` wieder her, lädt systemd neu und startet den Legacy-Dienst. Die verwalteten Server- und Zustandsdaten unter `/opt/vpn-gateway` bleiben erhalten. Die frühere breite `NOPASSWD`-Regel wird aus Sicherheitsgründen nicht automatisch wiederhergestellt; ihr root-lesbares Backup bleibt bestehen.
