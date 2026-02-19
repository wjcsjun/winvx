#!/bin/bash
# WinVX 安装脚本

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 WinVX 安装脚本"
echo "─────────────────────"

# 检查依赖
echo "检查依赖..."
deps_ok=true

python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null \
    && echo "  ✓ GTK3 (python3-gi)" \
    || { echo "  ✗ GTK3 — sudo apt install python3-gi gir1.2-gtk-3.0"; deps_ok=false; }

python3 -c "from PIL import Image" 2>/dev/null \
    && echo "  ✓ Pillow" \
    || echo "  ⚠ Pillow (可选, 用于高级图片处理) — sudo apt install python3-pil"

which xdotool >/dev/null 2>&1 \
    && echo "  ✓ xdotool" \
    || { echo "  ✗ xdotool — sudo apt install xdotool"; deps_ok=false; }

which xclip >/dev/null 2>&1 \
    && echo "  ✓ xclip" \
    || echo "  ⚠ xclip (可选) — sudo apt install xclip"

if [ "$deps_ok" = false ]; then
    echo ""
    echo "请先安装缺失的依赖后重试"
    exit 1
fi

# 创建数据目录
mkdir -p ~/.local/share/winvx/images
echo "✓ 数据目录已创建: ~/.local/share/winvx/"

# 安装自启动
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

# 更新 desktop 文件中的路径
sed "s|Exec=.*|Exec=python3 ${SCRIPT_DIR}/main.py|" \
    "$SCRIPT_DIR/winvx.desktop" > "$AUTOSTART_DIR/winvx.desktop"
echo "✓ 已添加开机自启: $AUTOSTART_DIR/winvx.desktop"

echo ""
echo "─────────────────────"
echo "✅ 安装完成!"
echo ""
echo "使用方法:"
echo "  启动:    python3 ${SCRIPT_DIR}/main.py"
echo "  快捷键:  Super+V 打开剪贴板历史"
echo "  切换:    python3 ${SCRIPT_DIR}/main.py --toggle"
echo ""
echo "如果 Super+V 被桌面环境占用, 请在系统设置中:"
echo "  1. 移除 Super+V 的默认快捷键"
echo "  2. 或者设置自定义快捷键 → 命令: python3 ${SCRIPT_DIR}/main.py --toggle"
