"""
clip_store.py — ClipEntry 数据模型与 JSON 持久化
WinVX: Windows 11 Win+V 风格剪贴板管理器
"""

import json
import os
import time
import uuid
import base64
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ── 数据目录 ──────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get(
    "WINVX_DATA_DIR",
    os.path.expanduser("~/.local/share/winvx")
))
HISTORY_FILE = DATA_DIR / "history.json"
IMAGES_DIR = DATA_DIR / "images"

MAX_ITEMS = 25          # 非置顶条目上限 (与 Win11 一致)
MAX_CONTENT_LEN = 4096  # 文本内容最大字符数


# ── ClipEntry 数据类 ──────────────────────────────────────────

@dataclass
class ClipEntry:
    """一条剪贴板历史记录"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content_type: str = "text"          # text | image | html
    content: str = ""                   # 文本内容 / 图片文件名
    preview: str = ""                   # 预览文本 (截断)
    timestamp: float = field(default_factory=time.time)
    pinned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClipEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── ClipStore 存储管理 ────────────────────────────────────────

class ClipStore:
    """管理剪贴板历史的增删改查与持久化"""

    def __init__(self, max_items: int = MAX_ITEMS):
        self.max_items = max_items
        self._entries: list[ClipEntry] = []
        self._ensure_dirs()
        self._load()

    # ── 公共 API ──────────────────────────────────────────────

    @property
    def entries(self) -> list[ClipEntry]:
        """返回所有条目 (置顶在前, 按时间倒序)"""
        pinned = [e for e in self._entries if e.pinned]
        normal = [e for e in self._entries if not e.pinned]
        pinned.sort(key=lambda e: e.timestamp, reverse=True)
        normal.sort(key=lambda e: e.timestamp, reverse=True)
        return pinned + normal

    def add(self, content_type: str, content: str, preview: str = "") -> Optional[ClipEntry]:
        """添加新条目, 自动去重和数量限制. 返回新条目或 None."""
        if not content or not content.strip():
            return None

        # 去重: 如果内容已存在, 移到顶部 (更新时间戳)
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
        """保存图片到磁盘并添加条目"""
        if not image_bytes:
            return None
        filename = f"{uuid.uuid4().hex[:12]}.{fmt}"
        filepath = IMAGES_DIR / filename
        filepath.write_bytes(image_bytes)

        entry = ClipEntry(
            content_type="image",
            content=filename,
            preview=f"[图片 {len(image_bytes)//1024}KB]",
            timestamp=time.time(),
        )
        self._entries.append(entry)
        self._enforce_limit()
        self._save()
        return entry

    def delete(self, entry_id: str) -> bool:
        """按 id 删除条目"""
        for i, e in enumerate(self._entries):
            if e.id == entry_id:
                removed = self._entries.pop(i)
                # 清理图片文件
                if removed.content_type == "image":
                    img_path = IMAGES_DIR / removed.content
                    if img_path.exists():
                        img_path.unlink()
                self._save()
                return True
        return False

    def toggle_pin(self, entry_id: str) -> bool:
        """切换置顶状态"""
        for e in self._entries:
            if e.id == entry_id:
                e.pinned = not e.pinned
                self._save()
                return True
        return False

    def clear(self, keep_pinned: bool = True):
        """清空历史 (默认保留置顶项)"""
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
        """搜索条目 (模糊匹配)"""
        if not query:
            return self.entries
        q = query.lower()
        results = [e for e in self.entries if q in e.preview.lower() or q in e.content.lower()]
        return results

    def get_image_path(self, entry: ClipEntry) -> Optional[Path]:
        """获取图片条目的完整路径"""
        if entry.content_type != "image":
            return None
        path = IMAGES_DIR / entry.content
        return path if path.exists() else None

    # ── 内部方法 ──────────────────────────────────────────────

    def _make_preview(self, content_type: str, content: str) -> str:
        if content_type == "image":
            return "[图片]"
        # 文本预览: 第一行, 截断到 80 字符
        line = content.split("\n")[0].strip()
        return line[:80] + ("…" if len(line) > 80 else "")

    def _enforce_limit(self):
        """限制非置顶条目数量"""
        normal = [e for e in self._entries if not e.pinned]
        if len(normal) > self.max_items:
            # 按时间升序排, 删除最旧的
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
            print(f"[WinVX] 保存历史失败: {e}")
