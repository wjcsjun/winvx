"""
clipboard_ui.py — Windows 11 style clipboard popup UI
Dark glassmorphism theme, search filtering, keyboard navigation, click to paste
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


# ── CSS Styles (Win11 Dark Theme) ───────────────────────────

CSS = """
/* ── Global ── */
* {
    font-family: "Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
}

/* ── Main Window (Background manually drawn by draw signal) ── */
#winvx-window {
    background-color: transparent;
}

/* ── Header ── */
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

/* ── Search Bar ── */
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

/* ── List Scroll Area ── */
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

/* ── List Container ── */
#winvx-list {
    background: transparent;
    padding: 2px 8px;
}

/* ── Single Record ── */
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

/* ── Item Content ── */
.clip-preview {
    color: #e8e8e8;
    font-size: 13px;
}
.clip-meta {
    color: #888888;
    font-size: 11px;
    margin-top: 4px;
}

/* ── Action Buttons ── */
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

/* ── Footer Toolbar ── */
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

/* ── Empty State ── */
.empty-label {
    color: #777777;
    font-size: 14px;
    padding: 40px 20px;
}

/* ── Image Thumbnails ── */
.clip-image-preview {
    border-radius: 6px;
    margin-top: 6px;
}
"""


class ClipboardPopup(Gtk.Window):
    """Win11 style clipboard popup"""

    WINDOW_WIDTH = 380
    WINDOW_HEIGHT = 520

    def __init__(self, store: ClipStore, on_paste: Optional[Callable] = None,
                 wayland: bool = False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.store = store
        self.on_paste = on_paste
        self._selected_index = -1
        self._visible_entries: list[ClipEntry] = []
        self._pasting = False  # Pasting flag to avoid focus-out interference
        self._wayland = wayland

        self._setup_window()
        self._apply_css()
        self._build_ui()

    # ── Window Settings ──────────────────────────────────────────

    def _setup_window(self):
        self.set_name("winvx-window")
        self.set_title("WinVX Clipboard")
        self.set_default_size(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        self.set_resizable(False)
        self.set_decorated(False)         # No border
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)  # DIALOG can obtain keyboard focus
        self.set_accept_focus(True)
        self.set_can_focus(True)

        # Support RGBA transparency (for rounded corners external transparency)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        # Manually draw window background (rounded dark corners + border)
        self.connect("draw", self._on_draw)

        # Auto-hide on focus-out
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
        """Manually draw window background: dark rounded rectangle + subtle border"""
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = 12  # Corner radius

        # Clear first (full transparency)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()

        # Draw rounded rectangle path
        cr.new_path()
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)           # Top left
        cr.arc(w - r, r, r, 1.5 * math.pi, 2 * math.pi)   # Top right
        cr.arc(w - r, h - r, r, 0, 0.5 * math.pi)         # Bottom right
        cr.arc(r, h - r, r, 0.5 * math.pi, math.pi)       # Bottom left
        cr.close_path()

        # Fill dark background (nearly opaque)
        cr.set_source_rgba(0.13, 0.13, 0.13, 0.97)  # #212121, 97% opaque
        cr.fill_preserve()

        # Draw border
        cr.set_source_rgba(0.3, 0.3, 0.3, 0.6)  # Subtle gray border
        cr.set_line_width(1)
        cr.stroke()

        # Switch back to OVER mode to let child widgets draw normally
        cr.set_operator(cairo.OPERATOR_OVER)
        return False  # Continue propagation to let child widgets draw

    # ── Build UI ──────────────────────────────────────────────────

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # ── Title ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_name("winvx-header")
        title = Gtk.Label(label="📋  Clipboard History")
        title.set_name("winvx-title")
        title.set_halign(Gtk.Align.START)
        header.pack_start(title, True, True, 0)
        main_box.pack_start(header, False, False, 0)

        # ── Search Bar ──
        self.search_entry = Gtk.Entry()
        self.search_entry.set_name("winvx-search")
        self.search_entry.set_placeholder_text("Search clipboard content...")
        self.search_entry.connect("changed", self._on_search_changed)
        # Intercept Up/Down/Enter/Esc on search box to prevent them from being swallowed by input box
        self.search_entry.connect("key-press-event", self._on_key_press)
        main_box.pack_start(self.search_entry, False, False, 0)

        # ── Scroll List ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_name("winvx-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.list_box.set_name("winvx-list")
        scroll.add(self.list_box)
        main_box.pack_start(scroll, True, True, 0)

        # ── Footer Toolbar ──
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_name("winvx-footer")

        clear_btn = Gtk.Button(label="🗑  Clear All")
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
        self.hide()  # Hidden by default

    # ── Show/Hide ─────────────────────────────────────────────────

    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.popup()

    def popup(self):
        """Show window, follow mouse position"""
        self._refresh_list()
        self.search_entry.set_text("")
        self._selected_index = -1

        # Get mouse position and position popup
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        _, mx, my = pointer.get_position()

        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()

        # Popup slightly below mouse position, horizontally centered to mouse
        x = max(geom.x, min(mx - self.WINDOW_WIDTH // 2,
                            geom.x + geom.width - self.WINDOW_WIDTH))
        y = max(geom.y, min(my + 20,
                            geom.y + geom.height - self.WINDOW_HEIGHT))
        self.move(x, y)

        self.show_all()

        # Forcefully grab focus (multiple ways to ensure success)
        self.present_with_time(Gdk.CURRENT_TIME)
        if self.get_window():
            self.get_window().focus(Gdk.CURRENT_TIME)
        self.search_entry.grab_focus()

        # Delay again to grab focus (some WMs need a frame)
        GLib.timeout_add(50, self._force_focus)
        GLib.timeout_add(150, self._force_focus)

    def _force_focus(self):
        """Force grab focus (fallback)"""
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

    # ── List Refresh ──────────────────────────────────────────────

    def _refresh_list(self, query: str = ""):
        """Re-render item list"""
        # Clear
        for child in self.list_box.get_children():
            self.list_box.remove(child)

        if query:
            entries = self.store.search(query)
        else:
            entries = self.store.entries

        self._visible_entries = entries

        if not entries:
            empty = Gtk.Label(label="No clipboard records yet")
            empty.get_style_context().add_class("empty-label")
            self.list_box.pack_start(empty, True, True, 0)
            self.count_label.set_text("")
        else:
            for i, entry in enumerate(entries):
                item = self._create_item_widget(entry, i)
                self.list_box.pack_start(item, False, False, 0)
            total = len(self.store.entries)
            self.count_label.set_text(f"{total} items")

        self.list_box.show_all()

    def _create_item_widget(self, entry: ClipEntry, index: int) -> Gtk.Widget:
        """Create widget for a single record"""
        # Outer event box (clickable)
        event_box = Gtk.EventBox()
        event_box.connect("button-press-event",
                          lambda w, e, ent=entry: self._on_item_click(ent))

        item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctx = item_box.get_style_context()
        ctx.add_class("clip-item")
        if entry.pinned:
            ctx.add_class("pinned")

        # Save reference for keyboard navigation
        item_box._entry = entry
        item_box._index = index

        # ── Left Content ──
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content_box.set_hexpand(True)

        if entry.content_type == "image":
            # Image preview
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
            # Text preview (max 3 lines)
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

        # Time label
        time_str = self._format_time(entry.timestamp)
        meta = Gtk.Label(label=time_str)
        meta.get_style_context().add_class("clip-meta")
        meta.set_halign(Gtk.Align.START)
        content_box.pack_start(meta, False, False, 0)

        item_box.pack_start(content_box, True, True, 0)

        # ── Right Action Buttons ──
        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        btn_box.set_valign(Gtk.Align.CENTER)

        # Pin button
        pin_btn = Gtk.Button(label="📌")
        pin_btn.get_style_context().add_class("clip-action-btn")
        if entry.pinned:
            pin_btn.get_style_context().add_class("clip-pin-active")
        pin_btn.set_tooltip_text("Pin" if not entry.pinned else "Unpin")
        pin_btn.connect("clicked",
                        lambda w, eid=entry.id: self._on_pin(eid))
        btn_box.pack_start(pin_btn, False, False, 0)

        # Delete button
        del_btn = Gtk.Button(label="✕")
        del_btn.get_style_context().add_class("clip-action-btn")
        del_btn.set_tooltip_text("Delete")
        del_btn.connect("clicked",
                        lambda w, eid=entry.id: self._on_delete(eid))
        btn_box.pack_start(del_btn, False, False, 0)

        item_box.pack_end(btn_box, False, False, 0)

        event_box.add(item_box)
        return event_box

    # ── Event Handling ────────────────────────────────────────────

    def _on_item_click(self, entry: ClipEntry):
        """Click item → Paste"""
        self._pasting = True  # Mark pasting, prevent focus-out hiding
        if self.on_paste:
            self.on_paste(entry)
        self.hide()
        # Reset flag after paste is complete
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
        """Hide on focus-out"""
        if self._pasting:
            return False  # Do not hide while pasting
        # Delay slightly to avoid accidental trigger when clicking buttons
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
        """Keyboard navigation"""
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
        """Move keyboard selection"""
        if not self._visible_entries:
            return

        old = self._selected_index
        new = max(0, min(len(self._visible_entries) - 1, old + delta))
        if new == old:
            return

        self._selected_index = new

        # Update visual highlight
        children = self.list_box.get_children()
        for i, child in enumerate(children):
            # child is EventBox, item_box inside
            inner = child.get_children()[0] if isinstance(child, Gtk.EventBox) else child
            ctx = inner.get_style_context()
            if i == new:
                ctx.add_class("selected")
                # Scroll to visible area
                adj = self.list_box.get_parent().get_vadjustment()
                alloc = child.get_allocation()
                if alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
                    adj.set_value(alloc.y + alloc.height - adj.get_page_size())
                elif alloc.y < adj.get_value():
                    adj.set_value(alloc.y)
            else:
                ctx.remove_class("selected")

    # ── Utility Methods ───────────────────────────────────────────

    @staticmethod
    def _format_time(ts: float) -> str:
        """Format time as relative description"""
        diff = time.time() - ts
        if diff < 60:
            return "Just now"
        elif diff < 3600:
            return f"{int(diff // 60)} minutes ago"
        elif diff < 86400:
            return f"{int(diff // 3600)} hours ago"
        elif diff < 604800:
            return f"{int(diff // 86400)} days ago"
        else:
            import datetime
            return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    def refresh(self):
        """External call: refresh list (when new items added)"""
        if self.get_visible():
            query = self.search_entry.get_text()
            self._refresh_list(query)
