# Architektur – Raspberry-Pi-VPN-Gateway mit PC-GUI, Serververwaltung und Gerätesteuerung

## 1. Zweck dieses Dokuments

Dieses Dokument beschreibt den anfänglichen Ist-Stand des bestehenden Raspberry-Pi-VPN-Gateways, die Architekturvorgaben und den umgesetzten Erweiterungsstand.

Die Erweiterung umfasst von Beginn an:

1. eine Windows-PC-GUI mit Python und PySide6,
2. den schnellen Wechsel des aktiven WireGuard-VPN-Servers,
3. das Hinzufügen, Bearbeiten und Löschen von VPN-Servern über die GUI,
4. die Anzeige aller bekannten Geräte,
5. die Anzeige des aktiven Profils und der daraus resultierenden Regeln je Gerät,
6. das Umschalten eines Geräts zwischen Profilen wie `Normal`, `VPN` und `Sicher`,
7. eine sichere SSH-Schnittstelle zwischen GUI und Raspberry Pi,
8. Health-Checks, Rollback und persistente Zustände.

Das Dokument dient als Startpunkt für einen Codex- oder anderen Coding-Agenten.

Der Agent soll den vorhandenen Code zuerst analysieren und das bestehende Projekt erweitern. Funktionierende Gateway-, Routing-, Firewall- und Profil-Logik soll nicht ohne zwingenden Grund neu geschrieben werden.

---

## 2. Zielbild

Der Raspberry Pi bleibt das zentrale Netzwerk-Gateway.

Eine neue Desktop-GUI auf dem Windows-PC dient als Verwaltungsoberfläche für:

- Verbindung zum Raspberry Pi,
- VPN-Status,
- Auswahl des aktiven VPN-Servers,
- Verwaltung der verfügbaren VPN-Server,
- Übersicht aller bekannten Geräte,
- Änderung des Geräteprofils,
- Anzeige der effektiven Routing-, DNS- und Firewallwirkung je Gerät,
- Diagnose von WireGuard, Routing und Profilregeln.

Der Endpoint-Wechsel soll möglichst ohne Neustart von `wg0` erfolgen.

Wichtig:

- `wg0` bleibt beim normalen Serverwechsel aktiv.
- Policy Routing, NAT, DNS und Firewall bleiben bestehen.
- Die GUI wird bereits in der ersten Projektphase gebaut.
- Pi-Backend und GUI werden parallel als zusammengehöriges System entwickelt.
- Der Raspberry Pi bleibt die alleinige Quelle der Wahrheit.
- Die GUI bearbeitet keine lokalen Kopien als führenden Zustand.
- Änderungen werden über eine klar definierte CLI per SSH ausgeführt.
- Die GUI darf keine freien Shell-Befehle auf dem Pi ausführen.
- Die GUI darf keine rohen `iptables`-, `nftables`- oder `ip rule`-Befehle zusammenbauen.
- Für einen normalen Serverwechsel übermittelt die GUI nur eine bekannte `server_id`.
- Beim Anlegen oder Bearbeiten eines Servers übermittelt die GUI validierte Serverdaten an einen dafür vorgesehenen Verwaltungsbefehl.

---

## 3. Bekannter Ist-Stand

Die folgenden Punkte stammen aus dem bisherigen Projektstand und müssen vom Coding-Agenten im Repository und auf dem Raspberry Pi verifiziert werden.

### 3.1 Raspberry Pi als Gateway

- Ein Raspberry Pi wird als Netzwerk-Gateway verwendet.
- Ausgewählte Geräte können ihren Internetverkehr über den Raspberry Pi leiten.
- Als VPN-Technik wird WireGuard verwendet.
- Das WireGuard-Interface heißt voraussichtlich `wg0`.
- Die WireGuard-Konfiguration liegt voraussichtlich unter:

  `/etc/wireguard/wg0.conf`

- Der VPN-Anbieter ist Mullvad.
- Als Mullvad-DNS wird aktuell voraussichtlich verwendet:

- Die WireGuard-Verbindung wird vermutlich über `wg-quick` oder `wg-quick@wg0.service` gestartet.

### 3.2 Policy Routing

Der bestehende Aufbau verwendet voraussichtlich:

- eine eigene Routing-Tabelle `100`,
- quell-IP- oder gerätebezogene `ip rule`-Regeln,
- Routing ausgewählter Clients über `wg0`,
- normales Routing für nicht VPN-gebundene Geräte.

Bekannte Profile:

- `Normal`
- `VPN`
- `Sicher`

Die genaue Semantik muss im vorhandenen Code geprüft werden.

Vermutete Bedeutung:

- `Normal`: normaler Internetzugang ohne VPN,
- `VPN`: Internetzugang über `wg0`,
- `Sicher`: VPN mit Kill Switch; kein ungeschützter Fallback.

Diese Bedeutungen dürfen nicht ungeprüft in Code gegossen werden.

### 3.3 Firewall, NAT und DNS

Das bestehende Projekt verwendet voraussichtlich:

- IPv4-Forwarding,
- `iptables`, `iptables-nft` oder direkt `nftables`,
- NAT beziehungsweise Masquerading,
- Forwarding zwischen LAN und VPN,
- DNS-Weiterleitung oder DNS-DNAT auf `10.64.0.1`,
- Kill-Switch-Regeln für sichere Profile,
- möglicherweise deaktiviertes IPv6.

Der Agent muss feststellen, welche Technik tatsächlich aktiv ist.

### 3.4 Bestehende Projektdateien

Mögliche bestehende Bestandteile:

- `gateway_rules.py`,
- `switch_profile.py`,
- `config.json`,
- `devices.json`,
- Shell- oder systemd-Komponenten,
- SSH-Key-basierter Zugriff vom PC.

Diese Namen sind Annahmen. Der Agent muss die tatsächlichen Dateinamen und Pfade prüfen.

### 3.5 Zu Beginn der Erweiterung noch nicht vorhanden

Folgende Komponenten werden neu erstellt, sofern sie nicht bereits teilweise existieren:

- PC-GUI,
- `vpn_switch.py`,
- stabile JSON-CLI für die GUI,
- Serververwaltung,
- Server-CRUD,
- GUI-basierte Gerätesteuerung,
- strukturierte Geräte- und Regelansicht,
- Health-Check nach Endpoint-Wechsel,
- automatischer Rollback,
- persistente Speicherung des aktiven Servers,
- definierte Fehlercodes,
- sichere sudoers- und SSH-Schnittstelle für die GUI.

### 3.6 Verifizierter Ausgangsstand auf dem Raspberry Pi

Die folgenden Punkte wurden am 29.07.2026 vor der Implementierung per ausschließlich lesendem SSH-Zugriff auf `pi@pi5-marcel` geprüft. Sie dokumentieren den Ausgangsstand; der aktuelle, umgesetzte Stand steht in Abschnitt 3.7.

#### Laufende Komponenten und Deployment

- Das aktuelle Gateway-Projekt liegt unter `/home/pi/vpn-gateway`.
- Der aktive Einstiegspunkt ist `main.py`; die auf dem Pi installierte Datei entspricht der Arbeitskopie dieses Repositorys.
- `vpn-gateway.service` war aktiviert und erfolgreich beendet aktiv. Der Dienst lief als `root` mit:

  ```text
  /home/pi/vpn-gateway/.venv/bin/python /home/pi/vpn-gateway/main.py --all
  ```

- `wg-quick@wg0.service` war aktiviert und aktiv.
- Es gab weder eine `servers.json` noch eine Statusdatei für den aktiven VPN-Server im bestehenden Projektpfad oder unter `/opt/vpn-gateway`.
- Eine Pi-CLI, GUI, Serververwaltung, Health-Check mit Rollback und strukturierte JSON-Antworten existierten noch nicht.

#### Netzwerk, WireGuard und Routing

