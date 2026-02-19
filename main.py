#!/usr/bin/env python3
"""
main.py — WinVX 入口
Linux 上的 Windows 11 Win+V 剪贴板管理器

用法:
    python3 main.py              # 启动守护进程
    python3 main.py --toggle     # 切换弹窗 (发信号给已运行的实例)
    python3 main.py --max 50     # 设置最大记录数
    python3 main.py --bind       # 自动注册 Super+V 到系统快捷键
"""

import os

# Wayland: 强制 GTK 使用 XWayland 后端, 使 window.move() 可用
# (GNOME Wayland 完全忽略客户端窗口定位请求)
# wl-copy/wl-paste/evdev 是子进程, 不受 GDK 后端影响
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ.setdefault("GDK_BACKEND", "x11")

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import os
import sys
import signal
import socket
import argparse
import subprocess
import threading
import ctypes
import ctypes.util
from pathlib import Path

from clip_store import ClipStore
from clipboard_monitor import ClipboardMonitor
from clipboard_ui import ClipboardPopup
from session_helper import is_wayland, is_x11, get_session_type, has_ydotool


# ── 单实例控制 ────────────────────────────────────────────────

SOCKET_PATH = "/tmp/winvx.sock"


def is_running() -> bool:
    """检查是否已有实例在运行"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def send_toggle():
    """向已运行实例发送 toggle 信号"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.sendall(b"toggle")
        sock.close()
        return True
    except Exception:
        return False


# ── X11 全局快捷键 (纯 ctypes) ────────────────────────────────

class X11HotkeyListener:
    """使用 ctypes 直接调用 X11 API 注册全局快捷键
    
    注意: 如果桌面环境 (GNOME/KDE) 已经抢占了 Super 键,
    XGrabKey 可能无法拦截到 Super+V。此时需要通过
    桌面环境自己的快捷键设置来绑定 --toggle 命令。
    """

    def __init__(self, callback):
        self.callback = callback
        self._running = False
        self._thread = None

        # 加载 X11 库
        x11_path = ctypes.util.find_library("X11")
        if not x11_path:
            raise RuntimeError("找不到 libX11")
        self.xlib = ctypes.cdll.LoadLibrary(x11_path)

        # 设置返回类型
        self.xlib.XOpenDisplay.restype = ctypes.c_void_p
        self.xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.xlib.XKeysymToKeycode.restype = ctypes.c_int
        self.xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.xlib.XGrabKey.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
            ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        self.xlib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.xlib.XFlush.argtypes = [ctypes.c_void_p]
        self.xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

        # 打开独立的 Display 连接 (线程安全)
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("无法打开 X Display")

        self.root = self.xlib.XDefaultRootWindow(self.display)

    def start(self):
        """开始监听全局快捷键 (在后台线程)"""
        # 获取 'v' 的 keycode
        keycode = self.xlib.XKeysymToKeycode(self.display, 0x0076)  # XK_v = 0x76
        if not keycode:
            print("[WinVX] ✗ 无法获取 'v' 的 keycode")
            return False

        # Mod4Mask = Super 键 (通常是 1<<6 = 64)
        Mod4Mask = (1 << 6)
        LockMask = (1 << 1)    # CapsLock
        Mod2Mask = (1 << 4)    # NumLock

        # 注册 XGrabKey (需要处理 CapsLock/NumLock 组合)
        modifiers_combos = [
            Mod4Mask,
            Mod4Mask | LockMask,
            Mod4Mask | Mod2Mask,
            Mod4Mask | LockMask | Mod2Mask,
        ]

        grabbed = False
        for mod in modifiers_combos:
            result = self.xlib.XGrabKey(
                self.display,
                keycode,
                mod,
                self.root,
                True,   # owner_events
                1,      # GrabModeAsync
                1,      # GrabModeAsync
            )
            if result == 0:  # BadAccess 等错误
                pass
            else:
                grabbed = True

        self.xlib.XFlush(self.display)

        if not grabbed:
            return False

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        return True

    def _listen_loop(self):
        """X11 事件循环 (在后台线程运行)"""
        # XEvent 结构足够大以容纳所有事件类型
        event_buf = ctypes.create_string_buffer(256)

        while self._running:
            try:
                self.xlib.XNextEvent(self.display, event_buf)
                # event.type 是结构体第一个字段 (int)
                event_type = ctypes.c_int.from_buffer_copy(event_buf).value
                if event_type == 2:  # KeyPress
                    GLib.idle_add(self.callback)
            except Exception:
                break

    def stop(self):
        self._running = False


