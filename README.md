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

Auf der Seite **Einstellungen** wird der Button **CLI und Gateway installieren/aktualisieren** erst nach einem erfolgreichen SSH-Test freigeschaltet.

Der Ablauf ist fest vorgegeben und läuft im Hintergrund:

1. Die GUI prüft SSH unabhängig davon, ob `vpn-gateway-cli` bereits installiert ist.
2. Die GUI prüft die installierte CLI-Version. Eine fehlende oder ältere CLI wird aus dem fest im GUI-Build enthaltenen Installationspaket installiert beziehungsweise aktualisiert.
3. Das Paket wird als normaler SSH-Benutzer in ein privates temporäres Verzeichnis übertragen.
4. Wenn der Pi keine kennwortlose Installation erlaubt, fragt die GUI das sudo-Passwort verdeckt ab. Es wird nur für diesen Installationsprozess im Arbeitsspeicher gehalten, nicht gespeichert und nicht als Kommandozeilenargument übertragen.
5. `install.sh --activate --harden-sudo` installiert Skripte und Dienste, migriert den aktiven WireGuard-Peer, aktiviert den Restore-Dienst und begrenzt anschließend die sudo-Regel wieder auf die feste CLI.
6. Ist bereits dieselbe oder eine neuere kompatible CLI installiert, wird `system setup` idempotent ausgeführt. Dabei werden die verwalteten Ordner geprüft beziehungsweise angelegt und VPN- sowie Geräteprofile erneut angewendet.

`config.json` und `devices.json` des vorhandenen Gateways werden dabei nicht ersetzt. Der Benutzer muss zu keinem Zeitpunkt ein Terminal auf dem Pi öffnen. Nach der Härtung laufen erneute Einrichtungen derselben Version über den begrenzten CLI-Befehl `system setup`; eine spätere CLI-Aktualisierung kann wieder vollständig aus der GUI erfolgen und fragt bei Bedarf erneut nach dem sudo-Passwort.

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
