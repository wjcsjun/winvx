"""
clipboard_ui.py — Windows 11 风格剪贴板弹窗 UI
深色毛玻璃主题, 搜索过滤, 键盘导航, 点击即粘贴
"""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango

import time
import os
import math
import cairo
from typing import Optional, Callable
from clip_store import ClipStore, ClipEntry, IMAGES_DIR


# ── CSS 样式 (Win11 深色主题) ─────────────────────────────────

CSS = """
/* ── 全局 ── */
* {
    font-family: "Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
}

/* ── 主窗口 (背景由 draw 信号手动绘制) ── */
#winvx-window {
    background-color: transparent;
}

/* ── 标题栏 ── */
#winvx-header {
    background: transparent;
    padding: 14px 18px 6px 18px;
}
#winvx-title {
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.3px;
}

/* ── 搜索栏 ── */
#winvx-search {
    background-color: #3a3a3a;
    border: 1px solid #4a4a4a;
    border-radius: 8px;
    color: #e8e8e8;
    padding: 8px 14px;
    margin: 6px 16px 8px 16px;
    font-size: 13px;
    caret-color: #60cdff;
}
#winvx-search:focus {
    border-color: #60cdff;
    background-color: #404040;
}

/* ── 列表滚动区 ── */
#winvx-scroll {
    background: transparent;
    min-height: 100px;
}
#winvx-scroll scrollbar {
    background: transparent;
    min-width: 6px;
}
#winvx-scroll scrollbar slider {
    background-color: #555555;
    border-radius: 4px;
    min-width: 6px;
}

/* ── 列表容器 ── */
#winvx-list {
    background: transparent;
    padding: 2px 8px;
}

/* ── 单条记录 ── */
.clip-item {
    background-color: #2d2d2d;
    border: 1px solid #3e3e3e;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 3px 8px;
}
.clip-item:hover {
    background-color: #383838;
    border-color: #4fa8d4;
}
.clip-item.selected {
    background-color: #1a3a50;
    border-color: #60cdff;
}
.clip-item.pinned {
    border-left: 3px solid #60cdff;
}

/* ── 条目内容 ── */
.clip-preview {
    color: #e8e8e8;
    font-size: 13px;
}
.clip-meta {
    color: #888888;
    font-size: 11px;
    margin-top: 4px;
}

/* ── 操作按钮 ── */
.clip-action-btn {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    min-width: 28px;
    min-height: 28px;
    color: #999999;
    font-size: 14px;
}
.clip-action-btn:hover {
    background-color: #444444;
    color: #ffffff;
}
.clip-pin-active {
    color: #60cdff;
}

/* ── 底部工具栏 ── */
#winvx-footer {
    background: transparent;
    padding: 6px 16px 12px 16px;
    border-top: 1px solid #3e3e3e;
}
.footer-btn {
    background: #2d2d2d;
    border: 1px solid #4a4a4a;
    border-radius: 6px;
    color: #aaaaaa;
    font-size: 12px;
    padding: 5px 14px;
}
.footer-btn:hover {
    background-color: #404040;
    color: #ffffff;
    border-color: #60cdff;
}

/* ── 空状态 ── */
.empty-label {
    color: #777777;
    font-size: 14px;
    padding: 40px 20px;
}

/* ── 图片缩略图 ── */
.clip-image-preview {
    border-radius: 6px;
    margin-top: 6px;
}
"""


