"""
session_helper.py — 会话类型检测
检测当前运行在 X11 还是 Wayland 会话
"""

import os
import shutil


def get_session_type() -> str:
    """返回当前会话类型: 'wayland' / 'x11' / 'unknown'"""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session in ("wayland", "x11"):
        return session
    # 备用检测
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def is_wayland() -> bool:
    return get_session_type() == "wayland"


def is_x11() -> bool:
    return get_session_type() == "x11"


def has_ydotool() -> bool:
    """检查 ydotool 是否可用 (Wayland 下模拟按键)"""
    return shutil.which("ydotool") is not None


def has_wl_paste() -> bool:
    """检查 wl-paste 是否可用"""
    return shutil.which("wl-paste") is not None