- Das LAN-Interface ist `eth0` mit `10.0.0.100/24`; das Standard-Gateway ist `10.0.0.138`.
- Das WireGuard-Interface ist `wg0`, aktiv und mit einer `/32`-Tunneladresse konfiguriert.
- Der letzte WireGuard-Handshake war bei der Prüfung frisch (ungefähr 16 Sekunden alt).
- Routing-Tabelle `100` enthält `default dev wg0`.
- Quellbasierte `ip rule`-Einträge verweisen für `10.0.0.21`, `10.0.0.50` und `10.0.0.150` auf Tabelle `100`.
- IPv4-Forwarding ist aktiviert; IPv6 ist für `all` und `default` deaktiviert.

#### Geräte, Profile und effektive Regeln

Die aktuelle Gerätequelle ist `/home/pi/vpn-gateway/devices.json`. Geräte werden derzeit ausschließlich über ihre IP-Adresse identifiziert:

| Gerät | IP-Adresse | Profil |
| --- | --- | --- |
| Haupt_PC | `10.0.0.21` | `VPN` |
| HTB-Kali | `10.0.0.150` | `Sicher` |
| TV Box | `10.0.0.50` | `Sicher` |

- Als Firewall-Backend ist `iptables-nft` aktiv.
- Für alle drei Geräte wird UDP-DNS auf `10.64.0.1` umgeleitet; NAT masqueradet Verkehr über `wg0`.
- Das Profil `VPN` erzwingt für das Gerät den Weg über `wg0` und weist Verkehr über andere ausgehende Interfaces zurück.
- Das Profil `Sicher` enthält zusätzlich eine Sperre zum lokalen Netz sowie eine gerätespezifische TCP-Sperre für Port `14035`.
- Eine globale TCP-MSS-Clamping-Regel ist in der Mangle-Tabelle vorhanden.
- `Sniff` ist im bestehenden Python-Code zusätzlich implementiert, aber gegenwärtig keinem Gerät zugewiesen und nicht Teil der beschriebenen GUI-Profile.

#### Beim Ausgangsstand verifizierte Risiken und Migrationsvorgaben

- `vpn-gateway.service` startete nur nach `network-online.target`, nicht explizit nach `wg-quick@wg0.service`. Der spätere Restore-Dienst musste eine explizite Abhängigkeits- und Reihenfolgedefinition erhalten.
- Der Benutzer `pi` besaß `NOPASSWD: ALL`. Vor GUI-Betrieb musste dies durch eine eng begrenzte sudoers-Regel für den festen CLI-Wrapper ersetzt werden.
- Die Pi-Konfiguration verwendet für `local_network` den Wert `10.0.0.100/24`. Neue Validierung und Speicherung müssen Netzwerkpräfixe kanonisch behandeln, zum Beispiel `10.0.0.0/24`.
- Die lokale `config.json` weicht in diesem Feld vom Pi ab. Bei Installation oder Migration darf die Pi-Konfiguration nicht durch die lokale Datei überschrieben werden.
- Die Umstellung von IP-basierten Schlüsseln auf stabile `device_id`-Werte benötigt eine kompatible, versionierte Migration; vorhandene Geräte und ihre Regeln müssen dabei unverändert erhalten bleiben.

### 3.7 Umgesetzter und verifizierter Stand

Die Erweiterung wurde auf `pi5-marcel` installiert, per Service-Neustart und vollständigem Raspberry-Pi-Neustart geprüft und anschließend gehärtet.

- Die Python-Verwaltungskomponenten liegen unter `/opt/vpn-gateway`; der feste Root-Wrapper ist `/usr/local/bin/vpn-gateway-cli`.
- Die Serverliste wird atomar in `/opt/vpn-gateway/config/servers.json` gespeichert. Die erfolgreich geprüfte Auswahl liegt in `/opt/vpn-gateway/state/current_vpn_server.json`.
- Das vorhandene `devices.json` und `config.json` bleiben die Quelle für Geräte und Gateway-Grundkonfiguration. Geräteprofile werden über den bestehenden Regelkern in `main.py` angewendet.
- `main.py` wurde kompatibel gehärtet und vor dem Austausch als `main.py.pre-vpn-gateway-cli` gesichert.
- Bei `--activate` sichert der Installer die bisherige `vpn-gateway.service` als `vpn-gateway.service.pre-vpn-gateway-cli` und ersetzt sie durch einen Oneshot-Dienst. Dieser wartet auf Netzwerk und `wg-quick@wg0.service`, stellt den ausgewählten VPN-Server wieder her und wendet danach die Geräteprofile an.
- Die sudoers-Regel erlaubt `pi` kennwortlos ausschließlich `/usr/local/bin/vpn-gateway-cli *` als `root`. Die frühere breite Regel wurde als root-lesbares Backup gespeichert; die reguläre, kennwortgeschützte Mitgliedschaft in der Debian-sudo-Gruppe bleibt davon unberührt.
- Die Windows-GUI kommuniziert per SSH mit Host-Key-Prüfung, `BatchMode=yes`, festen CLI-Befehlen und JSON über `stdin`. Sie speichert nur Verbindungsdaten und den Pfad zum privaten Schlüssel, nicht dessen Inhalt.
- Der Live-Test bestätigte Serverwechsel, Profilwechsel, Diagnoseausgaben sowie die Wiederherstellung des ausgewählten Servers und der Geräteprofile nach einem Neustart.

Für die folgenden Abschnitte gilt: Beschriebene Ziel- und Beispielnamen sind durch die nachstehende tatsächliche Projektstruktur konkretisiert.

---

## 4. Projektumfang

### 4.1 In Scope

Die erste zusammenhängende Projektversion umfasst:

#### Raspberry Pi

- Serverliste verwalten,
- Server hinzufügen,
- Server bearbeiten,
- Server löschen,
- aktiven VPN-Server wechseln,
- aktuellen VPN-Status ausgeben,
- Geräte und deren Profile ausgeben,
- Geräteprofile ändern,
- effektive Regeln je Gerät ausgeben,
- Änderungen sicher und atomar speichern,
- Gateway-Regeln nach Profiländerung anwenden,
- WireGuard-Handshake prüfen,
- VPN-Tunnel prüfen,
- Rollback durchführen,
- letzten funktionierenden Server wiederherstellen,
- strukturierte JSON-Ausgaben liefern.

#### Windows-PC-GUI

- SSH-Verbindung konfigurieren,
- Verbindung testen,
- Dashboard anzeigen,
- aktive VPN-Verbindung anzeigen,
- VPN-Server auflisten,
- Server hinzufügen,
- Server bearbeiten,
- Server löschen,
- Server aktivieren,
- Geräte auflisten,
- aktives Profil eines Geräts anzeigen,
- Geräteprofil ändern,
- effektive Regeln eines Geräts anzeigen,
- Fehler und Rollback-Zustände anzeigen,
- Aktionen protokollieren,
- Daten manuell und nach Änderungen automatisch aktualisieren.

### 4.2 Nicht im ersten Scope

Nicht Teil der ersten Version:

- Webserver oder REST-API,
- Cloud-Synchronisierung,
- mobile App,
- Multi-Hop-VPN,
- mehrere parallele VPN-Tunnel,
- automatischer Serverwechsel nach Ping oder Auslastung,
- freie Bearbeitung beliebiger Firewallregeln,
- freier Editor für `ip rule`,
- freier Editor für `iptables` oder `nftables`,
- Bearbeitung der Router- oder DHCP-Konfiguration,
- VPN-Anbieterwechsel,
- automatische Mullvad-API-Integration, sofern nicht ausdrücklich später beauftragt.

---

## 5. Architekturübersicht

