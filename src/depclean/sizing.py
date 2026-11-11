"""Measure folder sizes in the background."""

from __future__ import annotations

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional, Set, Tuple

from .scanner import Target


class Cancelled(Exception):
    pass


def _usage(st: os.stat_result) -> int:
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


def dir_size(path: str, stop: Optional[threading.Event] = None) -> Tuple[int, int]:
    """Return ``(disk_usage, hardlinked_bytes)`` for everything under *path*.

    Hard-linked files are counted once. Their bytes are also reported
    separately because deleting one link does not free the space while
    another link (such as pnpm's content store) still exists.
    """
    total = 0
    linked = 0
    seen: Set[Tuple[int, int]] = set()
    try:
        total += _usage(os.lstat(path))
    except OSError:
        return 0, 0
    stack = [path]
    while stack:
        if stop is not None and stop.is_set():
            raise Cancelled()
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    total += _usage(st)
                    stack.append(entry.path)
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    linked += _usage(st)
                total += _usage(st)
    return total, linked


class Sizer:
    """Size targets on a thread pool. Results are written onto each Target."""

    def __init__(self, workers: Optional[int] = None) -> None:
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=workers or min(8, (os.cpu_count() or 4) * 2))
        self._lock = threading.Lock()
        self.pending = 0

    def submit(self, targets: Iterable[Target], on_done: Optional[Callable[[Target], None]] = None) -> None:
        for t in targets:
            with self._lock:
                self.pending += 1
            self._pool.submit(self._run, t, on_done)

    def _run(self, t: Target, on_done: Optional[Callable[[Target], None]]) -> None:
        try:
            t.size, t.linked = dir_size(t.path, self._stop)
            if on_done is not None:
                on_done(t)
        except Cancelled:
            pass
        finally:
            with self._lock:
                self.pending -= 1

    def wait(self) -> None:
        self._pool.shutdown(wait=True)

    def close(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
