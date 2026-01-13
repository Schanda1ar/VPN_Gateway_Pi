import subprocess
import sys
import json
import os
import time
import platform

from pathlib import Path
from loguru import logger

# --- BASIS SETUP ---
BASE_DIR = Path(__file__).resolve().parent
BASE_CONFIG_PATH = BASE_DIR / "config.json"

class GatewayManager:
    def __init__(self, config_path: Path):

        
        # 3. Konfiguration laden
        self.config_path = config_path
        self.base_config = self._load_config(self.config_path)
        # Werte aus der config.json ziehen
        self.device_file = Path(self.base_config.get("device_file", BASE_DIR / "devices.json"))

        # VPN Tabelle und lokales Netzwerk aus der Basis-Konfiguration laden
        self.vpn_table = self.base_config.get("vpn_table_id", "100")
        self.local_net = self.base_config.get("local_network", "192.168.178.0/24")
        is_linux = platform.system() == "Linux"
        self.dry_run = self.base_config.get("dry_run", False) or not is_linux
        
        # Geräteinformationen laden
        self.devices = self._load_json(self.device_file)
        if self.dry_run is not True:
            self.prepare_system()

    def prepare_system(self):
        logger.info("Starte Initialisierung (WireGuard Modus)...")

        # 1. Warten bis WireGuard bereit ist (max 45 Sek)
        found_iface = None
        for attempt in range(15):
            found_iface = self._get_vpn_interface()
            if found_iface:
                self.vpn_iface = found_iface
                logger.success(f"WireGuard Interface '{self.vpn_iface}' ist bereit.")
                break
            logger.info(f"Warte auf WireGuard... (Versuch {attempt+1}/15)")
            time.sleep(3)

        if not self.vpn_iface:
            logger.critical("WireGuard Interface wurde nicht gefunden! Abbruch.")
            return

        # 2. Infrastruktur mit dem gefundenen Interface setzen
        self._ensure_ip_forwarding()
        self._setup_nat(self.vpn_iface)


    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            logger.info(f"Datei nicht gefunden. Erstelle neue Konfiguration unter: {path}")
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump({}, f, indent=4)
                return {}
            except Exception as e:
                logger.error(f"Fehler beim Erstellen der Datei {path}: {e}")
                return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Fehler beim Laden von {path}: {e}")
            return {}

    # ... (Rest der apply_profile Methode nutzt nun self.vpn_table etc.) ...

    def _load_config(self, path: Path) -> dict:
        if not self.config_path.exists():
            logger.error(f"Konfigurationsdatei {self.config_path} fehlt!")
            return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Fehler beim Laden der JSON: {e}")
            return {}

    def _save_device_config(self):
        """Speichert den aktuellen Zustand der Profile in der JSON."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Speichere JSON nach {self.device_file}")
            # Optional: Trotzdem speichern, um die Dateilogik zu testen
            with open(self.device_file, 'w') as f:
                json.dump(self.devices, f, indent=4)
        else:
            try:
                with open(self.device_file, 'w', encoding='utf-8') as f:
                    json.dump(self.devices, f, indent=4, ensure_ascii=False)
                logger.debug("Konfiguration gespeichert.")
            except Exception as e:
                logger.error(f"Fehler beim Speichern der JSON: {e}")

    def _get_vpn_interface(self) -> str:
        """Findet das aktive WireGuard Interface (z.B. wg0)."""
        try:
            interfaces = os.listdir('/sys/class/net/')
            # Suche primär nach wg-Interfaces, fallback auf tun
            vpn_ifs = [i for i in interfaces if i.startswith('wg')]
            if not vpn_ifs:
                vpn_ifs = [i for i in interfaces if i.startswith('tun')]
                
            return vpn_ifs[0] if vpn_ifs else None
        except Exception as e:
            logger.error(f"Fehler beim Lesen der Interfaces: {e}")
            return None
        
    def _ensure_ip_forwarding(self):
        """Aktiviert das IP-Forwarding im Linux-Kernel."""
        logger.debug("Prüfe IP-Forwarding Status...")
        # Befehl zum Aktivieren des Forwardings
        cmd = ["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"]
        
        if self.dry_run:
            logger.info(f"[Dry-Run] Würde IP-Forwarding aktivieren: {' '.join(cmd)}")
        else:
            result = self._execute(cmd)
            if result and result.returncode == 0:
                logger.success("IP-Forwarding ist AKTIV.")
            else:
                logger.error("IP-Forwarding konnte nicht aktiviert werden!")

    def _setup_nat(self, vpn_iface: str):
        """Richtet das NAT Masquerading für das WireGuard-Interface ein."""
        # -C prüft, ob die Regel bereits existiert (verhindert Duplikate)
        check_cmd = ["sudo", "iptables", "-t", "nat", "-C", "POSTROUTING", "-o", vpn_iface, "-j", "MASQUERADE"]
        # -A fügt die Regel hinzu
        add_cmd = ["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING", "-o", vpn_iface, "-j", "MASQUERADE"]

        if self.dry_run:
            logger.info(f"[Dry-Run] Würde NAT für {vpn_iface} prüfen/aktivieren.")
            return

        # Erst prüfen, ob die Regel schon da ist
        check_result = self._execute(check_cmd)
        
        if check_result and check_result.returncode != 0:
            logger.info(f"NAT für {vpn_iface} nicht gefunden. Aktiviere...")
            self._execute(add_cmd)
            logger.success(f"NAT Masquerading für {vpn_iface} wurde aktiviert.")
        else:
            logger.debug(f"NAT für {vpn_iface} ist bereits konfiguriert.")

    def _execute(self, cmd: list):
        """Führt einen Shell-Befehl aus und gibt das Ergebnis zurück."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Executing: {cmd}")
            return None # Simuliere Erfolg
        else:
            try:
                # check=False verhindert den Absturz bei Fehlern (z.B. Regel nicht gefunden)
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    logger.debug(f"Info: Befehl {cmd[1:3]} nicht kritisch: {result.stderr.strip()}")
                return result
            except Exception as e:
                logger.error(f"Kritischer Fehler bei Systemaufruf: {e}")

    def apply_profile(self, ip: str, profile: str, name: str, update_json:bool):
        """
        Wendet ein Routing-Profil auf eine IP an. 
        Legt das Gerät an, falls es noch nicht existiert.
        """
        if ip not in self.devices and update_json is True:
            logger.info(f"Neues Gerät erkannt. Initialisiere {ip}...")
            self.devices[ip] = {"name": name if name else ip, "profile": profile}
        elif update_json is True:
            if profile == "Normal":
                logger.info(f"Setze Profil für {self.devices[ip]['name']} ({ip}) auf 'Normal' und entferne alle Regeln.")
                self.devices[ip]["profile"] = profile
                
            else:
                logger.info(f"Aktualisiere Profil für {self.devices[ip]['name']} ({ip}) von {self.devices[ip]['profile']} zu {profile}")
                self.devices[ip]["profile"] = profile
        else:
            logger.info(f"Initialisiere Profile für {ip}.")

        # Vorherige Regeln entfernen um Konflikte zu vermeiden
        self._execute(["sudo", "ip", "rule", "del", "from", ip, "table", self.vpn_table])
        self._execute(["sudo", "iptables", "-D", "FORWARD", "-s", ip, "-d", self.local_net, "-j", "DROP"])

        # Neue Regeln basierend auf dem Profil anwenden
        if profile == "VPN":
            self._execute(["sudo", "ip", "rule", "add", "from", ip, "table", self.vpn_table])
        elif profile == "Sicher":
            self._execute(["sudo", "ip", "rule", "add", "from", ip, "table", self.vpn_table])
            self._execute(["sudo", "iptables", "-I", "FORWARD", "-s", ip, "-d", self.local_net, "-j", "DROP"])
        
        if update_json:

            self._save_device_config()
            
        logger.info(f"Profil '{profile}' aktiv für {self.devices[ip]['name']} ({ip})")

    def init_all_devices(self):
        """Wird beim Reboot gerufen."""
        for ip, data in self.devices.items():
            current_name = data.get("name", "Unknown")
            self.apply_profile(ip, data["profile"], name=current_name, update_json=False)
        logger.success("Alle Profile nach Reboot wiederhergestellt.")