class WinVXApp:
    """WinVX 应用主类"""

    def __init__(self, max_items: int = 25):
        self.store = ClipStore(max_items=max_items)
        self._session_type = get_session_type()

        # 先创建 UI, 再创建 Monitor (避免回调时 popup 还不存在)
        self.popup = ClipboardPopup(self.store, on_paste=self._on_paste,
                                    wayland=is_wayland())
        self.monitor = ClipboardMonitor(self.store, on_change=self._on_clip_change,
                                        wayland=is_wayland())

        self._hotkey_listener = None
        self._setup_socket_server()
        self._setup_hotkey()

    # ── 全局快捷键 ────────────────────────────────────────────

    def _setup_hotkey(self):
        """绑定 Super+V 全局快捷键"""
        if is_wayland():
            # Wayland: 无法 XGrabKey, 尝试自动注册 gsettings 快捷键
            print("[WinVX] 🌊 Wayland 模式 — 使用系统快捷键绑定")
            self._setup_hotkey_wayland()
        else:
            # X11: 原有 XGrabKey 逻辑
            self._setup_hotkey_x11()

    def _setup_hotkey_x11(self):
        """X11: 通过 XGrabKey 绑定全局快捷键"""
        try:
            self._hotkey_listener = X11HotkeyListener(self._on_hotkey)
            if self._hotkey_listener.start():
                print("[WinVX] ✓ 全局快捷键 Super+V 已绑定 (X11 XGrabKey)")
            else:
                print("[WinVX] ⚠ XGrabKey 绑定失败 (可能被桌面环境占用)")
                self._print_manual_setup()
        except Exception as e:
            print(f"[WinVX] ⚠ 快捷键绑定失败: {e}")
            self._print_manual_setup()

    def _setup_hotkey_wayland(self):
        """Wayland: 尝试自动注册 GNOME 自定义快捷键"""
        try:
            if auto_bind_shortcut():
                print("[WinVX] ✓ 已自动注册 Super+V 快捷键")
            else:
                self._print_manual_setup()
        except Exception as e:
            print(f"[WinVX] ⚠ 自动绑定快捷键失败: {e}")
            self._print_manual_setup()

    def _on_hotkey(self):
        """快捷键回调 (在主线程)"""
        self.popup.toggle()
        return False  # GLib.idle_add 不重复

    def _print_manual_setup(self):
        me = os.path.abspath(__file__)
        print("[WinVX]")
        print("[WinVX] 请通过以下方式之一设置快捷键:")
        print("[WinVX]")
        print(f"[WinVX]   方法1: python3 {me} --bind")
        print(f"[WinVX]          (自动注册到 GNOME/KDE 快捷键)")
        print("[WinVX]")
        print(f"[WinVX]   方法2: 手动在系统设置 → 键盘 → 自定义快捷键:")
        print(f"[WinVX]          命令: python3 {me} --toggle")
        print(f"[WinVX]          快捷键: Super+V")

    # ── Socket 服务 (单实例通信) ───────────────────────────────

    def _setup_socket_server(self):
        """启动 Unix Socket 服务, 接收 toggle 命令"""
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(SOCKET_PATH)
        self._server_sock.listen(1)
        self._server_sock.setblocking(False)

        GLib.io_add_watch(
            self._server_sock.fileno(),
            GLib.IO_IN,
            self._on_socket_ready
        )

    def _on_socket_ready(self, fd, condition):
        """收到 socket 连接"""
        try:
            conn, _ = self._server_sock.accept()
            data = conn.recv(1024).decode("utf-8", errors="ignore")
            conn.close()
            if data == "toggle":
                GLib.idle_add(self.popup.toggle)
        except Exception:
            pass
        return True

    # ── 回调 ──────────────────────────────────────────────────

    def _on_clip_change(self, entry):
        """新剪贴板内容回调"""
        GLib.idle_add(self.popup.refresh)

    def _on_paste(self, entry):
        """用户点击粘贴 — 将内容设置到剪贴板"""
        self._pending_paste_entry = entry  # 保存条目, 供 _simulate_paste 使用
        self.monitor.paste_entry(entry)     # 设置剪贴板 (备用)
        # hide() 在 _on_item_click 中调用, 焦点回到目标窗口后模拟粘贴
        # Wayland 下需要更长延迟, 等待窗口管理器将焦点转回目标应用
        delay = 200 if is_wayland() else 30
        GLib.timeout_add(delay, self._simulate_paste)

    def _simulate_paste(self):
        """模拟粘贴"""
        if is_wayland():
            return self._simulate_paste_wayland()
        else:
            return self._simulate_paste_x11()

    def _simulate_paste_wayland(self):
        """Wayland: 使用 python-evdev 通过 uinput 模拟 Ctrl+V"""
        # 方式 1: python-evdev (直接 uinput, 最可靠)
        try:
            from evdev import UInput, ecodes
            import time as _time

            # 缓存 UInput 设备, 避免每次创建/销毁
            if not hasattr(self, '_uinput'):
                self._uinput = UInput(
                    {ecodes.EV_KEY: [ecodes.KEY_LEFTCTRL, ecodes.KEY_V]},
                    name='winvx-paste'
                )
                _time.sleep(0.05)  # 等待内核注册设备

            ui = self._uinput
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 1)
            ui.syn()
            _time.sleep(0.01)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 0)
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
            ui.syn()
            return False  # 成功
        except ImportError:
            pass  # evdev 未安装
        except PermissionError:
            print("[WinVX] ⚠ /dev/uinput 权限不足")
            print("[WinVX]   请运行: sudo usermod -aG input $USER")
        except Exception as e:
            print(f"[WinVX] evdev 异常: {e}")

        # 方式 2: xdotool (通过 XWayland, 仅对 X11 应用有效)
        try:
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "--delay", "0", "ctrl+v"],
                capture_output=True, timeout=3
            )
        except Exception:
            pass

        if not getattr(self, '_paste_warned', False):
            self._paste_warned = True
            print("[WinVX] ⚠ 自动粘贴可能不可用")
            print("[WinVX]   内容已复制到剪贴板, 请手动 Ctrl+V")
        return False

    def _simulate_paste_x11(self):
        """X11: 使用 XTest 直接发送 Ctrl+V 按键事件 (零延迟, 无进程开销)"""
        try:
            if not hasattr(self, '_xtst'):
                self._init_xtest()

            d = self._xtest_display
            # Ctrl 按下 → v 按下 → v 释放 → Ctrl 释放
            self._xtst.XTestFakeKeyEvent(d, self._ctrl_keycode, True, 0)
            self._xtst.XTestFakeKeyEvent(d, self._v_keycode, True, 0)
            self._xtst.XTestFakeKeyEvent(d, self._v_keycode, False, 0)
            self._xtst.XTestFakeKeyEvent(d, self._ctrl_keycode, False, 0)
            self._xlib_paste.XFlush(d)
        except Exception as e:
            # fallback: xdotool
            print(f"[WinVX] XTest 失败, 回退 xdotool: {e}")
            try:
                subprocess.Popen(
                    ["xdotool", "key", "--delay", "0", "ctrl+v"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
        return False

    def _init_xtest(self):
        """初始化 XTest 扩展 (只调用一次)"""
        import ctypes, ctypes.util

        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")

        self._xlib_paste = ctypes.cdll.LoadLibrary(x11_path)
        self._xtst = ctypes.cdll.LoadLibrary(xtst_path)

        # 设置函数签名
        self._xlib_paste.XOpenDisplay.restype = ctypes.c_void_p
        self._xlib_paste.XKeysymToKeycode.restype = ctypes.c_int
        self._xlib_paste.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._xlib_paste.XFlush.argtypes = [ctypes.c_void_p]

        self._xtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_uint,    # keycode
            ctypes.c_int,     # is_press (True/False)
            ctypes.c_ulong,   # delay
        ]
        self._xtst.XTestFakeKeyEvent.restype = ctypes.c_int

        self._xtest_display = self._xlib_paste.XOpenDisplay(None)
        self._ctrl_keycode = self._xlib_paste.XKeysymToKeycode(
            self._xtest_display, 0xffe3)  # XK_Control_L
        self._v_keycode = self._xlib_paste.XKeysymToKeycode(
            self._xtest_display, 0x0076)  # XK_v

    # ── 运行 ──────────────────────────────────────────────────

    def run(self):
        session = get_session_type()
        print(f"[WinVX] 🚀 剪贴板管理器已启动 ({session} 会话)")
        print("[WinVX] 按 Super+V 打开剪贴板历史")
        print(f"[WinVX] 或运行: python3 {os.path.abspath(__file__)} --toggle")
        if is_wayland():
            if not has_ydotool():
                print("[WinVX] ⚠ ydotool 未安装，粘贴功能将不可用")
                print("[WinVX]   请安装: sudo apt install ydotool")

        signal.signal(signal.SIGINT, lambda *a: self.quit())
        signal.signal(signal.SIGTERM, lambda *a: self.quit())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT,
                             lambda: self.quit() or True)

        try:
            Gtk.main()
        except KeyboardInterrupt:
            self.quit()

    def quit(self):
        print("\n[WinVX] 正在退出...")
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        if hasattr(self, 'monitor'):
            self.monitor.stop()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        Gtk.main_quit()


