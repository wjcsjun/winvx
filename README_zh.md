# WinVX

[English](README.md) | [中文](README_zh.md)

**WinVX** 是一款 Linux 原生的剪贴板历史管理器，旨在复刻 **Windows 11 (Win+V)** 的使用体验。它拥有现代化的深色主题 UI，支持文本和图片，并且在 **X11** 和 **Wayland** 下都能完美运行。

![WinVX Preview](https://github.com/wjcsjun/winvx/raw/main/preview.png) *(预览图占位)*

## ✨ 功能特性

- **Win+V 体验**: 熟悉的浮动弹窗，快速访问剪贴板历史。
- **文本与图片**: 自动记录剪贴板中的文字片段和图片。
- **现代化 UI**: 仿 Win11 深色主题，圆角设计，流畅交互。
- **搜索与过滤**: 内置搜索框，瞬间找回历史记录。
- **置顶功能**: 将重要条目固定在顶部，防止被覆盖。
- **Wayland & X11**: 完美支持双显示服务器，在 Wayland 下提供可靠的粘贴模拟。
- **自动快捷键**: 智能配置工具自动检测桌面环境 (GNOME, KDE, XFCE 等) 并绑定快捷键。

## 🚀 安装指南

### 1. 下载 .deb 安装包 (推荐)
在基于 Debian 的系统（如 Ubuntu, Linux Mint）上，最简单的安装方式是使用 `.deb` 包。你可以从 [GitHub Releases](https://github.com/wjcsjun/winvx/releases) 中下载，或者自行构建：

```bash
# 构建 .deb 包
bash build_deb.sh

# 安装包
sudo dpkg -i winvx_1.0.0_all.deb
sudo apt-get install -f
```

### 2. 源码安装
如果你希望直接从源码运行：

```bash
# 克隆仓库
git clone https://github.com/wjcsjun/winvx.git
cd winvx

# 安装依赖 (Ubuntu/Debian)
sudo apt install python3-gi gir1.2-gtk-3.0 python3-evdev xdotool wl-clipboard xclip

# 运行安装脚本
bash install.sh
```

## ⌨️ 使用方法

- **打开/切换**: 按下 `Super+V` (即 Win+V键)。
- **粘贴**: 点击列表项，或选定后按 `Enter`。
- **导航**: 使用方向键 `↑` `↓` 进行选择，`Esc` 键退出。
- **置顶/删除**: 鼠标悬停在条目上可看到操作按钮。

### 命令行接口
```bash
winvx          # 启动后台监控程序
winvx --toggle # 打开/关闭弹出界面
winvx-setup    # 重新配置快捷键
```

## 🛠️ 环境要求
- Python 3.8+
- PyGObject (GTK3)
- `python3-evdev` (Wayland 下模拟按键所需)
- `wl-clipboard` (支持 Wayland)
- `xdotool` / `xclip` (支持 X11)

## 📄 开源协议
本项目采用 MIT 协议开源 - 详情请参阅 [LICENSE](LICENSE) 文件。
