"""Delete folders, with safety checks right before each removal."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from typing import List, Optional

from . import rules


class UnsafeError(Exception):
    """Raised when a path fails a safety check and is left alone."""


def is_protected_root(path: str) -> bool:
    """True for ``/`` and the user's home folder, which need ``--force-root``."""
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    return real == home or os.path.dirname(real) == real


def check(path: str, root: str) -> None:
    """Raise :class:`UnsafeError` unless *path* is a real, still-matching folder inside *root*."""
    if os.path.islink(path):
        raise UnsafeError("is a symlink")
    if not os.path.isdir(path):
        raise UnsafeError("no longer exists")
    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if real == real_root:
        raise UnsafeError("is the scan root")
    try:
        inside = os.path.commonpath([real, real_root]) == real_root
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        raise UnsafeError("is outside the scanned folder")
    if rules.match_path(path) is None:
        raise UnsafeError("no longer matches a cleanup rule")


def delete(path: str, root: str) -> Optional[str]:
    """Delete *path*. Returns ``None`` on success, otherwise an error message."""
    try:
        check(path, root)
    except UnsafeError as e:
        return f"skipped: {e}"

    errors: List[str] = []

    def retry_writable(func, p, exc):  # noqa: ANN001
        # Read-only files (or folders) are common in caches; make writable and retry once.
        try:
            parent = os.path.dirname(p)
            if parent.startswith(path + os.sep) or parent == path:  # never touch folders outside the target
                os.chmod(parent, stat.S_IRWXU)
            if p != path and not os.path.islink(p):
                os.chmod(p, stat.S_IRWXU)
            func(p)
        except OSError as e2:
            errors.append(f"{p}: {e2.strerror or e2}")

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry_writable)
        else:
            shutil.rmtree(path, onerror=lambda f, p, ei: retry_writable(f, p, ei[1]))
    except OSError as e:
        errors.append(str(e))

    if errors:
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return errors[0] + more
    return None