```text
Windows-PC
└── VPN Gateway Manager GUI
    ├── Dashboard
    ├── VPN-Server
    │   ├── Anzeigen
    │   ├── Hinzufügen
    │   ├── Bearbeiten
    │   ├── Löschen
    │   └── Aktivieren
    ├── Geräte
    │   ├── Anzeigen
    │   ├── Profil anzeigen
    │   ├── Profil ändern
    │   └── effektive Regeln anzeigen
    ├── Diagnose
    └── SSH Client
        ↓
        strukturierte CLI-Aufrufe über SSH
        ↓
Raspberry Pi
├── gateway_cli.py
│   ├── status
│   ├── server ...
│   ├── vpn ...
│   ├── device ...
│   └── diagnostics ...
├── vpn_switch.py
├── bestehende Gateway-/Profil-Logik
├── ServerRepository
├── DeviceRepository
├── StateRepository
├── WireGuardClient
├── DeviceProfileService
├── EffectiveRulesService
├── HealthChecker
└── ProcessLock
```

---

## 6. Zentrale Architekturentscheidung

### 6.1 Der Raspberry Pi ist die Quelle der Wahrheit

Folgende Daten liegen führend auf dem Raspberry Pi:

- Serverliste,
- aktiver VPN-Server,
- Geräteliste,
- Geräteprofile,
- Gateway-Konfiguration,
- effektive Routing- und Firewallregeln.

Die GUI hält nur:

- Verbindungsdaten zum Pi,
- UI-Zustand,
- optional einen kurzlebigen Cache,
- keine führende Kopie der Gateway-Konfiguration.

### 6.2 Die GUI greift nicht direkt auf Dateien zu

Die GUI darf nicht per SCP oder SFTP direkt folgende Dateien überschreiben:

- `servers.json`,
- `devices.json`,
- `wg0.conf`,
- Gateway-Regeldateien,
- systemd-Units.

Stattdessen nutzt sie ausschließlich validierte CLI-Befehle.

### 6.3 Keine freie Shell

Die GUI führt nur definierte Kommandos aus.

Nicht erlaubt:

```text
ssh pi "beliebiger vom Benutzer erzeugter Shell-String"
```

Erlaubt ist eine klar begrenzte Schnittstelle:

```text
ssh pi "sudo /usr/local/bin/vpn-gateway-cli <definierter-befehl>"
```

Eingabedaten werden vorzugsweise als JSON über `stdin` übertragen. Dadurch müssen Servernamen, Keys und Endpoints nicht in einen Shell-String eingebaut werden.

---

## 7. Tatsächliche Projektstruktur

Die Umsetzung bündelt Backend und GUI in einem Python-Projekt. Die bestehende Regelengine bleibt in `main.py`; neue Verwaltungslogik ist klar davon getrennt.

```text
project-root/
├── main.py                         # bestehender Gateway-Regelkern
├── pyproject.toml
├── vpn_gateway/
│   ├── cli.py                      # JSON-CLI und Befehlsrouting
│   ├── models.py
│   ├── repositories.py
│   ├── services.py
│   ├── wireguard.py
│   ├── gateway_adapter.py
│   ├── command_runner.py
│   ├── locking.py
│   ├── paths.py
│   └── storage.py
├── vpn_gateway_gui/
│   ├── app.py                      # PySide6-Oberfläche
│   └── gateway_client.py           # SSH- und JSON-Client
├── gui_main.py
├── pi/
│   ├── scripts/
│   │   ├── install.sh
│   │   ├── uninstall.sh
│   │   └── vpn-gateway-cli
│   └── systemd/
│       └── vpn-gateway-restore.service
└── tests/
```

Falls das bestehende Pi-Projekt bereits Skripte wie `gateway_rules.py` oder `switch_profile.py` enthält, sollen diese zunächst über Adapter eingebunden werden.

---

## 8. Pi-Komponenten

### 8.1 `vpn_gateway/cli.py`

`vpn_gateway/cli.py` beziehungsweise der installierte Befehl `vpn-gateway-cli` ist die einzige öffentliche Remote-Schnittstelle der GUI.

Er orchestriert die spezialisierten Komponenten.

Beispiel:

```bash
vpn-gateway-cli status --json
vpn-gateway-cli server list --json
vpn-gateway-cli server add --json
vpn-gateway-cli server update --server-id at-vie-001 --json
vpn-gateway-cli server delete --server-id at-vie-001 --json
vpn-gateway-cli vpn switch --server-id at-vie-001 --json
vpn-gateway-cli device list --json
vpn-gateway-cli device show --device-id desktop-pc --json
vpn-gateway-cli device set-profile --device-id desktop-pc --profile VPN --json
vpn-gateway-cli diagnostics rules --device-id desktop-pc --json
```

### 8.2 `vpn_gateway/wireguard.py` und `VpnService`

`WireGuardClient` in `vpn_gateway/wireguard.py` und `VpnService` in `vpn_gateway/services.py` sind für den WireGuard-Wechsel zuständig.

Es soll nicht gleichzeitig die gesamte GUI- oder Geräteverwaltung übernehmen.

Aufgaben:

- aktuellen WireGuard-Zustand lesen,
- neuen Server anhand einer validierten Server-ID laden,
- Runtime-Konfiguration sichern,
- Peer im laufenden Betrieb wechseln,
- Handshake und Tunnel prüfen,
- Rollback durchführen,
- aktiven Server speichern.

### 8.3 `ServerManagementService`

Aufgaben:

- Serverliste ausgeben,
- Server anlegen,
- Server ändern,
- Server löschen,
- Eingaben validieren,
- aktive Server schützen,
- Dateien atomar speichern,
- Backups erstellen.

### 8.4 `DeviceProfileService`

Aufgaben:

- bekannte Geräte laden,
- aktuelles Profil ermitteln,
- Profilzuweisung validieren,
- Profil ändern,
- bestehende Gateway-Logik zum Anwenden aufrufen,
- Ergebnis prüfen,
- bei Fehler vorherigen Zustand wiederherstellen.

### 8.5 `EffectiveRulesService`

Aufgaben:

- verständliche Regelwirkung eines Geräts ausgeben,
- tatsächliche `ip rule`-Zuordnung ermitteln,
- relevante Route ermitteln,
- DNS-Verhalten anzeigen,
- Kill-Switch-Wirkung anzeigen,
- relevante Firewallregeln diagnostisch anzeigen.

Dieser Service ist primär lesend.

Die GUI soll keine rohen Firewallzeilen direkt bearbeiten.

---

## 9. Serververwaltung

### 9.1 Datenhaltung

Empfohlene Datei:

```text
/opt/vpn-gateway/config/servers.json
```

Beispiel:

```json
{
  "schema_version": 1,
  "servers": [
    {
      "id": "at-vie-001",
      "name": "Austria - Vienna 1",
      "country": "Austria",
      "city": "Vienna",
      "endpoint": "203.0.113.10:51820",
      "public_key": "SERVER_PUBLIC_KEY",
      "allowed_ips": [
        "0.0.0.0/0"
      ],
      "persistent_keepalive": 25,
      "enabled": true
    }
  ]
}
```

Die IP-Adresse und der Key sind Platzhalter.

### 9.2 Server hinzufügen

Die GUI zeigt einen Dialog mit:

- eindeutiger ID,
- Anzeigename,
- Land,
- Stadt,
- Endpoint oder Host,
- Port,
- WireGuard-Public-Key,
- Aktiviert/Deaktiviert,
- optional `AllowedIPs`,
- optional `PersistentKeepalive`.

Die GUI führt grundlegende Validierung durch.

Der Pi führt dieselbe Validierung erneut durch. Clientvalidierung ersetzt niemals Servervalidierung.

### 9.3 Server bearbeiten

Bearbeitbar:

- Anzeigename,
- Land,
- Stadt,
- Endpoint,
- Port,
- Public Key,
- Aktivstatus,
- optionale WireGuard-Parameter.

Die technische `id` sollte nach der Erstellung standardmäßig unveränderlich sein.

