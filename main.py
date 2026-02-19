#!/usr/bin/env python3
"""
main.py — WinVX Entry Point
Windows 11-style Win+V Clipboard Manager for Linux

Usage:
    python3 main.py              # Start daemon
    python3 main.py --toggle     # Toggle popup (signal running instance)
    python3 main.py --max 50     # Set max record count
    python3 main.py --bind       # Auto-register Super+V to system hotkeys
"""

import os

# Wayland: Force GTK to use XWayland backend to make window.move() work
# (GNOME Wayland completely ignores client-side window positioning requests)
# wl-copy/wl-paste/evdev are subprocesses, unaffected by GDK backend
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


# ── Single Instance Control ───────────────────────────────────

SOCKET_PATH = "/tmp/winvx.sock"


def is_running() -> bool:
    """Check if an instance is already running"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def send_toggle():
    """Send toggle signal to a running instance"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.sendall(b"toggle")
        sock.close()
        return True
    except Exception:
        return False


# ── X11 Global Hotkey (Pure ctypes) ───────────────────────────

class X11HotkeyListener:
    """Use ctypes to directly call X11 API to register global hotkeys
    
    Note: If the desktop environment (GNOME/KDE) has already claimed the Super key,
    XGrabKey may fail to intercept Super+V. In this case, you need to use
    the desktop environment's own hotkey settings to bind the --toggle command.
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
        """Start listening for global hotkeys (in background thread)"""
        # Get keycode for 'v'
        keycode = self.xlib.XKeysymToKeycode(self.display, 0x0076)  # XK_v = 0x76
        if not keycode:
            print("[WinVX] ✗ Failed to get keycode for 'v'")
            return False

        # Mod4Mask = Super key (usually 1<<6 = 64)
        Mod4Mask = (1 << 6)
        LockMask = (1 << 1)    # CapsLock
        Mod2Mask = (1 << 4)    # NumLock

        # Register XGrabKey (need to handle CapsLock/NumLock combinations)
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
            if result == 0:  # BadAccess and other errors
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
        """X11 event loop (runs in background thread)"""
        # XEvent structure large enough to hold all event types
        event_buf = ctypes.create_string_buffer(256)

        while self._running:
            try:
                self.xlib.XNextEvent(self.display, event_buf)
                # event.type is the first field of the structure (int)
                event_type = ctypes.c_int.from_buffer_copy(event_buf).value
                if event_type == 2:  # KeyPress
                    GLib.idle_add(self.callback)
            except Exception:
                break

    def stop(self):
        self._running = False


class WinVXApp:
    """WinVX Main Application Class"""

    def __init__(self, max_items: int = 25):
        self.store = ClipStore(max_items=max_items)
        self._session_type = get_session_type()

        # Create UI first, then Monitor (to ensure popup exists before callbacks)
        self.popup = ClipboardPopup(self.store, on_paste=self._on_paste,
                                    wayland=is_wayland())
        self.monitor = ClipboardMonitor(self.store, on_change=self._on_clip_change,
                                        wayland=is_wayland())

        self._hotkey_listener = None
        self._setup_socket_server()
        self._setup_hotkey()

    # ── Global Hotkeys ────────────────────────────────────────────

    def _setup_hotkey(self):
        """Bind Super+V global hotkey"""
        if is_wayland():
            # Wayland: Cannot XGrabKey, try auto-registering gsettings hotkey
            print("[WinVX] 🌊 Wayland Mode — Using system hotkey binding")
            self._setup_hotkey_wayland()
        else:
            # X11: Existing XGrabKey logic
            self._setup_hotkey_x11()

    def _setup_hotkey_x11(self):
        """X11: Bind global hotkey via XGrabKey"""
        try:
            self._hotkey_listener = X11HotkeyListener(self._on_hotkey)
            if self._hotkey_listener.start():
                print("[WinVX] ✓ Global Hotkey Super+V Bound (X11 XGrabKey)")
            else:
                print("[WinVX] ⚠ XGrabKey Binding Failed (likely claimed by desktop environment)")
                self._print_manual_setup()
        except Exception as e:
            print(f"[WinVX] ⚠ Hotkey Binding Failed: {e}")
            self._print_manual_setup()

    def _setup_hotkey_wayland(self):
        """Wayland: Try auto-registering GNOME custom hotkey"""
        try:
            if auto_bind_shortcut():
                print("[WinVX] ✓ Auto-registered Super+V hotkey")
            else:
                self._print_manual_setup()
        except Exception as e:
            print(f"[WinVX] ⚠ Auto-binding hotkey failed: {e}")
            self._print_manual_setup()

    def _on_hotkey(self):
        """Hotkey callback (in main thread)"""
        self.popup.toggle()
        return False  # GLib.idle_add does not repeat

    def _print_manual_setup(self):
        me = os.path.abspath(__file__)
        print("[WinVX]")
        print("[WinVX] Please set hotkey using one of these methods:")
        print("[WinVX]")
        print(f"[WinVX]   Method 1: python3 {me} --bind")
        print(f"[WinVX]             (Auto-register to GNOME/KDE hotkeys)")
        print("[WinVX]")
        print(f"[WinVX]   Method 2: Manual setup in System Settings → Keyboard → Shortcuts:")
        print(f"[WinVX]             Command: python3 {me} --toggle")
        print(f"[WinVX]             Hotkey: Super+V")

    # ── Socket Server (Single Instance Communication) ──────────

    def _setup_socket_server(self):
        """Start Unix Socket service to receive toggle commands"""
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
        """Received socket connection"""
        try:
            conn, _ = self._server_sock.accept()
            data = conn.recv(1024).decode("utf-8", errors="ignore")
            conn.close()
            if data == "toggle":
                GLib.idle_add(self.popup.toggle)
        except Exception:
            pass
        return True

    # ── Callbacks ─────────────────────────────────────────────────

    def _on_clip_change(self, entry):
        """New clipboard content callback"""
        GLib.idle_add(self.popup.refresh)

    def _on_paste(self, entry):
        """User clicked paste — Set content to clipboard"""
        self._pending_paste_entry = entry  # Save entry for _simulate_paste
        self.monitor.paste_entry(entry)     # Set clipboard (fallback)
        # hide() is called in _on_item_click, simulate paste after focus returns to target window
        # Wayland needs longer delay for WM to switch focus back to target app
        delay = 200 if is_wayland() else 30
        GLib.timeout_add(delay, self._simulate_paste)

    def _simulate_paste(self):
        """Simulate paste"""
        if is_wayland():
            return self._simulate_paste_wayland()
        else:
            return self._simulate_paste_x11()

    def _simulate_paste_wayland(self):
        """Wayland: Simulate Ctrl+V using python-evdev via uinput"""
        # Method 1: python-evdev (direct uinput, most reliable)
        try:
            from evdev import UInput, ecodes
            import time as _time

            # Cache UInput device to avoid repeated creation/destruction
            if not hasattr(self, '_uinput'):
                self._uinput = UInput(
                    {ecodes.EV_KEY: [ecodes.KEY_LEFTCTRL, ecodes.KEY_V]},
                    name='winvx-paste'
                )
                _time.sleep(0.05)  # Wait for kernel to register device

            ui = self._uinput
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 1)
            ui.syn()
            _time.sleep(0.01)
            ui.write(ecodes.EV_KEY, ecodes.KEY_V, 0)
            ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
            ui.syn()
            return False  # Success
        except ImportError:
            pass  # evdev not installed
        except PermissionError:
            print("[WinVX] ⚠ Insufficient permission for /dev/uinput")
            print("[WinVX]   Please run: sudo usermod -aG input $USER")
        except Exception as e:
            print(f"[WinVX] evdev exception: {e}")

        # Method 2: xdotool (via XWayland, only works for X11 apps)
        try:
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "--delay", "0", "ctrl+v"],
                capture_output=True, timeout=3
            )
        except Exception:
            pass

        if not getattr(self, '_paste_warned', False):
            self._paste_warned = True
            print("[WinVX] ⚠ Auto-paste might be unavailable")
            print("[WinVX]   Content copied to clipboard, please Ctrl+V manually")
        return False

    def _simulate_paste_x11(self):
        """X11: Use XTest to send Ctrl+V key events directly (zero delay, no proc overhead)"""
        try:
            if not hasattr(self, '_xtst'):
                self._init_xtest()

            d = self._xtest_display
            # Ctrl press → v press → v release → Ctrl release
            self._xtst.XTestFakeKeyEvent(d, self._ctrl_keycode, True, 0)
            self._xtst.XTestFakeKeyEvent(d, self._v_keycode, True, 0)
            self._xtst.XTestFakeKeyEvent(d, self._v_keycode, False, 0)
            self._xtst.XTestFakeKeyEvent(d, self._ctrl_keycode, False, 0)
            self._xlib_paste.XFlush(d)
        except Exception as e:
            # fallback: xdotool
            print(f"[WinVX] XTest failed, falling back to xdotool: {e}")
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
        """Initialize XTest extension (called only once)"""
        import ctypes, ctypes.util

        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")

        self._xlib_paste = ctypes.cdll.LoadLibrary(x11_path)
        self._xtst = ctypes.cdll.LoadLibrary(xtst_path)

        # Set function signatures
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

    # ── Run ───────────────────────────────────────────────────

    def run(self):
        session = get_session_type()
        print(f"[WinVX] 🚀 Clipboard Manager Started ({session} session)")
        print("[WinVX] Press Super+V to open clipboard history")
        print(f"[WinVX] Or run: python3 {os.path.abspath(__file__)} --toggle")
        if is_wayland():
            if not has_ydotool():
                print("[WinVX] ⚠ ydotool not installed, paste function will be unavailable")
                print("[WinVX]   Please install: sudo apt install ydotool")

        signal.signal(signal.SIGINT, lambda *a: self.quit())
        signal.signal(signal.SIGTERM, lambda *a: self.quit())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT,
                             lambda: self.quit() or True)

        try:
            Gtk.main()
        except KeyboardInterrupt:
            self.quit()

    def quit(self):
        print("\n[WinVX] Exiting...")
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        if hasattr(self, 'monitor'):
            self.monitor.stop()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        Gtk.main_quit()


# ── Auto-bind Hotkeys to Desktop Environment ─────────────────

def auto_bind_shortcut():
    """Try auto-registering Super+V hotkey to GNOME/KDE"""
    me = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
    toggle_cmd = f"python3 {me} --toggle"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if "gnome" in desktop or "ubuntu" in desktop or "unity" in desktop:
        # GNOME: Use gsettings for custom hotkey
        try:
            # Read existing custom keybindings
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                 "custom-keybindings"],
                capture_output=True, text=True
            )
            existing = result.stdout.strip()

            path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/winvx/"

            # Check if already registered
            if "winvx" in existing:
                print("[WinVX] Hotkey already registered, updating...")
            else:
                # Add to list
                if existing == "@as []" or existing == "[]":
                    new_list = f"['{path}']"
                else:
                    new_list = existing.rstrip("]") + f", '{path}']"
                subprocess.run([
                    "gsettings", "set",
                    "org.gnome.settings-daemon.plugins.media-keys",
                    "custom-keybindings", new_list
                ], check=True)

            # Set hotkey properties
            base = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
            schema_path = path
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "name", "WinVX Clipboard"], check=True)
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "command", toggle_cmd], check=True)
            subprocess.run(["gsettings", "set", f"{base}:{schema_path}", "binding", "<Super>v"], check=True)

            print("[WinVX] ✓ Registered GNOME Hotkey: Super+V")
            print(f"[WinVX]   Command: {toggle_cmd}")
            return True
        except Exception as e:
            print(f"[WinVX] ✗ GNOME Hotkey registration failed: {e}")
            return False

    elif "kde" in desktop or "plasma" in desktop:
        # KDE: Use kglobalaccel or kwriteconfig
        try:
            rc_path = os.path.expanduser("~/.config/kglobalshortcutsrc")
            # Write khotkeys config
            subprocess.run([
                "kwriteconfig5", "--file", "kglobalshortcutsrc",
                "--group", "winvx.desktop",
                "--key", "_launch", f"{toggle_cmd},none,WinVX Clipboard"
            ], check=True)
            print("[WinVX] ✓ KDE config written, please set hotkey manually")
            print(f"[WinVX]   System Settings → Shortcuts → Custom → WinVX Clipboard → Super+V")
            return True
        except Exception as e:
            print(f"[WinVX] ✗ KDE configuration failed: {e}")
            return False

    else:
        # XFCE, Cinnamon, etc.: Prompt for manual setup
        print(f"[WinVX] GNOME/KDE not detected (Current: {desktop})")
        print(f"[WinVX] Please manually add a custom hotkey in system settings:")
        print(f"[WinVX]   Command: {toggle_cmd}")
        print(f"[WinVX]   Hotkey: Super+V")
        return False


# ── CLI Entry Point ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WinVX — Linux Clipboard Manager")
    parser.add_argument("--toggle", action="store_true",
                        help="Toggle popup display (signal running instance)")
    parser.add_argument("--bind", action="store_true",
                        help="Auto-register Super+V to system hotkeys")
    parser.add_argument("--max", type=int, default=25,
                        help="Max history items (default 25)")
    args = parser.parse_args()

    # --bind: Register system hotkey
    if args.bind:
        auto_bind_shortcut()
        sys.exit(0)

    # --toggle: Send signal to running instance
    if args.toggle:
        if send_toggle():
            sys.exit(0)
        else:
            print("[WinVX] No instance running, starting...")

    # Check for single instance
    if is_running():
        print("[WinVX] Already running, sending toggle signal")
        send_toggle()
        sys.exit(0)

    app = WinVXApp(max_items=args.max)
    app.run()


if __name__ == "__main__":
    main()
