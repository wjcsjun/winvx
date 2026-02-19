"""
session_helper.py — Session Type Detection
Detect whether running in X11 or Wayland session
"""

import os
import shutil


def get_session_type() -> str:
    """Returns current session type: 'wayland' / 'x11' / 'unknown'"""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session in ("wayland", "x11"):
        return session
    # Fallback detection
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
    """Check if ydotool is available (simulating key presses under Wayland)"""
    return shutil.which("ydotool") is not None


def has_wl_paste() -> bool:
    """Check if wl-paste is available"""
    return shutil.which("wl-paste") is not None