Falls eine ID-Änderung später unterstützt wird, muss sie als eigene Rename-Operation implementiert werden.

### 9.4 Server löschen

Löschregeln:

- Der aktuell aktive Server darf nicht direkt gelöscht werden.
- Vor dem Löschen muss ein anderer Server aktiviert werden.
- Eine Bestätigungsabfrage ist erforderlich.
- Das Löschen verändert nicht automatisch `wg0`.
- Der Pi prüft die Bedingung zusätzlich zur GUI.
- Der letzte Server darf nur gelöscht werden, wenn ausdrücklich ein Betrieb ohne hinterlegten VPN-Server erlaubt ist.

### 9.5 Server testen

Optional, aber für die erste GUI sinnvoll:

```bash
vpn-gateway-cli server test --server-id at-vie-001 --json
```

Ein Test darf den aktiven Tunnel nicht dauerhaft verändern.

Mögliche erste Umsetzung:

- Syntax und Erreichbarkeit des Endpoints prüfen,
- vollständigen Test zunächst als noch nicht unterstützt kennzeichnen.

Ein echter Test mit temporärem Peer darf nur umgesetzt werden, wenn er den produktiven Tunnel nicht gefährdet.

### 9.6 Eingabeformat

Für `add` und `update` soll die GUI JSON über `stdin` senden.

Beispiel:

```json
{
  "name": "Austria - Vienna 1",
  "country": "Austria",
  "city": "Vienna",
  "endpoint": "203.0.113.10:51820",
  "public_key": "SERVER_PUBLIC_KEY",
  "enabled": true
}
```

Keine dynamischen Shellargumente mit unescaped Benutzereingaben.

---

## 10. Geräteverwaltung

### 10.1 Ziel

Die GUI erhält einen eigenen Bereich `Geräte`.

Dort sollen alle konfigurierten Geräte separat sichtbar sein.

Pro Gerät mindestens anzeigen:

- Anzeigename,
- interne Geräte-ID,
- IP-Adresse,
- optional MAC-Adresse,
- aktuelles Profil,
- erwarteter Internetpfad,
- erwarteter DNS-Pfad,
- Kill-Switch aktiv oder nicht,
- letzter erfolgreicher Regel-Apply,
- Warnungen bei inkonsistentem Zustand.

### 10.2 Profilwechsel

Ein Gerät soll in der GUI zwischen vorhandenen Profilen umgeschaltet werden können.

Beispiel:

```text
Desktop-PC    192.168.1.50    VPN
Smart-TV      192.168.1.60    Normal
Konsole       192.168.1.70    Sicher
```

Änderung:

```bash
vpn-gateway-cli device set-profile \
  --device-id desktop-pc \
  --profile VPN \
  --json
```

Ablauf auf dem Pi:

1. Prozess-Lock beziehen.
2. Gerät laden.
3. Zielprofil validieren.
4. bisherigen Gerätezustand sichern.
5. Profilzuweisung atomar speichern.
6. bestehende Gateway-Regellogik anwenden.
7. effektiven Zustand prüfen.
8. bei Fehler alte Profilzuweisung wiederherstellen.
9. alte Regeln erneut anwenden.
10. Ergebnis als JSON ausgeben.

### 10.3 Keine freie Regelbearbeitung in Version 1

Die GUI soll in der ersten Version nicht erlauben:

- freie `iptables`-Regeln einzugeben,
- freie `nftables`-Regeln einzugeben,
- freie `ip rule`-Einträge zu erzeugen,
- beliebige Routingtabellen zu bearbeiten.

Stattdessen ändert der Benutzer das gewünschte Profil.

Die bestehende Gateway-Logik erzeugt daraus die korrekten technischen Regeln.

### 10.4 Effektive Regeln anzeigen

Für jedes Gerät soll eine separate Detailansicht vorhanden sein.

Beispielhafte Darstellung:

```text
Gerät: Desktop-PC
IP: 192.168.1.50
Profil: VPN

Erwartete Wirkung:
- Internetverkehr über WireGuard wg0
- Policy-Routing-Tabelle 100
- DNS über 10.64.0.1
- Fallback ohne VPN: abhängig von bestehender Profildefinition
- Kill Switch: nein

Tatsächlich erkannt:
- ip rule vorhanden: ja
- Route in Tabelle 100 vorhanden: ja
- passende Firewallregel vorhanden: ja
- DNS-Regel vorhanden: ja
- Zustand konsistent: ja
```

Zusätzlich kann eine aufklappbare Diagnoseansicht rohe relevante Systemzeilen anzeigen.

Diese Rohdaten sind schreibgeschützt.

### 10.5 Geräte hinzufügen oder bearbeiten

Ob die bestehende Konfiguration bereits Geräteverwaltung unterstützt, muss geprüft werden.

Die GUI-Architektur soll Erweiterungspunkte vorsehen für:

- Gerät hinzufügen,
- Anzeigename ändern,
- IP-Adresse ändern,
- MAC-Adresse ergänzen,
- Gerät deaktivieren,
- Gerät löschen.

Für die erste Umsetzung sind mindestens erforderlich:

- Geräte auflisten,
- Profil ändern,
- effektive Regeln anzeigen.

Geräte-CRUD soll implementiert werden, wenn die bestehende `devices.json` oder gleichwertige Struktur dies bereits sauber unterstützt.

Falls Geräte-CRUD zusätzliche DHCP- oder Routeränderungen erfordern würde, soll es zunächst auf die interne Gateway-Konfiguration begrenzt oder klar als Folgefunktion dokumentiert werden.

---

## 11. Profile

### 11.1 Quelle der Wahrheit

Die vorhandene Profilimplementierung bleibt maßgeblich.

Der Agent soll prüfen:

- wo Profile definiert sind,
- welche Regeln ein Profil erzeugt,
- wie Geräte einem Profil zugewiesen werden,
- wie Regeln angewendet werden,
- wie Fehler erkannt werden.

### 11.2 Profilmodell für die GUI

Die CLI soll Profile strukturiert liefern.

Beispiel:

```json
{
  "success": true,
  "profiles": [
    {
      "id": "Normal",
      "name": "Normal",
      "description": "Direkter Internetzugang ohne VPN",
      "vpn_required": false,
      "kill_switch": false
    },
    {
      "id": "VPN",
      "name": "VPN",
      "description": "Internetzugang über WireGuard",
      "vpn_required": true,
      "kill_switch": false
    },
    {
      "id": "Sicher",
      "name": "Sicher",
      "description": "Nur VPN; kein ungeschützter Fallback",
      "vpn_required": true,
      "kill_switch": true
    }
  ]
}
```

Die Beschreibungen sind erst nach Prüfung des bestehenden Systems festzulegen.

### 11.3 Profiländerung und VPN-Zustand

Wird ein Gerät auf ein VPN-abhängiges Profil gesetzt, während `wg0` nicht funktionsfähig ist, muss das Verhalten eindeutig sein.

Empfehlung:

- `Sicher`: Änderung zulassen, Verkehr bleibt durch Kill Switch blockiert.
- `VPN`: Verhalten aus bestehender Semantik übernehmen.
- GUI zeigt eine Warnung, wenn kein funktionierender Tunnel vorhanden ist.

Keine stillschweigende Umleitung über den normalen Internetpfad, wenn dies dem Profil widerspricht.

---

## 12. WireGuard-Serverwechsel

### 12.1 Bevorzugter Mechanismus

Der normale Wechsel soll nicht Folgendes ausführen:

```bash
wg-quick down wg0
wg-quick up wg0
```

Bevorzugt:

```bash
wg syncconf wg0 <TEMP_CONFIG>
```

### 12.2 Ablauf

