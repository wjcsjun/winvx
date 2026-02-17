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
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, libxtst6, xdotool
Recommends: xclip
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
  - 全局快捷键 Super+V
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

# 注册 GNOME 快捷键 (如果是 GNOME)
if command -v gsettings &>/dev/null; then
    DESKTOP=$(su - "${SUDO_USER:-$USER}" -c 'echo $XDG_CURRENT_DESKTOP' 2>/dev/null || echo "")
    if echo "$DESKTOP" | grep -qiE "gnome|ubuntu|unity"; then
        su - "${SUDO_USER:-$USER}" -c '
            python3 /opt/winvx/main.py --bind 2>/dev/null || true
        ' 2>/dev/null || true
    fi
fi

echo ""
echo "✓ WinVX 已安装!"
echo ""
echo "  启动: winvx"
echo "  切换: winvx --toggle"
echo "  快捷键: Super+V (GNOME 已自动注册)"
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

# 移除 GNOME 快捷键
if command -v gsettings &>/dev/null; then
    DESKTOP=$(su - "${SUDO_USER:-$USER}" -c 'echo $XDG_CURRENT_DESKTOP' 2>/dev/null || echo "")
    if echo "$DESKTOP" | grep -qiE "gnome|ubuntu|unity"; then
        su - "${SUDO_USER:-$USER}" -c '
            EXISTING=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null)
            if echo "$EXISTING" | grep -q winvx; then
                NEW=$(echo "$EXISTING" | sed "s|, *\x27/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winvx/\x27||g" | sed "s|\x27/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winvx/\x27, *||g" | sed "s|\x27/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winvx/\x27||g")
                gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW" 2>/dev/null || true
            fi
        ' 2>/dev/null || true
    fi
fi
PRERM
chmod 755 "$SCRIPT_DIR/$PKG_DIR/DEBIAN/prerm"

# ── 5. 复制 Python 源码 ──────────────────────
cp "$SCRIPT_DIR/main.py" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
cp "$SCRIPT_DIR/clip_store.py" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
cp "$SCRIPT_DIR/clipboard_monitor.py" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"
cp "$SCRIPT_DIR/clipboard_ui.py" "$SCRIPT_DIR/$PKG_DIR/opt/winvx/"

# ── 6. 启动脚本 /usr/bin/winvx ───────────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx" << 'LAUNCHER'
#!/bin/bash
exec python3 /opt/winvx/main.py "$@"
LAUNCHER
chmod 755 "$SCRIPT_DIR/$PKG_DIR/usr/bin/winvx"

# ── 7. 桌面文件 (.desktop) ───────────────────
cat > "$SCRIPT_DIR/$PKG_DIR/usr/share/applications/winvx.desktop" << 'DESKTOP'
[Desktop Entry]
Name=WinVX Clipboard Manager
Name[zh_CN]=WinVX 剪贴板管理器
Comment=Windows 11 style clipboard history (Win+V)
Comment[zh_CN]=Windows 11 风格剪贴板历史 (Win+V)
Exec=winvx
Terminal=false
Type=Application
Categories=Utility;GTK;
Keywords=clipboard;paste;history;
StartupNotify=false
DESKTOP

# ── 8. 自启动文件 ────────────────────────────
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

# ── 9. 设置文件权限 ──────────────────────────
find "$SCRIPT_DIR/$PKG_DIR/opt" -type f -name "*.py" -exec chmod 644 {} \;
chmod 755 "$SCRIPT_DIR/$PKG_DIR/opt/winvx/main.py"

# ── 10. 构建 .deb ────────────────────────────
dpkg-deb --build --root-owner-group "$SCRIPT_DIR/$PKG_DIR"

echo ""
echo "✅ 构建成功: ${PKG_DIR}.deb"
echo ""
echo "安装: sudo dpkg -i ${PKG_DIR}.deb"
echo "卸载: sudo dpkg -r winvx"

# 清理构建目录
rm -rf "$SCRIPT_DIR/$PKG_DIR"