class ClipboardPopup(Gtk.Window):
    """Win11 风格剪贴板弹窗"""

    WINDOW_WIDTH = 380
    WINDOW_HEIGHT = 520

    def __init__(self, store: ClipStore, on_paste: Optional[Callable] = None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.store = store
        self.on_paste = on_paste
        self._selected_index = -1
        self._visible_entries: list[ClipEntry] = []
        self._pasting = False  # 粘贴中标志, 避免 focus-out 干扰

        self._setup_window()
        self._apply_css()
        self._build_ui()

    # ── 窗口设置 ──────────────────────────────────────────────

    def _setup_window(self):
        self.set_name("winvx-window")
        self.set_title("WinVX Clipboard")
        self.set_default_size(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        self.set_resizable(False)
        self.set_decorated(False)         # 无边框
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)  # DIALOG 可以获得键盘焦点
        self.set_accept_focus(True)
        self.set_can_focus(True)

        # 支持 RGBA 透明 (用于圆角外部透明区域)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        # 手动绘制窗口背景 (圆角深色 + 边框)
        self.connect("draw", self._on_draw)

        # 失焦自动隐藏
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda w, e: self.hide() or True)

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_draw(self, widget, cr):
        """手动绘制窗口背景: 深色圆角矩形 + 微妙边框"""
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = 12  # 圆角半径

        # 先清空 (完全透明)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()

        # 绘制圆角矩形路径
        cr.new_path()
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)           # 左上
        cr.arc(w - r, r, r, 1.5 * math.pi, 2 * math.pi)   # 右上
        cr.arc(w - r, h - r, r, 0, 0.5 * math.pi)         # 右下
        cr.arc(r, h - r, r, 0.5 * math.pi, math.pi)       # 左下
        cr.close_path()

        # 填充深色背景 (近乎不透明)
        cr.set_source_rgba(0.13, 0.13, 0.13, 0.97)  # #212121, 97% 不透明
        cr.fill_preserve()

        # 绘制边框
        cr.set_source_rgba(0.3, 0.3, 0.3, 0.6)  # 微妙灰色边框
        cr.set_line_width(1)
        cr.stroke()

        # 切换回 OVER 模式, 让子控件正常绘制
        cr.set_operator(cairo.OPERATOR_OVER)
        return False  # 继续传播, 让子控件绘制

    # ── 构建 UI ──────────────────────────────────────────────

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # ── 标题 ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_name("winvx-header")
        title = Gtk.Label(label="📋  剪贴板历史")
        title.set_name("winvx-title")
        title.set_halign(Gtk.Align.START)
        header.pack_start(title, True, True, 0)
        main_box.pack_start(header, False, False, 0)

        # ── 搜索栏 ──
        self.search_entry = Gtk.Entry()
        self.search_entry.set_name("winvx-search")
        self.search_entry.set_placeholder_text("搜索剪贴板内容…")
        self.search_entry.connect("changed", self._on_search_changed)
        # 在搜索框上拦截 Up/Down/Enter/Esc, 防止被输入框吞掉
        self.search_entry.connect("key-press-event", self._on_key_press)
        main_box.pack_start(self.search_entry, False, False, 0)

        # ── 滚动列表 ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_name("winvx-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.list_box.set_name("winvx-list")
        scroll.add(self.list_box)
        main_box.pack_start(scroll, True, True, 0)

        # ── 底部工具栏 ──
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_name("winvx-footer")

        clear_btn = Gtk.Button(label="🗑  全部清除")
        clear_btn.get_style_context().add_class("footer-btn")
        clear_btn.connect("clicked", self._on_clear_all)
        footer.pack_end(clear_btn, False, False, 0)

        count_label = Gtk.Label()
        count_label.get_style_context().add_class("clip-meta")
        count_label.set_halign(Gtk.Align.START)
        self.count_label = count_label
        footer.pack_start(count_label, True, True, 0)

        main_box.pack_start(footer, False, False, 0)

        self.show_all()
        self.hide()  # 默认隐藏

    # ── 显示/隐藏 ─────────────────────────────────────────────

    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.popup()

    def popup(self):
        """在屏幕中央弹出窗口并抢夺焦点"""
        self._refresh_list()
        self.search_entry.set_text("")
        self._selected_index = -1

        # 定位: 屏幕中央偏下
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()

        x = geom.x + (geom.width - self.WINDOW_WIDTH) // 2
        y = geom.y + (geom.height - self.WINDOW_HEIGHT) // 2 + 60

        self.move(x, y)
        self.show_all()

        # 强制抢夺焦点 (多种方式确保成功)
        self.present_with_time(Gdk.CURRENT_TIME)
        self.get_window().focus(Gdk.CURRENT_TIME)
        self.search_entry.grab_focus()

        # 延迟再次抢焦点 (有些 WM 需要等一帧)
        GLib.timeout_add(50, self._force_focus)
        GLib.timeout_add(150, self._force_focus)

    def _force_focus(self):
        """强制抢焦点 (兜底)"""
        if self.get_visible():
            try:
                self.present_with_time(Gdk.CURRENT_TIME)
                win = self.get_window()
                if win:
                    win.focus(Gdk.CURRENT_TIME)
                self.search_entry.grab_focus()
            except Exception:
                pass
        return False

    # ── 列表刷新 ──────────────────────────────────────────────

    def _refresh_list(self, query: str = ""):
        """重新渲染条目列表"""
        # 清空
        for child in self.list_box.get_children():
            self.list_box.remove(child)

        if query:
            entries = self.store.search(query)
        else:
            entries = self.store.entries

        self._visible_entries = entries

        if not entries:
            empty = Gtk.Label(label="暂无剪贴板记录")
            empty.get_style_context().add_class("empty-label")
            self.list_box.pack_start(empty, True, True, 0)
            self.count_label.set_text("")
        else:
            for i, entry in enumerate(entries):
                item = self._create_item_widget(entry, i)
                self.list_box.pack_start(item, False, False, 0)
            total = len(self.store.entries)
            self.count_label.set_text(f"{total} 条记录")

        self.list_box.show_all()

    def _create_item_widget(self, entry: ClipEntry, index: int) -> Gtk.Widget:
        """创建单条记录的 Widget"""
        # 外层事件盒 (可点击)
        event_box = Gtk.EventBox()
        event_box.connect("button-press-event",
                          lambda w, e, ent=entry: self._on_item_click(ent))

        item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctx = item_box.get_style_context()
        ctx.add_class("clip-item")
        if entry.pinned:
            ctx.add_class("pinned")

        # 保存引用用于键盘导航
        item_box._entry = entry
        item_box._index = index

        # ── 左侧内容 ──
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content_box.set_hexpand(True)

        if entry.content_type == "image":
            # 图片预览
            img_path = self.store.get_image_path(entry)
            if img_path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(img_path), 200, 80, True)
                    image = Gtk.Image.new_from_pixbuf(pixbuf)
                    image.get_style_context().add_class("clip-image-preview")
                    image.set_halign(Gtk.Align.START)
                    content_box.pack_start(image, False, False, 0)
                except Exception:
                    label = Gtk.Label(label=entry.preview)
                    label.get_style_context().add_class("clip-preview")
                    label.set_halign(Gtk.Align.START)
                    content_box.pack_start(label, False, False, 0)
            else:
                label = Gtk.Label(label=entry.preview)
                label.get_style_context().add_class("clip-preview")
                label.set_halign(Gtk.Align.START)
                content_box.pack_start(label, False, False, 0)
        else:
            # 文本预览 (最多 3 行)
            preview_text = entry.content[:200]
            lines = preview_text.split("\n")[:3]
            display_text = "\n".join(lines)
            if len(entry.content) > 200 or len(preview_text.split("\n")) > 3:
                display_text += "…"

            label = Gtk.Label(label=display_text)
            label.get_style_context().add_class("clip-preview")
            label.set_halign(Gtk.Align.START)
            label.set_xalign(0)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.CHAR)
            label.set_max_width_chars(40)
            label.set_lines(3)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            content_box.pack_start(label, False, False, 0)

        # 时间标签
        time_str = self._format_time(entry.timestamp)
        meta = Gtk.Label(label=time_str)
        meta.get_style_context().add_class("clip-meta")
        meta.set_halign(Gtk.Align.START)
        content_box.pack_start(meta, False, False, 0)

        item_box.pack_start(content_box, True, True, 0)

        # ── 右侧操作按钮 ──
        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        btn_box.set_valign(Gtk.Align.CENTER)

        # 置顶按钮
        pin_btn = Gtk.Button(label="📌")
        pin_btn.get_style_context().add_class("clip-action-btn")
        if entry.pinned:
            pin_btn.get_style_context().add_class("clip-pin-active")
        pin_btn.set_tooltip_text("置顶" if not entry.pinned else "取消置顶")
        pin_btn.connect("clicked",
                        lambda w, eid=entry.id: self._on_pin(eid))
        btn_box.pack_start(pin_btn, False, False, 0)

        # 删除按钮
        del_btn = Gtk.Button(label="✕")
        del_btn.get_style_context().add_class("clip-action-btn")
        del_btn.set_tooltip_text("删除")
        del_btn.connect("clicked",
                        lambda w, eid=entry.id: self._on_delete(eid))
        btn_box.pack_start(del_btn, False, False, 0)

        item_box.pack_end(btn_box, False, False, 0)

        event_box.add(item_box)
        return event_box

    # ── 事件处理 ──────────────────────────────────────────────

    def _on_item_click(self, entry: ClipEntry):
        """点击条目 → 粘贴"""
        self._pasting = True  # 标记粘贴中, 阻止 focus-out 隐藏
        if self.on_paste:
            self.on_paste(entry)
        self.hide()
        # 粘贴完成后重置标志
        GLib.timeout_add(100, self._reset_pasting)

    def _on_pin(self, entry_id: str):
        self.store.toggle_pin(entry_id)
        query = self.search_entry.get_text()
        self._refresh_list(query)

    def _on_delete(self, entry_id: str):
        self.store.delete(entry_id)
        query = self.search_entry.get_text()
        self._refresh_list(query)

    def _on_clear_all(self, widget):
        self.store.clear(keep_pinned=True)
        query = self.search_entry.get_text()
        self._refresh_list(query)

    def _on_search_changed(self, entry):
        query = entry.get_text()
        self._refresh_list(query)

    def _on_focus_out(self, widget, event):
        """失焦隐藏"""
        if self._pasting:
            return False  # 粘贴中不隐藏
        # 延迟一点, 避免点击按钮时误触
        GLib.timeout_add(100, self._check_focus)
        return False

    def _check_focus(self):
        if not self.is_active() and not self._pasting:
            self.hide()
        return False

    def _reset_pasting(self):
        self._pasting = False
        return False

    def _on_key_press(self, widget, event):
        """键盘导航"""
        key = event.keyval

        if key == Gdk.KEY_Escape:
            self.hide()
            return True

        if key == Gdk.KEY_Return or key == Gdk.KEY_KP_Enter:
            if 0 <= self._selected_index < len(self._visible_entries):
                self._on_item_click(self._visible_entries[self._selected_index])
            return True

        if key == Gdk.KEY_Down:
            self._move_selection(1)
            return True

        if key == Gdk.KEY_Up:
            self._move_selection(-1)
            return True

        return False

    def _move_selection(self, delta: int):
        """移动键盘选中项"""
        if not self._visible_entries:
            return

        old = self._selected_index
        new = max(0, min(len(self._visible_entries) - 1, old + delta))
        if new == old:
            return

        self._selected_index = new

        # 更新视觉高亮
        children = self.list_box.get_children()
        for i, child in enumerate(children):
            # child 是 EventBox, 内部是 item_box
            inner = child.get_children()[0] if isinstance(child, Gtk.EventBox) else child
            ctx = inner.get_style_context()
            if i == new:
                ctx.add_class("selected")
                # 滚动到可见区域
                adj = self.list_box.get_parent().get_vadjustment()
                alloc = child.get_allocation()
                if alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
                    adj.set_value(alloc.y + alloc.height - adj.get_page_size())
                elif alloc.y < adj.get_value():
                    adj.set_value(alloc.y)
            else:
                ctx.remove_class("selected")

    # ── 工具方法 ──────────────────────────────────────────────

    @staticmethod
    def _format_time(ts: float) -> str:
        """格式化时间为相对描述"""
        diff = time.time() - ts
        if diff < 60:
            return "刚刚"
        elif diff < 3600:
            return f"{int(diff // 60)} 分钟前"
        elif diff < 86400:
            return f"{int(diff // 3600)} 小时前"
        elif diff < 604800:
            return f"{int(diff // 86400)} 天前"
        else:
            import datetime
            return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    def refresh(self):
        """外部调用: 刷新列表 (新条目添加时)"""
        if self.get_visible():
            query = self.search_entry.get_text()
            self._refresh_list(query)