1. Wechsel-Lock beziehen.
2. Root-Rechte prüfen.
3. `server_id` laden und validieren.
4. Zustand von `wg0` prüfen.
5. aktuelle Runtime-Konfiguration mit `wg showconf wg0` sichern.
6. aktuellen Peer und Endpoint erfassen.
7. temporäre Zielkonfiguration erzeugen.
8. `wg syncconf` ausführen.
9. Testverkehr über `wg0` auslösen.
10. frischen Handshake prüfen.
11. Tunnel-Health-Check ausführen.
12. bei Erfolg aktiven Server atomar speichern.
13. Ergebnis als JSON zurückgeben.
14. bei Fehler vorherige Runtime-Konfiguration wiederherstellen.
15. Rollback prüfen.

### 12.3 Endpoint und Public Key

Ein Serverwechsel muss gemeinsam ändern:

- Endpoint,
- Port,
- Peer-Public-Key.

Nur die IP-Adresse eines alten Peers zu ändern ist nicht ausreichend.

### 12.4 Auswirkungen

Der Wechsel hält `wg0` aktiv.

Trotzdem können bestehende TCP-Verbindungen abbrechen, weil sich die öffentliche Ausgangs-IP ändert.

Die GUI soll vor dem Wechsel einen kurzen Hinweis anzeigen:

```text
Bestehende Downloads, Streams oder Spielsitzungen können getrennt werden.
```

---

## 13. Statusdateien und Persistenz

### 13.1 Aktiver Server

Empfohlene Datei:

```text
/opt/vpn-gateway/state/current_vpn_server.json
```

Beispiel:

```json
{
  "schema_version": 1,
  "server_id": "at-vie-001",
  "switched_at": "2026-07-29T13:00:00+02:00",
  "verified": true
}
```

### 13.2 Gerätezustand

Der gewünschte Profilzustand liegt in der bestehenden Gerätedatei oder in einer neuen klar definierten Datei.

Beispiel:

```json
{
  "schema_version": 1,
  "devices": [
    {
      "id": "desktop-pc",
      "name": "Desktop-PC",
      "ip_address": "192.168.1.50",
      "mac_address": null,
      "profile": "VPN",
      "enabled": true
    }
  ]
}
```

### 13.3 Atomare Schreibvorgänge

Für alle veränderbaren JSON-Dateien:

1. Daten vollständig validieren.
2. temporäre Datei im gleichen Verzeichnis erzeugen.
3. Dateirechte setzen.
4. Inhalt schreiben.
5. `flush` und `fsync`.
6. Backup der bisherigen Datei erzeugen.
7. Datei mit `os.replace()` ersetzen.
8. neue Datei erneut lesen und validieren.

---

## 14. CLI-Protokoll

### 14.1 Grundregeln

- strukturierte Antworten als JSON,
- JSON auf `stdout`,
- Logs auf `stderr`,
- keine zusätzlichen Texte auf `stdout`,
- stabile `error_code`-Werte,
- jeder Befehl liefert `success`,
- Rückgabecode `0` bei Erfolg,
- Rückgabecode ungleich `0` bei Fehler.

### 14.2 Status

```bash
vpn-gateway-cli status --json
```

Beispiel:

```json
{
  "success": true,
  "gateway": {
    "reachable": true,
    "routing_table": 100
  },
  "vpn": {
    "interface": "wg0",
    "interface_up": true,
    "server_id": "at-vie-001",
    "endpoint": "203.0.113.10:51820",
    "latest_handshake_age_seconds": 8
  },
  "devices": {
    "total": 3,
    "normal": 1,
    "vpn": 1,
    "secure": 1
  }
}
```

### 14.3 Serverliste

```bash
vpn-gateway-cli server list --json
```

### 14.4 Server hinzufügen

```bash
vpn-gateway-cli server add --json
```

Serverdaten kommen über `stdin`.

### 14.5 Server bearbeiten

```bash
vpn-gateway-cli server update --server-id at-vie-001 --json
```

Änderungen kommen über `stdin`.

### 14.6 Server löschen

```bash
vpn-gateway-cli server delete --server-id at-vie-001 --json
```

### 14.7 VPN wechseln

```bash
vpn-gateway-cli vpn switch --server-id at-vie-001 --json
```

### 14.8 Geräte auflisten

```bash
vpn-gateway-cli device list --json
```

### 14.9 Gerät anzeigen

```bash
vpn-gateway-cli device show --device-id desktop-pc --json
```

### 14.10 Profil ändern

```bash
vpn-gateway-cli device set-profile \
  --device-id desktop-pc \
  --profile VPN \
  --json
```

### 14.11 Effektive Regeln anzeigen

```bash
vpn-gateway-cli diagnostics rules \
  --device-id desktop-pc \
  --json
```

---

## 15. Fehlercodes

Empfohlene stabile Fehlercodes:

### Allgemein

- `NOT_ROOT`
- `INVALID_ARGUMENT`
- `INVALID_JSON`
- `CONFIG_NOT_FOUND`
- `CONFIG_INVALID`
- `STATE_WRITE_FAILED`
- `LOCKED`
- `COMMAND_TIMEOUT`
- `INTERNAL_ERROR`

### Server

- `SERVER_NOT_FOUND`
- `SERVER_ALREADY_EXISTS`
- `SERVER_DISABLED`
- `SERVER_IN_USE`
- `LAST_SERVER_DELETE_FORBIDDEN`
- `INVALID_ENDPOINT`
- `INVALID_PUBLIC_KEY`
- `DUPLICATE_SERVER_ID`

### WireGuard

- `WG_INTERFACE_NOT_FOUND`
- `WG_INTERFACE_DOWN`
- `WG_READ_FAILED`
- `WG_APPLY_FAILED`
- `HANDSHAKE_TIMEOUT`
- `TUNNEL_HEALTHCHECK_FAILED`
- `ROLLBACK_FAILED`

### Geräte und Profile

- `DEVICE_NOT_FOUND`
- `DEVICE_ALREADY_EXISTS`
- `PROFILE_NOT_FOUND`
- `PROFILE_APPLY_FAILED`
- `DEVICE_STATE_INCONSISTENT`
- `DEVICE_ROLLBACK_FAILED`

### SSH und GUI

Diese Codes entstehen auf der GUI-Seite:

- `SSH_CONNECTION_FAILED`
- `SSH_AUTHENTICATION_FAILED`
- `SSH_HOST_KEY_MISMATCH`
- `REMOTE_COMMAND_FAILED`
- `REMOTE_RESPONSE_INVALID`
- `REMOTE_TIMEOUT`

---

## 16. Health-Check und Rollback

### 16.1 VPN-Health-Check

Nach dem Serverwechsel prüfen:

- neuer Peer vorhanden,
- Handshake nach dem Umschaltzeitpunkt,
- Handshake innerhalb eines konfigurierbaren Timeouts,
- Tunnelziel über `wg0` erreichbar,
- optional öffentliche Ausgangs-IP.

Empfohlener Timeout:

- 10 bis 15 Sekunden.

### 16.2 VPN-Rollback

Vor dem Wechsel vollständige Runtime-Konfiguration sichern.

Bei Fehler:

1. alte Konfiguration mit `wg syncconf` anwenden,
2. alten Peer prüfen,
3. alten Handshake prüfen,
4. Statusdatei unverändert lassen,
5. Rollback-Ergebnis zurückgeben.

### 16.3 Geräteprofil-Rollback

Vor Profiländerung:

- alte Gerätezuordnung sichern,
- relevante aktuelle Regeln erfassen.

Bei Fehler:

1. alte Zuordnung wiederherstellen,
2. Gateway-Regeln erneut anwenden,
3. effektiven Zustand prüfen,
4. Rollback-Ergebnis zurückgeben.

---

## 17. Nebenläufigkeit

Es sollen getrennte Locks verwendet werden.

Empfohlen:

```text
/run/lock/vpn-gateway-vpn-switch.lock
/run/lock/vpn-gateway-server-config.lock
/run/lock/vpn-gateway-device-config.lock
/run/lock/vpn-gateway-rules-apply.lock
```

