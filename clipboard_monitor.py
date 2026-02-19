"""
clipboard_monitor.py — 剪贴板监控模块
通过 GTK Clipboard 的 owner-change 信号实时捕获剪贴板变化
Wayland 模式下额外使用 wl-paste --watch 后台进程确保后台捕获
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
    """监控系统剪贴板变化并写入 ClipStore"""

    def __init__(self, store: ClipStore, on_change: Optional[Callable] = None,
                 wayland: bool = False):
        self.store = store
        self.on_change = on_change  # 回调: 新条目添加时触发
        self._last_text = None
        self._last_image_hash = None
        self._ignore_next = False   # 用于粘贴时忽略自己触发的 owner-change
        self._wayland = wayland
        self._wl_watch_proc = None
        self._skip_change_until = 0  # 时间戳: 在此之前跳过 wl-paste 变化检测

        # 获取系统剪贴板
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        if self._wayland:
            # Wayland: 仅使用 wl-paste 轮询, 不连接 GTK owner-change
            # (两者同时启用会导致重复触发, 弹窗闪烁)
            self._start_wl_paste_watch()
        else:
            # X11: 使用 GTK owner-change 信号
            self.clipboard.connect("owner-change", self._on_owner_change)

        # 初始化: 读取当前内容
        self._read_current()

    def stop(self):
        """停止监控 (退出时调用)"""
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
        """粘贴操作前调用, 忽略下一次 owner-change"""
        self._ignore_next = True

    def paste_entry(self, entry: ClipEntry):
        """将指定条目内容设置到剪贴板 (准备粘贴)"""
        self._ignore_next = True
        # Wayland: 跳过接下来 2 秒内的 wl-paste 变化检测
        # (避免我们自己设置的内容触发列表刷新)
        import time as _time
        self._skip_change_until = _time.time() + 2.0
        # 记住我们要粘贴的内容, 防止 wl-paste 检测到后重复处理
        if entry.content_type in ("text", "html"):
            self._last_text = entry.content

        if self._wayland:
            # Wayland: 仅用 wl-copy (不要同时设置 GTK 剪贴板, 会冲突导致空内容)
            self._paste_entry_wayland(entry)
        else:
            # X11: 使用 GTK 剪贴板
            self._paste_entry_gtk(entry)

    def _paste_entry_gtk(self, entry: ClipEntry):
        """GTK 方式设置剪贴板"""
        if entry.content_type == "text" or entry.content_type == "html":
            self.clipboard.set_text(entry.content, -1)
        elif entry.content_type == "image":
            img_path = self.store.get_image_path(entry)
            if img_path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(img_path))
                    self.clipboard.set_image(pixbuf)
                except Exception as e:
                    print(f"[WinVX] 粘贴图片失败: {e}")

    def _paste_entry_wayland(self, entry: ClipEntry):
        """Wayland: 使用 wl-copy 设置剪贴板 (更可靠)"""
        try:
            if entry.content_type == "text" or entry.content_type == "html":
                content = entry.content
                print(f"[WinVX] DEBUG: 准备写入剪贴板: '{content[:50]}...' (len={len(content)})")
                # 通过 stdin 管道传递内容 (wl-copy 会持续运行以服务剪贴板, 不能 wait)
                proc = subprocess.Popen(
                    ["wl-copy"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                proc.stdin.write(content.encode("utf-8"))
                proc.stdin.close()
                # 不等待 wl-copy 退出, 它会在后台持续运行直到其他应用接管剪贴板
                # 验证: 读回剪贴板内容
                try:
                    verify = subprocess.run(
                        ["wl-paste", "--no-newline"],
                        capture_output=True, timeout=1
                    )
                    actual = verify.stdout.decode("utf-8", errors="replace")[:50]
                    print(f"[WinVX] DEBUG: 验证剪贴板: '{actual}'")
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
            print("[WinVX] ✗ wl-copy 未安装, 回退到 GTK")
            self._paste_entry_gtk(entry)
        except Exception as e:
            print(f"[WinVX] wl-copy 失败, 回退到 GTK: {e}")
            self._paste_entry_gtk(entry)

    # ── wl-paste 后台监控 (Wayland) ──────────────────────────────

    def _start_wl_paste_watch(self):
        """启动 wl-paste --watch 事件驱动监控 (仅在剪贴板变化时触发)"""
        try:
            # wl-paste --watch: 每次剪贴板变化时执行指定命令
            # 使用 "cat" 作为命令, 它会将剪贴板内容输出到 stdout
            # 每次变化产生一份输出, 输出之间没有分隔符
            # 所以我们用行模式逐行读, 然后分批处理
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
            print("[WinVX] ✓ wl-paste --watch 事件监控已启动")
        except FileNotFoundError:
            print("[WinVX] ⚠ wl-paste 未安装, 后台监控不可用")
            print("[WinVX]   请安装: sudo apt install wl-clipboard")
        except Exception as e:
            print(f"[WinVX] ⚠ wl-paste 监控启动失败: {e}")

    def _wl_paste_watch_loop(self):
        """后台线程: 读取 wl-paste --watch 的输出, 仅在剪贴板变化时触发"""
        import time as _time
        import select
        proc = self._wl_watch_proc
        if not proc or not proc.stdout:
            return

        fd = proc.stdout.fileno()

        while getattr(self, '_wl_running', False):
            try:
                # 等待有数据可读 (非阻塞, 但不消耗 CPU)
                readable, _, _ = select.select([fd], [], [], 1.0)
                if not readable:
                    continue

                # 有新数据 — 剪贴板发生了变化
                # 短暂等待以收集完整内容 (wl-paste 可能分多次写)
                _time.sleep(0.05)

                # 读取所有可用数据
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
                    # 进程可能已退出
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

                # 解码文本
                try:
                    text = data.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue

                if not text:
                    continue

                # 跳过粘贴操作后的短暂时间窗口
                if _time.time() < self._skip_change_until:
                    continue

                if text != self._last_text:
                    self._last_text = text
                    GLib.idle_add(self._handle_wl_paste_text, text)

            except Exception:
                _time.sleep(1)

    def _handle_wl_paste_text(self, text):
        """在主线程中处理 wl-paste 捕获的文本"""
        entry = self.store.add("text", text)
        if entry and self.on_change:
            self.on_change(entry)
        return False

    # ── 内部方法 (GTK 通道) ───────────────────────────────────

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
