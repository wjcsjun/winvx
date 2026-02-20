# WinVX

[English](README.md) | [中文](README_zh.md)

**WinVX** is a Linux-native clipboard history manager that replicates the **Windows 11 (Win+V)** experience. It features a sleek dark-themed UI, supports both text and images, and works seamlessly on both **X11** and **Wayland**.

![WinVX Preview](https://github.com/wjcsjun/winvx/raw/main/preview.png) *(Placeholder for preview)*

## ✨ Features

- **Win+V Experience**: Familiar floating popup for quick clipboard access.
- **Text & Images**: Automatically records your clipboard history including text snippets and images.
- **Modern UI**: Win11-style dark theme with smooth animations and rounded corners.
- **Search & Filter**: Instantly find previous clips using the built-in search bar.
- **Pinning**: Keep important items at the top of your history.
- **Wayland & X11**: Full support for both display servers, including reliable paste simulation under Wayland.
- **Auto Hotkey**: Intelligent setup tool detects your desktop environment (GNOME, KDE, XFCE, etc.) and binds the hotkey automatically.

## 🚀 Installation

### 1. Download .deb Package (Recommended)
The easiest way to install WinVX on Debian-based systems (Ubuntu, Linux Mint, etc.) is using the `.deb` package. You can find it in the [GitHub Releases](https://github.com/wjcsjun/winvx/releases) or build it yourself:

```bash
# Build the .deb package
bash build_deb.sh

# Install it
sudo dpkg -i winvx_1.0.0_all.deb
sudo apt-get install -f
```

### 2. Manual Installation
If you prefer to run from source:

```bash
# Clone the repository
git clone https://github.com/wjcsjun/winvx.git
cd winvx

# Install dependencies (Ubuntu/Debian)
sudo apt install python3-gi gir1.2-gtk-3.0 python3-evdev xdotool wl-clipboard xclip

# Run the installer script
bash install.sh
```

## ⌨️ Usage

- **Open/Toggle**: Press `Super+V` (Win+V).
- **Paste**: Click an item or press `Enter` on a selected item.
- **Navigation**: Use `↑` and `↓` keys to navigate; `Esc` to close.
- **Pin/Delete**: Hover over items to see action buttons, or use the context icons.

### Command Line Interface
```bash
winvx          # Start background monitor
winvx --toggle # Open/Close the popup
winvx-setup    # Reconfigure hotkeys
```

## 🛠️ Requirements
- Python 3.8+
- PyGObject (GTK3)
- `python3-evdev` (for Wayland paste simulation)
- `wl-clipboard` (Wayland support)
- `xdotool` / `xclip` (X11 support)

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