Regeln:

- nur ein VPN-Wechsel gleichzeitig,
- keine Serveränderung während eines laufenden Wechsels,
- keine konkurrierenden Geräteprofiländerungen,
- kein paralleles Anwenden der Gateway-Regeln,
- reine Leseoperationen dürfen soweit sicher parallel erfolgen.

---

## 18. Logging

Für Python-Code wird `loguru` verwendet.

Regeln:

- `INFO` für wichtige Aktionen,
- `ERROR` für Fehler,
- `DEBUG` für technische Details und geparste Datenstrukturen,
- keine privaten WireGuard-Schlüssel loggen,
- Public Keys nur gekürzt loggen,
- keine vollständigen geheimen Konfigurationen loggen,
- GUI-Aktionen lokal protokollieren,
- Pi-Aktionen auf dem Pi protokollieren,
- Korrelations-ID je GUI-Aktion verwenden.

Beispiel:

```text
request_id=8f32... action=device.set_profile device=desktop-pc profile=VPN
```

Empfohlene Pi-Logdatei:

```text
/var/log/vpn-gateway/gateway.log
```

---

## 19. Sicherheit

### 19.1 SSH

- SSH-Key-Authentifizierung,
- `BatchMode=yes`,
- definierter Host-Key,
- kein automatisches Akzeptieren geänderter Host-Keys,
- konfigurierbarer SSH-Host und Benutzer,
- kurze Timeouts,
- keine Passwortspeicherung im Klartext.

### 19.2 sudoers

Die GUI meldet sich als normaler Benutzer an.

Erhöhte Rechte nur für exakt definierte Verwaltungsbefehle.

Bevorzugt:

```text
/usr/local/bin/vpn-gateway-cli
```

Nicht bevorzugt:

```text
/usr/bin/python3 /beliebiger/pfad/skript.py *
```

Der installierte Wrapper muss:

- einen festen Codepfad verwenden,
- keine frei wählbaren Skriptpfade akzeptieren,
- Eingaben selbst validieren,
- keine Shell öffnen.

### 19.3 Server-CRUD

Beim Serverwechsel bleibt die Eingabe auf `server_id` beschränkt.

Beim Server-CRUD sind neue Endpoint- und Public-Key-Daten erforderlich.

Diese werden:

- als JSON über `stdin` übertragen,
- auf dem Pi erneut validiert,
- niemals direkt in einen Shell-Befehl interpoliert,
- atomar gespeichert,
- nicht ungeprüft auf `wg0` angewendet.

### 19.4 Geräteänderungen

Die GUI sendet nur:

- `device_id`,
- `profile_id`,
- gegebenenfalls klar definierte Gerätefelder.

Sie sendet keine technischen Firewallbefehle.

---

## 20. PC-GUI

### 20.1 Technologie

Empfehlung:

- Python,
- PySide6,
- Qt Widgets oder QML entsprechend dem bestehenden Projektstil,
- SSH über eine saubere Client-Abstraktion,
- keine blockierenden Netzwerkaufrufe im UI-Thread.

Der Agent soll prüfen, ob im bestehenden Projekt bereits Qt Widgets oder QML verwendet wird.

### 20.2 Hauptnavigation

Empfohlene Bereiche:

```text
Dashboard
VPN-Server
Geräte
Diagnose
Einstellungen
```

### 20.3 Dashboard

Anzeige:

- Verbindung zum Pi,
- `wg0`-Status,
- aktiver Server,
- Endpoint,
- letzter Handshake,
- optional Ausgangs-IP,
- Anzahl der Geräte pro Profil,
- Warnungen,
- Aktualisieren-Schaltfläche.

### 20.4 VPN-Server-Seite

Tabelle:

- Name,
- Land,
- Stadt,
- Endpoint,
- Aktiviert,
- aktuell aktiv,
- letzter bekannter Status.

Aktionen:

- Hinzufügen,
- Bearbeiten,
- Löschen,
- Aktivieren,
- Aktualisieren.

### 20.5 Serverdialog

Felder:

- ID,
- Name,
- Land,
- Stadt,
- Host oder IP,
- Port,
- Public Key,
- Aktiviert,
- erweiterte WireGuard-Optionen.

Validierung:

- Pflichtfelder,
- ID-Format,
- Portbereich,
- Endpoint-Format,
- Public-Key-Format.

### 20.6 Geräte-Seite

Tabelle:

- Gerätename,
- IP-Adresse,
- MAC-Adresse, falls vorhanden,
- Profil,
- VPN-Pfad,
- Kill Switch,
- Konsistenzstatus.

Aktionen:

- Profil ändern,
- Details anzeigen,
- Regeln aktualisieren,
- optional Gerät bearbeiten.

### 20.7 Gerätedetail

Bereiche:

#### Allgemein

- ID,
- Name,
- IP,
- MAC,
- Aktivstatus.

#### Gewünschte Konfiguration

- Profil,
- erwarteter Route-Pfad,
- erwarteter DNS-Pfad,
- erwarteter Kill-Switch-Zustand.

#### Effektiver Systemzustand

- passende `ip rule`,
- Route in Tabelle `100`,
- relevante Firewallregel,
- NAT-Zuordnung,
- DNS-Regel,
- erkannte Abweichungen.

#### Rohdiagnose

- relevante Zeilen aus `ip rule`,
- relevante Route,
- relevante Firewallauszüge.

Rohdiagnose ist schreibgeschützt.

### 20.8 Einstellungen

- Raspberry-Pi-Host,
- SSH-Port,
- SSH-Benutzer,
- Pfad zum privaten Schlüssel,
- Host-Key-Fingerprint,
- Verbindungs-Timeout,
- Remote-CLI-Pfad,
- Log-Level.

### 20.9 GUI-Zustandsmodell

Die GUI darf Remote-Daten nicht als aktuell anzeigen, wenn:

- die SSH-Verbindung unterbrochen wurde,
- der letzte Refresh fehlgeschlagen ist,
- ein Änderungsbefehl noch läuft,
- die Antwort nicht validiert werden konnte.

Zustände:

- `Loading`,
- `Ready`,
- `Changing`,
- `Stale`,
- `Error`.

### 20.10 Asynchronität

SSH-Aufrufe dürfen den UI-Thread nicht blockieren.

Mögliche Umsetzung:

- `QThreadPool` und `QRunnable`,
- dedizierter Worker-Thread,
- Qt-kompatible Async-Integration.

Für jede schreibende Aktion:

1. betroffene Buttons deaktivieren,
2. Fortschrittsstatus anzeigen,
3. Remote-Befehl ausführen,
4. JSON auswerten,
5. Daten neu laden,
6. Ergebnis anzeigen.

---

## 21. GUI-zu-Pi-Kommunikation

### 21.1 Client-Abstraktion

Die GUI verwendet eine zentrale Klasse:

```text
GatewayClient
├── get_status()
├── list_servers()
├── add_server()
├── update_server()
├── delete_server()
├── switch_server()
├── list_devices()
├── get_device()
├── set_device_profile()
└── get_effective_rules()
```

Darunter liegt:

```text
SshClient
└── execute(command, stdin_json, timeout)
```

Views oder ViewModels dürfen nicht direkt SSH-Kommandos zusammensetzen.

### 21.2 Antwortvalidierung

Jede Antwort wird gegen ein erwartetes Modell validiert.

Mindestens prüfen:

- valides JSON,
- `success` vorhanden,
- erwartete Felder vorhanden,
- Datentypen korrekt,
- keine Vermischung von Logs und JSON,
- Remote-Exitcode.

### 21.3 Versionierung

Die CLI soll eine Protokollversion liefern.

Beispiel:

```json
{
  "success": true,
  "api_version": 1,
  "application_version": "0.1.0"
}
```

Die GUI prüft die Kompatibilität.

