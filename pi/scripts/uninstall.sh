#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this uninstaller as root." >&2
    exit 1
fi

ACTIVE_UNIT=/etc/systemd/system/vpn-gateway.service
UNIT_BACKUP=${ACTIVE_UNIT}.pre-vpn-gateway-cli
LEGACY_DIR=${VPN_GATEWAY_LEGACY_DIR:-/home/pi/vpn-gateway}
LEGACY_MAIN=$LEGACY_DIR/main.py
LEGACY_MAIN_BACKUP=${LEGACY_MAIN}.pre-vpn-gateway-cli
RESTORED_UNIT=0

if [ -f "$UNIT_BACKUP" ]; then
    systemctl stop vpn-gateway.service 2>/dev/null || true
    cp -p "$UNIT_BACKUP" "$ACTIVE_UNIT"
    rm -f "$UNIT_BACKUP"
    RESTORED_UNIT=1
fi

if [ -f "$LEGACY_MAIN_BACKUP" ]; then
    cp -p "$LEGACY_MAIN_BACKUP" "$LEGACY_MAIN"
    rm -f "$LEGACY_MAIN_BACKUP"
fi

rm -f /usr/local/bin/vpn-gateway-cli /etc/sudoers.d/vpn-gateway-cli
systemctl daemon-reload

if [ "$RESTORED_UNIT" -eq 1 ]; then
    systemctl enable vpn-gateway.service
    systemctl restart vpn-gateway.service
fi

echo "Legacy gateway files were restored when backups existed; /opt/vpn-gateway data and the root-only sudoers backup were retained."
