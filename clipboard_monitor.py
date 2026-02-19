"""
clipboard_monitor.py — Clipboard Monitoring Module
Capture clipboard changes in real-time via GTK Clipboard's owner-change signal
In Wayland mode, additionally use wl-paste --watch background process for background capture
"""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

import io
import os
import subprocess
import traceback
from typing import Callable, Optional
from clip_store import ClipStore, ClipEntry


class ClipboardMonitor:
    """Monitor system clipboard changes and write to ClipStore"""

    def __init__(self, store: ClipStore, on_change: Optional[Callable] = None,
                 wayland: bool = False):
        self.store = store
        self.on_change = on_change  # Callback: triggered when a new entry is added
        self._last_text = None
        self._last_image_hash = None
        self._ignore_next = False   # Used to ignore owner-change triggered by ourselves during paste
        self._wayland = wayland
        self._wl_watch_proc = None
        self._skip_change_until = 0  # Timestamp: skip wl-paste change detection before this time

        # Get system clipboard
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        if self._wayland:
            # Wayland: Only use wl-paste polling, don't connect GTK owner-change
            # (Enabling both simultaneously causes double triggers and popup flickering)
            self._start_wl_paste_watch()
        else:
            # X11: Use GTK owner-change signal
            self.clipboard.connect("owner-change", self._on_owner_change)

        # Initialization: Read current content
        self._read_current()

    def stop(self):
        """Stop monitoring (called on exit)"""
        self._wl_running = False
        if self._wl_watch_proc:
            try:
                self._wl_watch_proc.terminate()
                self._wl_watch_proc.wait(timeout=2)
            except Exception:
                try:
                    self._wl_watch_proc.kill()
                except Exception:
                    pass
            self._wl_watch_proc = None

    def set_ignore_next(self):
        """Called before paste operation to ignore the next owner-change"""
        self._ignore_next = True

    def paste_entry(self, entry: ClipEntry):
        """Set specified entry content to clipboard (prepare for paste)"""
        self._ignore_next = True
        # Wayland: skip wl-paste change detection for the next 2 seconds
        # (Avoid list refresh triggered by our own set content)
        import time as _time
        self._skip_change_until = _time.time() + 2.0
        # Remember the content we are pasting to prevent duplicate processing if detected by wl-paste
        if entry.content_type in ("text", "html"):
            self._last_text = entry.content

        if self._wayland:
            # Wayland: use only wl-copy (do not set GTK clipboard at same time, causes conflict and empty content)
            self._paste_entry_wayland(entry)
        else:
            # X11: Use GTK clipboard
            self._paste_entry_gtk(entry)

    def _paste_entry_gtk(self, entry: ClipEntry):
        """Set clipboard via GTK"""
        if entry.content_type == "text" or entry.content_type == "html":
            self.clipboard.set_text(entry.content, -1)
        elif entry.content_type == "image":
            img_path = self.store.get_image_path(entry)
            if img_path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(img_path))
                    self.clipboard.set_image(pixbuf)
                except Exception as e:
                    print(f"[WinVX] Failed to paste image: {e}")

    def _paste_entry_wayland(self, entry: ClipEntry):
        """Wayland: use wl-copy to set clipboard (more reliable)"""
        try:
            if entry.content_type == "text" or entry.content_type == "html":
                content = entry.content
                print(f"[WinVX] DEBUG: Preparing to write to clipboard: '{content[:50]}...' (len={len(content)})")
                # Pass content via stdin pipe (wl-copy stays running to serve clipboard, cannot wait)
                proc = subprocess.Popen(
                    ["wl-copy"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                proc.stdin.write(content.encode("utf-8"))
                proc.stdin.close()
                # Do not wait for wl-copy to exit; it runs in background until another app takes over
                # Verification: read back clipboard content
                try:
                    verify = subprocess.run(
                        ["wl-paste", "--no-newline"],
                        capture_output=True, timeout=1
                    )
                    actual = verify.stdout.decode("utf-8", errors="replace")[:50]
                    print(f"[WinVX] DEBUG: Verify clipboard: '{actual}'")
                except Exception:
                    pass
            elif entry.content_type == "image":
                img_path = self.store.get_image_path(entry)
                if img_path:
                    with open(img_path, "rb") as f:
                        proc = subprocess.Popen(
                            ["wl-copy", "--type", "image/png"],
                            stdin=f,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE
                        )
                        proc.wait(timeout=2)
        except FileNotFoundError:
            print("[WinVX] ✗ wl-copy not installed, falling back to GTK")
            self._paste_entry_gtk(entry)
        except Exception as e:
            print(f"[WinVX] wl-copy failed, falling back to GTK: {e}")
            self._paste_entry_gtk(entry)

    # ── wl-paste Background Monitoring (Wayland) ──────────────────

    def _start_wl_paste_watch(self):
        """Start wl-paste --watch event-driven monitoring (triggered only on change)"""
        try:
            # wl-paste --watch: execute specified command on every clipboard change
            # Use "cat" as the command; it will output clipboard content to stdout
            # Each change produces one output, no separator between outputs
            # So we read block by block and process in batches
            self._wl_watch_proc = subprocess.Popen(
                ["wl-paste", "--no-newline", "--watch", "cat"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            import threading
            self._wl_running = True
            self._wl_thread = threading.Thread(
                target=self._wl_paste_watch_loop, daemon=True
            )
            self._wl_thread.start()
            print("[WinVX] ✓ wl-paste --watch event monitoring started")
        except FileNotFoundError:
            print("[WinVX] ⚠ wl-paste not installed, background monitoring unavailable")
            print("[WinVX]   Please install: sudo apt install wl-clipboard")
        except Exception as e:
            print(f"[WinVX] ⚠ wl-paste monitoring failed to start: {e}")

    def _wl_paste_watch_loop(self):
        """Background thread: read wl-paste --watch output, triggered only on change"""
        import time as _time
        import select
        proc = self._wl_watch_proc
        if not proc or not proc.stdout:
            return

        fd = proc.stdout.fileno()

        while getattr(self, '_wl_running', False):
            try:
                # Wait for data to be readable (non-blocking, low CPU usage)
                readable, _, _ = select.select([fd], [], [], 1.0)
                if not readable:
                    continue

                # New data — clipboard has changed
                # Short sleep to collect full content (wl-paste may write in multiple chunks)
                _time.sleep(0.05)

                # Read all available data
                data = b""
                while True:
                    r, _, _ = select.select([fd], [], [], 0.01)
                    if not r:
                        break
                    chunk = proc.stdout.read1(4096)  # type: ignore
                    if not chunk:
                        break
                    data += chunk

                if not data:
                    # Process may have exited
                    if proc.poll() is not None:
                        _time.sleep(1)
                        if getattr(self, '_wl_running', False):
                            try:
                                self._wl_watch_proc = subprocess.Popen(
                                    ["wl-paste", "--no-newline", "--watch", "cat"],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL
                                )
                                proc = self._wl_watch_proc
                            except Exception:
                                _time.sleep(5)
                    continue

                # Decode text
                try:
                    text = data.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue

                if not text:
                    continue

                # Skip the short window after paste operation
                if _time.time() < self._skip_change_until:
                    continue

                if text != self._last_text:
                    self._last_text = text
                    GLib.idle_add(self._handle_wl_paste_text, text)

            except Exception:
                _time.sleep(1)

    def _handle_wl_paste_text(self, text):
        """Handle text captured by wl-paste in main thread"""
        entry = self.store.add("text", text)
        if entry and self.on_change:
            self.on_change(entry)
        return False  # GLib.idle_add does not repeat

    # ── Internal Methods (GTK Channel) ─────────────────────────────

    def _on_owner_change(self, clipboard, event):
        """Clipboard owner change callback"""
        if self._ignore_next:
            self._ignore_next = False
            return
        # Delay reading slightly to ensure new content is available
        GLib.timeout_add(100, self._read_current)

    def _read_current(self):
        """Read current clipboard content"""
        try:
            # Try reading text
            text = self.clipboard.wait_for_text()
            if text and text != self._last_text:
                self._last_text = text
                entry = self.store.add("text", text)
                if entry and self.on_change:
                    self.on_change(entry)
                return False

            # Try reading image
            pixbuf = self.clipboard.wait_for_image()
            if pixbuf:
                # Convert to PNG bytes
                success, buf = pixbuf.save_to_bufferv("png", [], [])
                if success:
                    img_hash = hash(buf)
                    if img_hash != self._last_image_hash:
                        self._last_image_hash = img_hash
                        entry = self.store.add_image(buf)
                        if entry and self.on_change:
                            self.on_change(entry)

        except Exception as e:
            print(f"[WinVX] Failed to read clipboard: {e}")
            traceback.print_exc()

        return False  # GLib.timeout_add does not repeat
