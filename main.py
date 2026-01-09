import subprocess
import sys
import json
from pathlib import Path
from loguru import logger

# --- BASIS SETUP ---
BASE_CONFIG_PATH = Path("config.json")

class GatewayManager:
    def __init__(self, config_path: Path):
        self.base_config = self._load_json(config_path)
        # Werte aus der config.json ziehen
        self.device_file = Path(self.base_config.get("device_file", "devices.json"))
        self.vpn_table = self.base_config.get("vpn_table_id", "100")
        self.local_net = self.base_config.get("local_network", "192.168.178.0/24")
        
        # Jetzt die Geräte laden
        self.devices = self._load_json(self.device_file)

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            logger.error(f"Datei nicht gefunden: {path}")
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Fehler beim Laden von {path}: {e}")
            return {}

    # ... (Rest der apply_profile Methode nutzt nun self.vpn_table etc.) ...

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            logger.error(f"Konfigurationsdatei {self.config_path} fehlt!")
            return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Fehler beim Laden der JSON: {e}")
            return {}

    def _save_config(self):
        """Speichert den aktuellen Zustand der Profile in der JSON."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.devices, f, indent=4, ensure_ascii=False)
            logger.debug("Konfiguration gespeichert.")
        except Exception as e:
            logger.error(f"Fehler beim Speichern der JSON: {e}")

    def _execute(self, cmd: list):
        return subprocess.run(cmd, capture_output=True, text=True)

    def apply_profile(self, ip: str, profile: str, update_json=True):
        if ip not in self.devices:
            logger.error(f"IP {ip} unbekannt.")
            return

        # Reset (Alte Regeln entfernen)
        self._execute(["sudo", "ip", "rule", "del", "from", ip, "table", self.vpn_table])
        self._execute(["sudo", "iptables", "-D", "FORWARD", "-s", ip, "-d", self.local_net, "-j", "DROP"])

        if profile == "VPN":
            self._execute(["sudo", "ip", "rule", "add", "from", ip, "table", self.vpn_table])
        elif profile == "Sicher":
            self._execute(["sudo", "ip", "rule", "add", "from", ip, "table", self.vpn_table])
            self._execute(["sudo", "iptables", "-A", "FORWARD", "-s", ip, "-d", self.local_net, "-j", "DROP"])
        
        if update_json:
            self.devices[ip]["profile"] = profile
            self._save_config()
            
        logger.info(f"Profil '{profile}' aktiv für {self.devices[ip]['name']} ({ip})")

    def init_all_devices(self):
        """Wird beim Reboot gerufen."""
        for ip, data in self.devices.items():
            self.apply_profile(ip, data["profile"], update_json=False)
        logger.success("Alle Profile nach Reboot wiederhergestellt.")

if __name__ == "__main__":
    manager = GatewayManager(BASE_CONFIG_PATH)

    if len(sys.argv) == 2 and sys.argv[1] == "--all":
        manager.init_all_devices()
    elif len(sys.argv) == 3:
        manager.apply_profile(sys.argv[1], sys.argv[2])
    else:
        print("Nutzung: python3 gateway_manager.py <IP> <Profil>  ODER  --all")