---

## 22. Nutzung bestehender Gateway-Logik

### 22.1 Keine doppelte Regelengine

Falls bereits `gateway_rules.py` oder eine vergleichbare Komponente existiert, bleibt diese verantwortlich für die technische Erzeugung der Regeln.

Die neue Architektur ergänzt:

- strukturierte Eingabe,
- Validierung,
- Statusausgabe,
- Transaktionen,
- Rollback,
- GUI-Anbindung.

### 22.2 Adapter

Empfohlene Komponente:

```text
GatewayAdapter
```

Aufgaben:

- bestehende Profiländerung aufrufen,
- bestehendes Apply ausführen,
- Ergebnis prüfen,
- relevante Diagnosedaten liefern.

Langfristig kann bestehende Skriptlogik in importierbare Python-Module überführt werden. Dies soll schrittweise erfolgen.

### 22.3 Keine Shelltext-Auswertung

Falls ein altes Skript derzeit nur Text ausgibt, soll der Adapter Rückgabecode und definierte Signale auswerten.

Mittelfristig sollen bestehende Kernfunktionen direkt importierbar sein.

---

## 23. Tests

### 23.1 Pi-Unit-Tests

Mindestens:

- Serverliste laden,
- Servervalidierung,
- Server hinzufügen,
- Server bearbeiten,
- aktiven Server nicht löschen,
- atomische Dateioperationen,
- unbekannte Server-ID,
- WireGuard-Konfiguration erzeugen,
- Peer ersetzen,
- Rollback-Entscheidung,
- Geräteliste laden,
- unbekanntes Gerät,
- unbekanntes Profil,
- Profiländerung,
- Geräte-Rollback,
- effektive Regeln parsen,
- Fehlercodes,
- JSON-Ausgaben.

Systembefehle werden gemockt.

### 23.2 GUI-Unit-Tests

Mindestens:

- JSON-Antwortmodelle,
- SSH-Fehlerzuordnung,
- Serverformularvalidierung,
- Geräteprofil-Auswahl,
- ViewModel-Zustände,
- Sperren während laufender Aktionen,
- Fehleranzeige,
- Refresh nach erfolgreicher Änderung.

### 23.3 GUI-Integrationstests

Mit Fake- oder Mock-GatewayClient:

- Server hinzufügen,
- Server bearbeiten,
- Server löschen,
- aktiven Server wechseln,
- Gerät auf `VPN` setzen,
- Gerät auf `Normal` setzen,
- Fehler und Rollback anzeigen,
- ungültige Remote-Antwort behandeln.

### 23.4 Pi-Integrationstests

Vor Tests sichern:

```bash
ip rule
ip route show table 100
wg show
wg showconf wg0
iptables-save
nft list ruleset
```

Nur die tatsächlich verwendete Firewalltechnik auswerten.

Tests:

1. Status laden.
2. Server hinzufügen.
3. Server bearbeiten.
4. nicht aktiven Server löschen.
5. auf zweiten Server wechseln.
6. Handshake prüfen.
7. Routingzustand vergleichen.
8. Gerät von `Normal` auf `VPN` setzen.
9. effektive Regeln prüfen.
10. Gerät auf vorheriges Profil zurücksetzen.
11. Profil-Apply-Fehler simulieren.
12. Geräte-Rollback prüfen.
13. VPN-Wechselfehler simulieren.
14. VPN-Rollback prüfen.
15. Neustart und Restore prüfen.

### 23.5 End-to-End-Test

Kompletter Ablauf über die GUI:

1. GUI verbindet sich mit Pi.
2. Dashboard wird geladen.
3. neuer Server wird angelegt.
4. Server wird bearbeitet.
5. Server wird aktiviert.
6. Status zeigt neuen Endpoint.
7. Gerät wird auf `VPN` umgestellt.
8. Gerätedetails zeigen Tabelle `100` und VPN-DNS.
9. Gerät wird auf `Normal` zurückgestellt.
10. nicht aktiver Testserver wird gelöscht.

---

## 24. Persistenz nach Neustart

### 24.1 VPN-Server und Geräteprofile wiederherstellen

Nach Start von `wg0`:

```bash
vpn-gateway-cli vpn restore --json
```

Installierter systemd-Oneshot-Service (`/etc/systemd/system/vpn-gateway.service`):

