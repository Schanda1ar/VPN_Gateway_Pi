#!/bin/sh
set -eu

# Installs the new management surface without changing the active boot service by default.
# Run with --activate only after the integration tests have succeeded on the target Pi.

ACTIVATE=0
HARDEN_SUDO=0
for argument in "$@"; do
    case "$argument" in
        --activate) ACTIVATE=1 ;;
        --harden-sudo) HARDEN_SUDO=1 ;;
        *) echo "Unknown argument: $argument" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
INSTALL_ROOT=/opt/vpn-gateway
LEGACY_DIR=${VPN_GATEWAY_LEGACY_DIR:-/home/pi/vpn-gateway}

install -d -m 0750 "$INSTALL_ROOT" "$INSTALL_ROOT/config" "$INSTALL_ROOT/state" "$INSTALL_ROOT/logs" "$INSTALL_ROOT/backups"
python3 -m venv "$INSTALL_ROOT/venv"
"$INSTALL_ROOT/venv/bin/pip" install --disable-pip-version-check --upgrade --force-reinstall "$REPOSITORY_ROOT"
install -o root -g root -m 0755 "$SCRIPT_DIR/vpn-gateway-cli" /usr/local/bin/vpn-gateway-cli

# Keep the current service entry point but replace it atomically with the
# hardened compatible implementation. Runtime configuration files are never copied.
if [ -f "$LEGACY_DIR/main.py" ]; then
    if [ ! -f "$LEGACY_DIR/main.py.pre-vpn-gateway-cli" ]; then
        cp -p "$LEGACY_DIR/main.py" "$LEGACY_DIR/main.py.pre-vpn-gateway-cli"
    fi
    install -m 0644 "$REPOSITORY_ROOT/main.py" "$LEGACY_DIR/main.py.vpn-gateway-new"
    mv "$LEGACY_DIR/main.py.vpn-gateway-new" "$LEGACY_DIR/main.py"
fi

# This command only reads wg0 and writes new application-owned state.
/usr/local/bin/vpn-gateway-cli server migrate-current --json

if [ "$ACTIVATE" -eq 1 ]; then
    ACTIVE_UNIT=/etc/systemd/system/vpn-gateway.service
    UNIT_BACKUP=${ACTIVE_UNIT}.pre-vpn-gateway-cli
    TEMP_UNIT=${ACTIVE_UNIT}.vpn-gateway-new.service
    install -o root -g root -m 0644 "$SCRIPT_DIR/../systemd/vpn-gateway-restore.service" "$TEMP_UNIT"
    systemd-analyze verify "$TEMP_UNIT"
    if [ ! -f "$UNIT_BACKUP" ] && [ -f "$ACTIVE_UNIT" ]; then
        cp -p "$ACTIVE_UNIT" "$UNIT_BACKUP"
    fi
    mv "$TEMP_UNIT" "$ACTIVE_UNIT"
    systemctl daemon-reload
    systemctl enable vpn-gateway.service
    if ! systemctl restart vpn-gateway.service; then
        if [ -f "$UNIT_BACKUP" ]; then
            cp -p "$UNIT_BACKUP" "$ACTIVE_UNIT"
            systemctl daemon-reload
            systemctl restart vpn-gateway.service || true
        fi
        echo "Restore service failed; the previous service definition was restored." >&2
        exit 1
    fi
fi

if [ "$HARDEN_SUDO" -eq 1 ]; then
    LEGACY_SUDOERS=/etc/sudoers.d/010_pi-nopasswd
    RESTRICTED_SUDOERS=/etc/sudoers.d/vpn-gateway-cli
    TEMP_SUDOERS=${RESTRICTED_SUDOERS}.tmp
    BACKUP_SUDOERS=${LEGACY_SUDOERS}.vpn-gateway-backup
    cat >"$TEMP_SUDOERS" <<'EOF'
pi ALL=(root) NOPASSWD: /usr/local/bin/vpn-gateway-cli *
EOF
    chmod 0440 "$TEMP_SUDOERS"
    visudo -cf "$TEMP_SUDOERS"
    if [ -f "$LEGACY_SUDOERS" ]; then
        cp -p "$LEGACY_SUDOERS" "$BACKUP_SUDOERS"
    fi
    mv "$TEMP_SUDOERS" "$RESTRICTED_SUDOERS"
    if ! visudo -cf /etc/sudoers; then
        rm -f "$RESTRICTED_SUDOERS"
        if [ -f "$BACKUP_SUDOERS" ]; then
            mv "$BACKUP_SUDOERS" "$LEGACY_SUDOERS"
        fi
        echo "sudoers validation failed; the broad rule was restored." >&2
        exit 1
    fi
    # The verified Pi currently stores the broad rule in this dedicated file.
    # Retain a root-only backup so recovery does not require an SSH password.
    rm -f "$LEGACY_SUDOERS"
fi

echo "VPN Gateway CLI installed."