# ── 自动绑定快捷键到桌面环境 ──────────────────────────────────

def auto_bind_shortcut():
    """尝试自动注册 Super+V 快捷键到 GNOME/KDE"""
    me = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
    toggle_cmd = f"python3 {me} --toggle"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if "gnome" in desktop or "ubuntu" in desktop or "unity" in desktop:
        # GNOME: 使用 gsettings 自定义快捷键
        try:
            # 读取已有的自定义快捷键
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                 "custom-keybindings"],
                capture_output=True, text=True
            )
            existing = result.stdout.strip()

            path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winvx/"

            # 检查是否已注册
            if "winvx" in existing:
                print("[WinVX] 快捷键已注册, 更新中...")
            else:
                # 添加到列表
                if existing == "@as []" or existing == "[]":
                    new_list = f"['{path}']"
                else:
                    new_list = existing.rstrip("]") + f", '{path}']"
                subprocess.run([
                    "gsettings", "set",
                    "org.gnome.settings-daemon.plugins.media-keys",
                    "custom-keybindings", new_list
                ], check=True)

            # 设置快捷键属性
            base = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
            schema_path = path
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "name", "WinVX Clipboard"], check=True)
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "command", toggle_cmd], check=True)
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "binding", "<Super>v"], check=True)

            print("[WinVX] ✓ 已注册 GNOME 快捷键: Super+V")
            print(f"[WinVX]   命令: {toggle_cmd}")
            return True
        except Exception as e:
            print(f"[WinVX] ✗ GNOME 快捷键注册失败: {e}")
            return False

    elif "kde" in desktop or "plasma" in desktop:
        # KDE: 使用 kglobalaccel 或 kwriteconfig
        try:
            rc_path = os.path.expanduser("~/.config/kglobalshortcutsrc")
            # 写入 khotkeys 配置
            subprocess.run([
                "kwriteconfig5", "--file", "kglobalshortcutsrc",
                "--group", "winvx.desktop",
                "--key", "_launch", f"{toggle_cmd},none,WinVX Clipboard"
            ], check=True)
            print("[WinVX] ✓ 已写入 KDE 配置, 请手动设置快捷键")
            print(f"[WinVX]   系统设置 → 快捷键 → 自定义 → WinVX Clipboard → Super+V")
            return True
        except Exception as e:
            print(f"[WinVX] ✗ KDE 配置失败: {e}")
            return False

    else:
        # XFCE, Cinnamon 等: 提示手动设置
        print(f"[WinVX] 未检测到 GNOME/KDE (当前: {desktop})")
        print(f"[WinVX] 请手动在系统设置中添加自定义快捷键:")
        print(f"[WinVX]   命令: {toggle_cmd}")
        print(f"[WinVX]   快捷键: Super+V")
        return False


# ── CLI 入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WinVX — Linux 剪贴板管理器")
    parser.add_argument("--toggle", action="store_true",
                        help="切换弹窗显示 (发信号给已运行的实例)")
    parser.add_argument("--bind", action="store_true",
                        help="自动注册 Super+V 到系统快捷键")
    parser.add_argument("--max", type=int, default=25,
                        help="最大历史记录数 (默认 25)")
    args = parser.parse_args()

    # --bind: 注册系统快捷键
    if args.bind:
        auto_bind_shortcut()
        sys.exit(0)

    # --toggle: 发送信号给已运行的实例
    if args.toggle:
        if send_toggle():
            sys.exit(0)
        else:
            print("[WinVX] 没有运行中的实例, 正在启动...")

    # 检查单实例
    if is_running():
        print("[WinVX] 已有实例在运行, 发送 toggle 信号")
        send_toggle()
        sys.exit(0)

    app = WinVXApp(max_items=args.max)
    app.run()


if __name__ == "__main__":
    main()