```ini
[Unit]
Description=Restore selected VPN gateway server and device profiles
After=network-online.target wg-quick@wg0.service
Wants=network-online.target
Requires=wg-quick@wg0.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/vpn-gateway-cli vpn restore --json
ExecStart=/usr/local/bin/vpn-gateway-cli device restore --json
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Reihenfolge: Netzwerk verfügbar, `wg0` aktiv, ausgewählten VPN-Server wiederherstellen, gespeicherte Geräteprofile anwenden. Scheitert der Dienst nach einem Austausch, stellt der Installer die zuvor gesicherte Unit-Datei wieder her.

---

## 25. Implementierungsphasen

Alle sechs Phasen wurden umgesetzt und in diesem Repository zusammengeführt. Die nachstehenden Punkte dokumentieren den erreichten Umfang.

### Phase 1: Bestand und Grundgerüst — abgeschlossen

Pi:

- bestehendes Projekt analysieren,
- reale Pfade und Dienste dokumentieren,
- vorhandene Profil- und Regelkomponenten identifizieren,
- CLI-Grundgerüst erstellen,
- Status- und Versionsbefehl erstellen.

GUI:

- PySide6-Projekt erstellen,
- Hauptfenster und Navigation erstellen,
- SSH-Einstellungen erstellen,
- Verbindungstest implementieren,
- Dashboard-Grundgerüst erstellen.

Ergebnis:

- GUI kann den Pi erreichen,
- Version und Grundstatus werden angezeigt.

### Phase 2: Serveranzeige und Server-CRUD — abgeschlossen

Pi:

- ServerRepository,
- ServerManagementService,
- `server list`,
- `server add`,
- `server update`,
- `server delete`.

GUI:

- Serverliste,
- Serverdialog,
- Hinzufügen,
- Bearbeiten,
- Löschen,
- Validierung,
- Fehlermeldungen.

Ergebnis:

- Server können vollständig über die GUI verwaltet werden.

### Phase 3: VPN-Wechsel — abgeschlossen

Pi:

- WireGuardClient,
- `WireGuardClient` und `VpnService`,
- HealthChecker,
- Rollback,
- Persistenz.

GUI:

- Server aktivieren,
- Fortschrittsanzeige,
- Warnung zu Verbindungsabbrüchen,
- neuen Status laden,
- Rollback anzeigen.

Ergebnis:

- aktiver Endpoint kann über die GUI gewechselt werden.

### Phase 4: Geräteansicht und Profilwechsel — abgeschlossen

Pi:

- DeviceRepository,
- Profilauflistung,
- DeviceProfileService,
- GatewayAdapter,
- Geräte-Rollback.

GUI:

- Geräteliste,
- Profilanzeige,
- Profilwechsel,
- Status- und Warnungsdarstellung.

Ergebnis:

- Geräte können über die GUI zwischen vorhandenen Profilen umgeschaltet werden.

### Phase 5: Effektive Regeln und Diagnose — abgeschlossen

Pi:

- EffectiveRulesService,
- strukturierte Diagnoseausgaben,
- Konsistenzprüfung.

GUI:

- Gerätedetail,
- erwartete Wirkung,
- tatsächliche Regeln,
- schreibgeschützte Rohdiagnose.

Ergebnis:

- pro Gerät ist sichtbar, wie es tatsächlich geroutet und gefiltert wird.

### Phase 6: Restore, Installation und Härtung — abgeschlossen

- systemd,
- sudoers,
- Installer,
- Backups,
- Neustarttests,
- End-to-End-Tests,
- PyInstaller-Build für Windows,
- Dokumentation.

---

## 26. Akzeptanzkriterien

### 26.1 Gesamtsystem

Die erste Version gilt als abgeschlossen, wenn:

- die GUI auf Windows startet,
- SSH-Verbindung zum Pi konfiguriert werden kann,
- Dashboard den Pi- und VPN-Status zeigt,
- Serverliste aus dem Pi geladen wird,
- Server in der GUI hinzugefügt werden können,
- Server in der GUI geändert werden können,
- nicht aktive Server in der GUI gelöscht werden können,
- ein Server über die GUI aktiviert werden kann,
- `wg0` beim normalen Wechsel aktiv bleibt,
- ein frischer Handshake geprüft wird,
- ein fehlerhafter Wechsel zurückgerollt wird,
- Geräte aus dem Pi geladen werden,
- das aktive Profil jedes Geräts sichtbar ist,
- ein Gerät über die GUI auf ein anderes Profil gesetzt werden kann,
- bestehende Gateway-Regeln dafür verwendet werden,
- ein fehlgeschlagener Profilwechsel zurückgerollt wird,
- effektive Regeln je Gerät sichtbar sind,
- rohe Systemregeln nur lesend angezeigt werden,
- keine freien Shell- oder Firewallbefehle aus der GUI möglich sind,
- alle schreibenden Änderungen auf dem Pi validiert werden,
- private Schlüssel nicht geloggt werden,
- parallele Konfliktaktionen gesperrt werden,
- Zustände nach einem Neustart wiederhergestellt werden.

### 26.2 Serververwaltung

- doppelte IDs werden verhindert,
- Endpoint und Public Key werden validiert,
- aktiver Server kann nicht gelöscht werden,
- Änderungen werden atomar gespeichert,
- beschädigte Serverdatei wird nicht überschrieben,
- Backups werden angelegt.

### 26.3 Geräteverwaltung

- unbekannte Geräte werden abgewiesen,
- unbekannte Profile werden abgewiesen,
- Profilwechsel verändert keine anderen Geräte unbeabsichtigt,
- erwartete und tatsächliche Regeln können verglichen werden,
- Inkonsistenzen werden sichtbar gemeldet.

---

## 27. Harte Vorgaben für den Coding-Agenten

Der Agent soll:

- zuerst den bestehenden Code lesen,
- das Projekt erweitern statt neu zu erfinden,
- die GUI von Beginn an erstellen,
- Pi und GUI als getrennte Anwendungen organisieren,
- eine stabile JSON-CLI definieren,
- Server-CRUD über validierte Pi-Befehle umsetzen,
- Profiländerungen über bestehende Gateway-Logik umsetzen,
- effektive Regeln lesbar und gerätebezogen ausgeben,
- `wg syncconf` für den normalen Serverwechsel bevorzugen,
- Endpoint und Public Key gemeinsam wechseln,
- `loguru` verwenden,
- keine übermäßigen Logs erzeugen,
- geparste Datenstrukturen nur auf `DEBUG` loggen,
- Subprozesse ohne `shell=True` starten,
- Timeouts setzen,
- Rückgabecodes prüfen,
- temporäre Dateien mit sicheren Rechten erstellen,
- alle schreibenden Dateizugriffe atomar ausführen,
- Backups vor Änderungen anlegen,
- Locking verwenden,
- Rollback für VPN- und Profiländerungen implementieren,
- GUI-Netzwerkzugriffe außerhalb des UI-Threads ausführen,
- klare ViewModels oder Controller verwenden,
- Unit-, Integrations- und End-to-End-Tests erstellen.

Der Agent soll nicht:

- die GUI erst auf später verschieben,
- einen Webserver auf dem Pi einführen,
- Konfigurationsdateien direkt aus der GUI überschreiben,
- beliebige Shell-Befehle erlauben,
- beliebige Firewallregeln editierbar machen,
- beim normalen Serverwechsel `wg0` herunterfahren,
- bei jedem Endpoint-Wechsel pauschal alle Gateway-Regeln neu anwenden,
- bestehende Profile ungeprüft umdefinieren,
- den aktiven Server löschen,
- private WireGuard-Schlüssel in Serverdateien kopieren,
- vollständige geheime Konfigurationen loggen,
- fehlgeschlagene Health-Checks ignorieren,
- Rollback-Fehler verbergen.

---

## 28. Zu verifizierende Punkte

Beim Start muss der Agent prüfen:

- tatsächlicher Projektpfad,
- vorhandene Python-Dateien,
- vorhandene Geräte- und Profilkonfiguration,
- tatsächliche Bedeutung von `Normal`, `VPN` und `Sicher`,
- tatsächlicher Name des WireGuard-Interfaces,
- WireGuard-Startmethode,
- aktuelle `AllowedIPs`,
- aktueller `PersistentKeepalive`,
- Anzahl der Peers,
- Routing-Tabelle `100`,
- aktuelle `ip rule`,
- Firewalltechnik,
- DNS-Regeln,
- NAT-Regeln,
- Kill-Switch-Implementierung,
- IPv6-Zustand,
- vorhandene systemd-Units,
- bestehender SSH-Zugriff,
- vorhandene sudoers-Regeln,
- ob Geräte über ID, IP oder MAC identifiziert werden,
- ob IP-Adressen statisch oder per DHCP vergeben werden,
- wie das bestehende Projekt Profiländerungen speichert,
- wie bestehende Regeln angewendet und geprüft werden,
- ob bereits eine `servers.json` existiert,
- ob Mullvad-Konfigurationen bereits lokal vorliegen.

Diese Punkte soll der Agent nach Möglichkeit direkt aus dem Projekt und dem Pi ermitteln, statt sie zu erfinden.

---

## 29. Erwarteter erster Arbeitsauftrag an Codex

Der Agent soll in dieser Reihenfolge beginnen:

1. Repository und Pi-Konfiguration analysieren.
2. reale Ist-Struktur dokumentieren.
3. Abweichungen zu dieser Architektur auflisten.
4. gemeinsames Monorepo oder klare Pi-/GUI-Ordnerstruktur herstellen.
5. Pi-CLI mit `version` und `status` erstellen.
6. PySide6-GUI mit Verbindungseinstellungen und Dashboard erstellen.
7. SSH-Kommunikation und JSON-Protokoll implementieren.
8. Serverliste und Server-CRUD auf Pi und GUI implementieren.
9. WireGuard-Wechsel mit Health-Check und Rollback implementieren.
10. Geräteauflistung und Profilwechsel implementieren.
11. effektive Regeln pro Gerät implementieren.
12. Tests und Installation ergänzen.

Nach jedem Abschnitt sollen die bestehenden Gateway-Funktionen weiter funktionieren.

---

## 30. Zusammenfassung

Das bisherige Raspberry-Pi-VPN-Gateway wird um eine vollständige PC-Verwaltungsoberfläche erweitert.

Die neue PySide6-GUI wird von Beginn an mitgebaut und bietet:

- VPN-Status,
- Serverauswahl,
- Server hinzufügen,
- Server bearbeiten,
- Server löschen,
- Geräteübersicht,
- Profilwechsel pro Gerät,
- Anzeige der effektiven Regeln.

Der Raspberry Pi bleibt für alle technischen und sicherheitsrelevanten Entscheidungen zuständig.

Der VPN-Endpoint wird im laufenden WireGuard-Interface gewechselt. Geräteprofile werden über die vorhandene Gateway- und Regelimplementierung angewendet. Die GUI zeigt technische Regeln zur Diagnose an, bearbeitet aber keine rohen Firewall- oder Routingbefehle.

Damit entsteht eine klare Trennung:

```text
GUI = Bedienung und Darstellung
Pi-CLI = validierte Verwaltungsschnittstelle
Services = Geschäftslogik
bestehende Gateway-Komponenten = Routing, Firewall, NAT und Profile
WireGuard = VPN-Tunnel
```
