#!/bin/bash
# WinVX Installation Script

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 WinVX Installation Script"
echo "─────────────────────"

# Check dependencies
echo "Checking dependencies..."
deps_ok=true

python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null \
    && echo "  ✓ GTK3 (python3-gi)" \
    || { echo "  ✗ GTK3 — sudo apt install python3-gi gir1.2-gtk-3.0"; deps_ok=false; }

python3 -c "from PIL import Image" 2>/dev/null \
    && echo "  ✓ Pillow" \
    || echo "  ⚠ Pillow (Optional, for advanced image processing) — sudo apt install python3-pil"

which xdotool >/dev/null 2>&1 \
    && echo "  ✓ xdotool" \
    || { echo "  ✗ xdotool — sudo apt install xdotool"; deps_ok=false; }

which xclip >/dev/null 2>&1 \
    && echo "  ✓ xclip" \
    || echo "  ⚠ xclip (Optional) — sudo apt install xclip"

if [ "$deps_ok" = false ]; then
    echo ""
    echo "Please install missing dependencies and try again"
    exit 1
fi

# Create data directory
mkdir -p ~/.local/share/winvx/images
echo "✓ Data directory created: ~/.local/share/winvx/"

# Install autostart
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

# Update paths in desktop file
sed "s|Exec=.*|Exec=python3 ${SCRIPT_DIR}/main.py|" \
    "$SCRIPT_DIR/winvx.desktop" > "$AUTOSTART_DIR/winvx.desktop"
echo "✓ Added autostart: $AUTOSTART_DIR/winvx.desktop"

echo ""
echo "─────────────────────"
echo "✅ Installation complete!"
echo ""
echo "Usage:"
echo "  Start:    python3 ${SCRIPT_DIR}/main.py"
echo "  Hotkey:   Super+V opens clipboard history"
echo "  Toggle:   python3 ${SCRIPT_DIR}/main.py --toggle"
echo ""
echo "If Super+V is occupied by the desktop environment, please set in system settings:"
echo "  1. Remove default hotkey for Super+V"
echo "  2. Or set custom hotkey → Command: python3 ${SCRIPT_DIR}/main.py --toggle"
