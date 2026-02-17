"""
clipboard_monitor.py — 剪贴板监控模块
通过 GTK Clipboard 的 owner-change 信号实时捕获剪贴板变化
"""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

import io
import traceback
from typing import Callable, Optional
from clip_store import ClipStore, ClipEntry


class ClipboardMonitor:
    """监控系统剪贴板变化并写入 ClipStore"""

    def __init__(self, store: ClipStore, on_change: Optional[Callable] = None):
        self.store = store
        self.on_change = on_change  # 回调: 新条目添加时触发
        self._last_text = None
        self._last_image_hash = None
        self._ignore_next = False   # 用于粘贴时忽略自己触发的 owner-change

        # 获取系统剪贴板
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.connect("owner-change", self._on_owner_change)

        # 初始化: 读取当前内容
        self._read_current()

    def set_ignore_next(self):
        """粘贴操作前调用, 忽略下一次 owner-change"""
        self._ignore_next = True

    def paste_entry(self, entry: ClipEntry):
        """将指定条目内容设置到剪贴板 (准备粘贴)"""
        self._ignore_next = True
        if entry.content_type == "text" or entry.content_type == "html":
            self.clipboard.set_text(entry.content, -1)
            # 注意: 不调用 clipboard.store()
            # store() 是同步 X11 调用, 会阻塞 1-2 秒
            # set_text() 已足够, xdotool Ctrl+V 会读取内容
        elif entry.content_type == "image":
            img_path = self.store.get_image_path(entry)
            if img_path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(img_path))
                    self.clipboard.set_image(pixbuf)
                except Exception as e:
                    print(f"[WinVX] 粘贴图片失败: {e}")

    # ── 内部方法 ──────────────────────────────────────────────

    def _on_owner_change(self, clipboard, event):
        """剪贴板所有者变更回调"""
        if self._ignore_next:
            self._ignore_next = False
            return
        # 延迟一点读取, 确保新内容可用
        GLib.timeout_add(100, self._read_current)

    def _read_current(self):
        """读取当前剪贴板内容"""
        try:
            # 尝试读取文本
            text = self.clipboard.wait_for_text()
            if text and text != self._last_text:
                self._last_text = text
                entry = self.store.add("text", text)
                if entry and self.on_change:
                    self.on_change(entry)
                return False

            # 尝试读取图片
            pixbuf = self.clipboard.wait_for_image()
            if pixbuf:
                # 转为 PNG bytes
                success, buf = pixbuf.save_to_bufferv("png", [], [])
                if success:
                    img_hash = hash(buf)
                    if img_hash != self._last_image_hash:
                        self._last_image_hash = img_hash
                        entry = self.store.add_image(buf)
                        if entry and self.on_change:
                            self.on_change(entry)

        except Exception as e:
            print(f"[WinVX] 读取剪贴板失败: {e}")
            traceback.print_exc()

        return False  # GLib.timeout_add 不重复
