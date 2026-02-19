"""
clip_store.py — ClipEntry data model and JSON persistence
WinVX: Windows 11 Win+V style clipboard manager
"""

import json
import os
import time
import uuid
import base64
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ── Data Directory ──────────────────────────────────────────

DATA_DIR = Path(os.environ.get(
    "WINVX_DATA_DIR",
    os.path.expanduser("~/.local/share/winvx")
))
HISTORY_FILE = DATA_DIR / "history.json"
IMAGES_DIR = DATA_DIR / "images"

MAX_ITEMS = 25          # Limit for non-pinned items (consistent with Win11)
MAX_CONTENT_LEN = 4096  # Max character count for text content


# ── ClipEntry Dataclass ───────────────────────────────────────

@dataclass
class ClipEntry:
    """A single clipboard history record"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content_type: str = "text"          # text | image | html
    content: str = ""                   # Text content / Image filename
    preview: str = ""                   # Preview text (truncated)
    timestamp: float = field(default_factory=time.time)
    pinned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClipEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── ClipStore Management ───────────────────────────────────────

class ClipStore:
    """Manage CRUD operations and persistence of clipboard history"""

    def __init__(self, max_items: int = MAX_ITEMS):
        self.max_items = max_items
        self._entries: list[ClipEntry] = []
        self._ensure_dirs()
        self._load()

    # ── Public API ────────────────────────────────────────────────

    @property
    def entries(self) -> list[ClipEntry]:
        """Return all entries (pinned first, reverse chronological order)"""
        pinned = [e for e in self._entries if e.pinned]
        normal = [e for e in self._entries if not e.pinned]
        pinned.sort(key=lambda e: e.timestamp, reverse=True)
        normal.sort(key=lambda e: e.timestamp, reverse=True)
        return pinned + normal

    def add(self, content_type: str, content: str, preview: str = "") -> Optional[ClipEntry]:
        """Add new entry, auto-deduplicate and limit. Returns new entry or None."""
        if not content or not content.strip():
            return None

        # Deduplication: if content exists, move to top (update timestamp)
        for existing in self._entries:
            if existing.content_type == content_type and existing.content == content:
                existing.timestamp = time.time()
                self._save()
                return existing

        entry = ClipEntry(
            content_type=content_type,
            content=content[:MAX_CONTENT_LEN] if content_type == "text" else content,
            preview=preview or self._make_preview(content_type, content),
            timestamp=time.time(),
        )
        self._entries.append(entry)
        self._enforce_limit()
        self._save()
        return entry

    def add_image(self, image_bytes: bytes, fmt: str = "png") -> Optional[ClipEntry]:
        """Save image to disk and add entry"""
        if not image_bytes:
            return None
        filename = f"{uuid.uuid4().hex[:12]}.{fmt}"
        filepath = IMAGES_DIR / filename
        filepath.write_bytes(image_bytes)

        entry = ClipEntry(
            content_type="image",
            content=filename,
            preview=f"[Image {len(image_bytes)//1024}KB]",
            timestamp=time.time(),
        )
        self._entries.append(entry)
        self._enforce_limit()
        self._save()
        return entry

    def delete(self, entry_id: str) -> bool:
        """Delete entry by id"""
        for i, e in enumerate(self._entries):
            if e.id == entry_id:
                removed = self._entries.pop(i)
                # Clean up image file
                if removed.content_type == "image":
                    img_path = IMAGES_DIR / removed.content
                    if img_path.exists():
                        img_path.unlink()
                self._save()
                return True
        return False

    def toggle_pin(self, entry_id: str) -> bool:
        """Toggle pinned status"""
        for e in self._entries:
            if e.id == entry_id:
                e.pinned = not e.pinned
                self._save()
                return True
        return False

    def clear(self, keep_pinned: bool = True):
        """Clear history (keep pinned items by default)"""
        if keep_pinned:
            removed = [e for e in self._entries if not e.pinned]
            self._entries = [e for e in self._entries if e.pinned]
        else:
            removed = list(self._entries)
            self._entries.clear()

        # 清理图片文件
        for e in removed:
            if e.content_type == "image":
                img_path = IMAGES_DIR / e.content
                if img_path.exists():
                    img_path.unlink()
        self._save()

    def search(self, query: str) -> list[ClipEntry]:
        """Search entries (fuzzy match)"""
        if not query:
            return self.entries
        q = query.lower()
        results = [e for e in self.entries if q in e.preview.lower() or q in e.content.lower()]
        return results

    def get_image_path(self, entry: ClipEntry) -> Optional[Path]:
        """Get full path for image entry"""
        if entry.content_type != "image":
            return None
        path = IMAGES_DIR / entry.content
        return path if path.exists() else None

    # ── Internal Methods ──────────────────────────────────────────

    def _make_preview(self, content_type: str, content: str) -> str:
        if content_type == "image":
            return "[Image]"
        # Text preview: first line, truncated to 80 chars
        line = content.split("\n")[0].strip()
        return line[:80] + ("…" if len(line) > 80 else "")

    def _enforce_limit(self):
        """Limit non-pinned entries"""
        normal = [e for e in self._entries if not e.pinned]
        if len(normal) > self.max_items:
            # Sort by time ascending, delete oldest
            normal.sort(key=lambda e: e.timestamp)
            to_remove = normal[:len(normal) - self.max_items]
            for e in to_remove:
                self._entries.remove(e)
                if e.content_type == "image":
                    img_path = IMAGES_DIR / e.content
                    if img_path.exists():
                        img_path.unlink()

    def _ensure_dirs(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self):
        if not HISTORY_FILE.exists():
            self._entries = []
            return
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = [ClipEntry.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            self._entries = []

    def _save(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self._entries], f,
                          ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[WinVX] Failed to save history: {e}")
