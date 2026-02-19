#!/bin/bash
# ─────────────────────────────────────────────
# build_deb.sh — Build WinVX .deb installation package
# Usage: bash build_deb.sh
# Output: winvx_1.0.0_all.deb
# ─────────────────────────────────────────────

set -e

VERSION="1.0.0"
PKG_NAME="winvx"
ARCH="all"
PKG_DIR="${PKG_NAME}_${VERSION}_${ARCH}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔨 Building WinVX v${VERSION} .deb package..."

# Clean up old builds
rm -rf "$SCRIPT_DIR/$PKG_DIR"
rm -f "$SCRIPT_DIR/${PKG_DIR}.deb"

# ── 1. Create Directory Structure ──────────────
mkdir -p "$SCRIPT_DIR/$PKG_DIR/DEBIAN"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/opt/winvx"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/usr/bin"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/usr/share/applications"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/etc/xdg/autostart"

# ── 2. DEBIAN/control ──────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/control" << 'EOF'
Package: winvx
Version: 1.0.0
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, python3-evdev, xdotool
Recommends: wl-clipboard, xclip
Maintainer: WinVX <winvx@github.com>
Description: Windows 11 style clipboard manager (Win+V)
 WinVX is a Linux native clipboard history manager,
 replicating the Windows 11 Win+V experience.
 .
 Features:
  - Automatically record text and image clipboard history
  - Dark theme floating popup UI
  - Search filtering, pinning, click to paste
  - Keyboard navigation (↑↓ Enter Esc)
  - Auto-detect desktop environment and bind hotkeys
  - Support X11 and Wayland
  - Single instance running
EOF

# ── 3. Post-Installation Script ────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e

# Create data directory
USER_HOME=$(eval echo ~${SUDO_USER:-$USER})
DATA_DIR="$USER_HOME/.local/share/winvx"
mkdir -p "$DATA_DIR/images"
chown -R "${SUDO_USER:-$USER}":"${SUDO_USER:-$USER}" "$DATA_DIR"

# Ensure uinput permissions (needed for Wayland paste)
if [ ! -f /etc/udev/rules.d/99-winvx-uinput.rules ]; then
    echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' > /etc/udev/rules.d/99-winvx-uinput.rules
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
fi
# Ensure user is in input group
usermod -aG input "${SUDO_USER:-$USER}" 2>/dev/null || true

# Automatically configure hotkeys (run as user)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    su - "$SUDO_USER" -c '/opt/winvx/winvx-setup --auto' 2>/dev/null || true
else
    /opt/winvx/winvx-setup --auto 2>/dev/null || true
fi

echo ""
echo "✓ WinVX installed!"
echo ""
echo "  Start: winvx"
echo "  Toggle: winvx --toggle"
echo "  Reconfigure hotkeys: winvx-setup"
echo ""
echo "  Auto-starts after re-login"
echo ""
POSTINST
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postinst"

# ── 4. Pre-Removal Script ──────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e

# Stop running instances
pkill -f "python3 /opt/winvx/main.py" 2>/dev/null || true
rm -f /tmp/winvx.sock 2>/dev/null || true

# Remove hotkeys (run as user)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    su - "$SUDO_USER" -c '/opt/winvx/winvx-setup --remove' 2>/dev/null || true
fi
PRERM
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/prerm"

# ── 5. Post-Removal Cleanup ────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
if [ "$1" = "purge" ]; then
    rm -f /etc/udev/rules.d/99-winvx-uinput.rules
    udevadm control --reload-rules 2>/dev/null || true
fi
POSTRM
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postrm"

# ── 6. Copy Python Source ──────────────────────
for f in main.py clip_store.py clipboard_monitor.py clipboard_ui.py session_helper.py; do
    cp "$SCRIPT_DIR/$f" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
done

# Copy configuration tool
cp "$SCRIPT_DIR/winvx-setup" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
chmod 755 "$SCRIPT_DIR/$PKG_DIR/opt/winvx/winvx-setup"

# ── 7. Startup Script /usr/bin/winvx ───────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx" << 'LAUNCHER'
#!/bin/bash
exec python3 /opt/winvx/main.py "$@"
LAUNCHER
chmod 755 "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx"

# ── 8. Configuration Tool Link /usr/bin/winvx-setup ─
cat > "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx-setup" << 'SETUP_LAUNCHER'
#!/bin/bash
exec /opt/winvx/winvx-setup "$@"
SETUP_LAUNCHER
chmod 755 "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx-setup"

# ── 9. Desktop File (.desktop) ─────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/share/applications/winvx.desktop" << 'DESKTOP'
[Desktop Entry]
Name=WinVX Clipboard Manager
Comment=Windows 11 style clipboard history (Win+V)
Exec=winvx
Icon=edit-paste
Terminal=false
Type=Application
Categories=Utility;GTK;
Keywords=clipboard;paste;history;
StartupNotify=false
DESKTOP

# ── 10. Autostart File ─────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/etc/xdg/autostart/winvx-autostart.desktop" << 'AUTOSTART'
[Desktop Entry]
Name=WinVX Clipboard Manager
Comment=Start WinVX clipboard manager
Exec=winvx
Terminal=false
Type=Application
X-GNOME-Autostart-enabled=true
NoDisplay=true
AUTOSTART

# ── 11. Set File Permissions ───────────────────
find "$SCRIPT_DIR/$PKG_DIR/opt" -type f -name "*.py" -exec chmod 644 {} \;
chmod 755 "$SCRIPT_DIR/$PKG_DIR/opt/winvx/main.py"

# ── 12. Build .deb ─────────────────────────────
dpkg-deb --build --root-owner-group "$SCRIPT_DIR/$PKG_DIR"

echo ""
echo "✅ Build successful: ${PKG_DIR}.deb"
echo ""
echo "Install: sudo dpkg -i ${PKG_DIR}.deb && sudo apt-get install -f"
echo "Uninstall: sudo dpkg -r winvx"

# Clean up build directory
rm -rf "$SCRIPT_DIR/$PKG_DIR"
