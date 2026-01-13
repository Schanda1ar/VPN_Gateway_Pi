import subprocess
import sys
import json
from pathlib import Path
from loguru import logger

# --- BASIS SETUP ---
BASE_DIR = Path(__file__).resolve().parent
BASE_CONFIG_PATH = BASE_DIR / "config.json"

class GatewayManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.base_config = self._load_config(self.config_path)
        # Werte aus der config.json ziehen
        self.device_file = Path(self.base_config.get("device_file", BASE_DIR / "devices.json"))

        # VPN Tabelle und lokales Netzwerk aus der Basis-Konfiguration laden
        self.vpn_table = self.base_config.get("vpn_table_id", "100")
        self.local_net = self.base_config.get("local_network", "192.168.178.0/24")
        self.dry_run = self.base_config.get("dry_run")
        
        # Geräteinformationen laden
        self.devices = self._load_json(self.device_file)

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
            self._execute(["sudo", "iptables", "-A", "FORWARD", "-s", ip, "-d", self.local_net, "-j", "DROP"])
        
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