if __name__ == "__main__":


    manager = GatewayManager(BASE_CONFIG_PATH)
    if len(sys.argv) == 2 and sys.argv[1] == "--all":
        manager.init_all_devices()
    
    elif len(sys.argv) == 1:
        print("--- Gateway Manager Interaktiv ---")
        print(" Erstelle neues Profil für das Gerät mit IP-Adresse, Profil und optionalem Namen.")
        print("-" * 34)
        
        try:
            ip = input("IP-Adresse des Geräts: ").strip()
            if not ip:
                raise ValueError("Die IP darf nicht leer sein.")
                
            profile = input("Profil (Normal/VPN/Sicher): ").strip()
            name = input("Name für dieses Gerät (optional): ").strip()

            if not name:
                name = ip  # Standardname ist die IP-Adresse
            
            # Falls Name leer bleibt, None übergeben (apply_profile regelt das)
            manager.apply_profile(ip, profile, name )
            
        except KeyboardInterrupt:
            print("\nAbgebrochen durch Nutzer.")
        except Exception as e:
            logger.error(f"Eingabefehler: {e}")

    # Fall 2: Neues Profil anwenden (IP Profil [Name])
    elif len(sys.argv) == 4:
        ip_arg = sys.argv[1]
        profile_arg = sys.argv[2]
        # Optionaler Name, falls vorhanden
        name_arg = sys.argv[3] if len(sys.argv) == 4 else None
        
        manager.apply_profile(ip_arg, profile_arg, name_arg)

    else:
        print("Nutzung:")
        print("  python3 script.py --all")
        print("  python3 script.py <IP> <Profil> [Name]")