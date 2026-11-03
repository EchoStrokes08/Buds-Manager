#!/usr/bin/env bash
# Buds Manager: instala (o desinstala con --uninstall) para el usuario actual.
#   ./install.sh               enlaces en ~/.local/bin, servicios de usuario, lanzador y regla udev
#   ./install.sh --uninstall   lo quita todo (tu configuración en ~/.config/redmi-buds se conserva)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
APPS="$HOME/.local/share/applications"
APP_ID="io.github.echostrokes08.BudsManager"
UDEV="/etc/udev/rules.d/70-redmi-buds.rules"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now buds-tray.service buds-manager-daemon.service 2>/dev/null || true
    rm -f "$UNITS/buds-tray.service" "$UNITS/buds-manager-daemon.service"
    systemctl --user daemon-reload
    rm -f "$BIN/buds" "$BIN/buds-manager" "$BIN/buds-tray" "$APPS/$APP_ID.desktop"
    if sudo -n -l /usr/bin/btmgmt conn-info -t 0 00:00:00:00:00:00 >/dev/null 2>&1; then
        sudo rm -f /etc/sudoers.d/71-redmi-buds-rssi
    fi
    if [[ -f "$UDEV" ]]; then
        say "Quitando la regla udev (pide tu contraseña)…"
        sudo rm -f "$UDEV" && sudo udevadm control --reload
    fi
    say "Desinstalado. Tu configuración sigue en ~/.config/redmi-buds."
    exit 0
fi

# --- dependencias ---
missing=()
command -v python3 >/dev/null || missing+=("python")
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null \
    || missing+=("python-gobject gtk4 libadwaita")
command -v bluetoothctl >/dev/null || missing+=("bluez-utils")
command -v pactl >/dev/null || missing+=("pipewire-pulse (o libpulse)")
command -v wpctl >/dev/null || missing+=("wireplumber")
if (( ${#missing[@]} )); then
    say "Faltan dependencias: ${missing[*]}"
    echo "En Arch: sudo pacman -S python-gobject gtk4 libadwaita bluez-utils pipewire-pulse wireplumber"
    exit 1
fi
command -v playerctl >/dev/null || echo "Opcional: playerctl (pausa automática y atajos multimedia)"
[[ -e /usr/lib/ladspa/librnnoise_ladspa.so ]] || echo "Opcional: noise-suppression-for-voice (quitar ruido en llamadas)"

# --- programas ---
mkdir -p "$BIN" "$UNITS" "$APPS"
for f in buds buds-manager buds-tray; do
    chmod +x "$SRC/$f"
    ln -sf "$SRC/$f" "$BIN/$f"
done
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Aviso: añade $BIN a tu PATH" ;; esac

# --- lanzador ---
install -m644 "$SRC/$APP_ID.desktop" "$APPS/$APP_ID.desktop"

# --- servicios de usuario ---
install -m644 "$SRC/buds-manager-daemon.service" "$SRC/buds-tray.service" "$UNITS/"
systemctl --user daemon-reload
systemctl --user enable --now buds-tray.service >/dev/null 2>&1 \
    || echo "Aviso: no pude activar el ícono de la bandeja (¿sesión sin graphical-session.target?)"
if systemctl --user is-enabled buds-manager-daemon.service >/dev/null 2>&1; then
    systemctl --user restart buds-manager-daemon.service
fi

# --- regla sudo: leer la señal Bluetooth (buscador «frío o caliente») ---
SUDOERS="/etc/sudoers.d/71-redmi-buds-rssi"
if ! sudo -n -l /usr/bin/btmgmt conn-info -t 0 00:00:00:00:00:00 >/dev/null 2>&1; then
    say "Permiso para leer la señal Bluetooth (solo «btmgmt conn-info»; pide tu contraseña)…"
    if visudo -cf "$SRC/71-redmi-buds-rssi.sudoers" >/dev/null && \
       sudo install -m440 "$SRC/71-redmi-buds-rssi.sudoers" "$SUDOERS"; then
        :
    else
        echo "Sin esta regla, el buscador «frío o caliente» no mide la señal (lo demás funciona)."
    fi
fi

# --- regla udev (atajos con los gestos) ---
if ! cmp -s "$SRC/70-redmi-buds.rules" "$UDEV" 2>/dev/null; then
    say "Instalando la regla udev para los gestos (pide tu contraseña)…"
    if sudo install -m644 "$SRC/70-redmi-buds.rules" "$UDEV"; then
        sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=input
    else
        echo "Sin la regla udev los atajos de gestos en la PC no funcionarán (todo lo demás sí)."
    fi
fi

say "Listo."
echo "  buds-manager      abre la app (también en tu lanzador)"
echo "  buds              estado en la terminal (buds --help para todo)"
echo "  buds hold on      mantener el control en esta PC: activa automatizaciones, gestos y perfiles"
