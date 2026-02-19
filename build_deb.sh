#!/bin/bash
# ─────────────────────────────────────────────
# build_deb.sh — 构建 WinVX .deb 安装包
# 用法: bash build_deb.sh
# 输出: winvx_1.0.0_all.deb
# ─────────────────────────────────────────────

set -e

VERSION="1.0.0"
PKG_NAME="winvx"
ARCH="all"
PKG_DIR="${PKG_NAME}_${VERSION}_${ARCH}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔨 构建 WinVX v${VERSION} .deb 包..."

# 清理旧构建
rm -rf "$SCRIPT_DIR/$PKG_DIR"
rm -f "$SCRIPT_DIR/${PKG_DIR}.deb"

# ── 1. 创建目录结构 ──────────────────────────
mkdir -p "$SCRIPT_DIR/$PKG_DIR/DEBIAN"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/opt/winvx"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/usr/bin"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/usr/share/applications"
mkdir -p "$SCRIPT_DIR/$PKG_DIR/etc/xdg/autostart"

# ── 2. DEBIAN/control ────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/control" << 'EOF'
Package: winvx
Version: 1.0.0
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, python3-evdev, xdotool
Recommends: wl-clipboard, xclip
Maintainer: WinVX <winvx@github.com>
Description: Windows 11 风格剪贴板管理器 (Win+V)
 WinVX 是一个 Linux 原生的剪贴板历史管理器，
 复刻了 Windows 11 的 Win+V 体验。
 .
 功能:
  - 自动记录文本和图片剪贴板历史
  - 深色主题浮动弹窗 UI
  - 搜索过滤、置顶、点击粘贴
  - 键盘导航 (↑↓ Enter Esc)
  - 自动检测桌面环境并绑定快捷键
  - 支持 X11 和 Wayland
  - 单实例运行
EOF

# ── 3. 安装后脚本 ─────────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e

# 创建数据目录
USER_HOME=$(eval echo ~${SUDO_USER:-$USER})
DATA_DIR="$USER_HOME/.local/share/winvx"
mkdir -p "$DATA_DIR/images"
chown -R "${SUDO_USER:-$USER}":"${SUDO_USER:-$USER}" "$DATA_DIR"

# 确保 uinput 权限 (Wayland 粘贴需要)
if [ ! -f /etc/udev/rules.d/99-winvx-uinput.rules ]; then
    echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' > /etc/udev/rules.d/99-winvx-uinput.rules
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
fi
# 确保用户在 input 组
usermod -aG input "${SUDO_USER:-$USER}" 2>/dev/null || true

# 自动配置快捷键 (以用户身份运行)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    su - "$SUDO_USER" -c '/opt/winvx/winvx-setup --auto' 2>/dev/null || true
else
    /opt/winvx/winvx-setup --auto 2>/dev/null || true
fi

echo ""
echo "✓ WinVX 已安装!"
echo ""
echo "  启动: winvx"
echo "  切换: winvx --toggle"
echo "  重新配置快捷键: winvx-setup"
echo ""
echo "  重新登录后自动启动"
echo ""
POSTINST
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postinst"

# ── 4. 卸载前脚本 ─────────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e

# 停止运行中的实例
pkill -f "python3 /opt/winvx/main.py" 2>/dev/null || true
rm -f /tmp/winvx.sock 2>/dev/null || true

# 移除快捷键 (以用户身份运行)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    su - "$SUDO_USER" -c '/opt/winvx/winvx-setup --remove' 2>/dev/null || true
fi
PRERM
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/prerm"

# ── 5. 卸载后清理 ─────────────────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
if [ "$1" = "purge" ]; then
    rm -f /etc/udev/rules.d/99-winvx-uinput.rules
    udevadm control --reload-rules 2>/dev/null || true
fi
POSTRM
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/postrm"

# ── 6. 复制 Python 源码 ──────────────────────
for f in main.py clip_store.py clipboard_monitor.py clipboard_ui.py session_helper.py; do
    cp "$SCRIPT_DIR/$f" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
done

# 复制配置工具
cp "$SCRIPT_DIR/winvx-setup" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
chmod 755 "$SCRIPT_DIR/$PKG_DIR/opt/winvx/winvx-setup"

# ── 7. 启动脚本 /usr/bin/winvx ───────────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx" << 'LAUNCHER'
#!/bin/bash
exec python3 /opt/winvx/main.py "$@"
LAUNCHER
chmod 755 "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx"

# ── 8. 配置工具链接 /usr/bin/winvx-setup ─────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx-setup" << 'SETUP_LAUNCHER'
#!/bin/bash
exec /opt/winvx/winvx-setup "$@"
SETUP_LAUNCHER
chmod 755 "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx-setup"

# ── 9. 桌面文件 (.desktop) ───────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/share/applications/winvx.desktop" << 'DESKTOP'
[Desktop Entry]
Name=WinVX Clipboard Manager
Name[zh_CN]=WinVX 剪贴板管理器
Comment=Windows 11 style clipboard history (Win+V)
Comment[zh_CN]=Windows 11 风格剪贴板历史 (Win+V)
Exec=winvx
Icon=edit-paste
Terminal=false
Type=Application
Categories=Utility;GTK;
Keywords=clipboard;paste;history;
StartupNotify=false
DESKTOP

# ── 10. 自启动文件 ────────────────────────────
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

# ── 11. 设置文件权限 ──────────────────────────
find "$SCRIPT_DIR/$PKG_DIR/opt" -type f -name "*.py" -exec chmod 644 {} \;
chmod 755 "$SCRIPT_DIR/$PKG_DIR/opt/winvx/main.py"

# ── 12. 构建 .deb ────────────────────────────
dpkg-deb --build --root-owner-group "$SCRIPT_DIR/$PKG_DIR"

echo ""
echo "✅ 构建成功: ${PKG_DIR}.deb"
echo ""
echo "安装: sudo dpkg -i ${PKG_DIR}.deb && sudo apt-get install -f"
echo "卸载: sudo dpkg -r winvx"

# 清理构建目录
rm -rf "$SCRIPT_DIR/$PKG_DIR"
