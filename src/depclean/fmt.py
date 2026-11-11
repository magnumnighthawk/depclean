"""Small formatting helpers shared by the list output and the TUI."""

from __future__ import annotations

import os
import time
from typing import Optional


def size(n: Optional[int]) -> str:
    if n is None:
        return "…"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
        value /= 1024
    return f"{n} B"  # unreachable


def age(mtime: Optional[float], now: Optional[float] = None) -> str:
    if not mtime:
        return "-"
    seconds = max((now if now is not None else time.time()) - mtime, 0)
    days = seconds / 86400
    if days < 1:
        return "today"
    if days < 30:
        return f"{int(days)}d"
    if days < 365:
        return f"{int(days // 30)}mo"
    return f"{days / 365:.1f}y".replace(".0y", "y")


def home_short(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def rel(path: str, root: str) -> str:
    r = os.path.relpath(path, root)
    return "." if r == "." else r
