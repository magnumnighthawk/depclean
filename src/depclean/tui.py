"""Interactive curses interface."""

from __future__ import annotations

import curses
import locale
import os
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from . import deleter, fmt
from .scanner import Project, Target
from .sizing import Sizer

SORTS = ("size", "age", "name")
GB = 1024 ** 3
MB100 = 100 * 1024 ** 2


# --------------------------------------------------------------------------- model


@dataclass
class Row:
    project: Project
    target: Optional[Target] = None

    @property
    def key(self) -> str:
        return self.target.path if self.target else self.project.root


class Model:
    """Selection, sorting, filtering and expansion state. No curses in here, so it is testable."""

    def __init__(self, root: str, projects: List[Project]) -> None:
        self.root = root
        self.projects = projects
        self.selected: Set[str] = set()
        self.expanded: Set[str] = set()
        self.sort = "size"
        self.filter = ""
        self.cursor = 0
        self._cursor_key: Optional[str] = None

    # rows -------------------------------------------------------------
    def visible_projects(self) -> List[Project]:
        f = self.filter.lower()
        ps = [
            p for p in self.projects
            if not f or f in fmt.rel(p.root, self.root).lower() or f in p.summary().lower()
        ]
        if self.sort == "size":
            ps.sort(key=lambda p: (-p.freeable, p.root))
        elif self.sort == "age":
            ps.sort(key=lambda p: (p.mtime or 0, p.root))
        else:
            ps.sort(key=lambda p: p.root.lower())
        return ps

    def rows(self) -> List[Row]:
        out: List[Row] = []
        for p in self.visible_projects():
            out.append(Row(p))
            if p.root in self.expanded:
                out.extend(Row(p, t) for t in p.targets)
        # keep the cursor on the same item when rows move (sizes arriving, re-sorting)
        if self._cursor_key is not None:
            for i, r in enumerate(out):
                if r.key == self._cursor_key:
                    self.cursor = i
                    break
        self.cursor = max(0, min(self.cursor, len(out) - 1))
        self._cursor_key = out[self.cursor].key if out else None
        return out

    def move(self, delta: int, rows: List[Row]) -> None:
        if rows:
            self.cursor = max(0, min(self.cursor + delta, len(rows) - 1))
            self._cursor_key = rows[self.cursor].key

    # selection --------------------------------------------------------
    def state(self, p: Project) -> str:
        n = sum(1 for t in p.targets if t.path in self.selected)
        return "none" if n == 0 else "all" if n == len(p.targets) else "some"

    def toggle(self, row: Row) -> None:
        if row.target is not None:
            self.selected ^= {row.target.path}
            return
        paths = {t.path for t in row.project.targets}
        if self.state(row.project) == "all":
            self.selected -= paths
        else:
            self.selected |= paths

    def toggle_all(self) -> None:
        paths = {t.path for p in self.visible_projects() for t in p.targets}
        if paths and paths <= self.selected:
            self.selected -= paths
        else:
            self.selected |= paths

    def expand(self, row: Row, on: Optional[bool] = None) -> None:
        root = row.project.root
        want = (root not in self.expanded) if on is None else on
        if want:
            self.expanded.add(root)
        else:
            self.expanded.discard(root)
            self._cursor_key = root

    def selected_targets(self) -> List[Target]:
        return [t for p in self.projects for t in p.targets if t.path in self.selected]

    def selected_bytes(self) -> int:
        return sum(t.freeable or 0 for t in self.selected_targets())

    def total_bytes(self) -> int:
        return sum(p.freeable for p in self.projects)

    def remove(self, paths: Set[str]) -> None:
        for p in self.projects:
            p.targets = [t for t in p.targets if t.path not in paths]
        self.projects = [p for p in self.projects if p.targets]
        self.selected -= paths


# --------------------------------------------------------------------------- view

C_TITLE, C_ACCENT, C_OK, C_BIG, C_MED, C_ERR, C_BAR = range(1, 8)


class App:
    def __init__(self, scr: curses.window, model: Model, sizer: Optional[Sizer]) -> None:
        self.scr = scr
        self.m = model
        self.sizer = sizer
        self.message = ""
        self.message_until = 0.0
        self.mode = "normal"  # normal | filter | help
        self.top = 0
        self.tick = 0
        enc = (locale.getpreferredencoding(False) or "").lower()
        self.fancy = "utf" in enc
        g = self.fancy
        self.SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if g else "|/-\\"
        self.OPEN, self.CLOSED = ("▾", "▸") if g else ("v", ">")
        self.ELL = "…" if g else "~"
        self.HR = "─" if g else "-"
        self.DOT = "·" if g else "|"
        self.BLOCK, self.SHADE = ("█", "░") if g else ("#", ".")
        self.color = curses.has_colors() and "NO_COLOR" not in os.environ
        self._init_colors()

    def _init_colors(self) -> None:
        if not self.color:
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        curses.init_pair(C_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(C_ACCENT, curses.COLOR_CYAN, bg)
        curses.init_pair(C_OK, curses.COLOR_GREEN, bg)
        curses.init_pair(C_BIG, curses.COLOR_RED, bg)
        curses.init_pair(C_MED, curses.COLOR_YELLOW, bg)
        curses.init_pair(C_ERR, curses.COLOR_RED, bg)
        curses.init_pair(C_BAR, curses.COLOR_GREEN, bg)

    def attr(self, pair: int, extra: int = 0) -> int:
        return (curses.color_pair(pair) if self.color else 0) | extra

    # drawing helpers --------------------------------------------------
    def put(self, y: int, x: int, text: str, attr: int = 0, width: Optional[int] = None) -> int:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return x
        limit = (w - x) if width is None else min(width, w - x)
        if y == h - 1:
            limit = min(limit, w - x - 1)  # writing the bottom-right cell raises in curses
        text = text[: max(limit, 0)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass
        return x + len(text)

    def clip(self, s: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(s) <= width:
            return s.ljust(width)
        if width <= 3:
            return s[:width]
        keep = width - 1
        head = keep // 3
        return s[:head] + self.ELL + s[len(s) - (keep - head):]

    def flash(self, msg: str, seconds: float = 3.0) -> None:
        self.message = msg
        self.message_until = time.time() + seconds

    def size_attr(self, n: Optional[int]) -> int:
        if n is None:
            return curses.A_DIM
        if n >= GB:
            return self.attr(C_BIG, curses.A_BOLD)
        if n >= MB100:
            return self.attr(C_MED)
        return 0

    def pending(self) -> int:
        return self.sizer.pending if self.sizer else 0

    # main screen ------------------------------------------------------
    def draw(self, rows: List[Row]) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 8 or w < 50:
            self.put(0, 0, "Terminal too small for depclean. Enlarge it or press q.")
            scr.refresh()
            return

        # line 0: title bar
        title = " depclean "
        x = self.put(0, 0, title, self.attr(C_TITLE, curses.A_BOLD))
        self.put(0, x + 1, fmt.home_short(self.m.root), curses.A_BOLD, w - x - 12)
        self.put(0, w - 9, "? help", curses.A_DIM)

        # line 1: totals
        n_sel = len(self.m.selected)
        parts = [
            (f"{len(self.m.projects)} project{'s' if len(self.m.projects) != 1 else ''}", 0),
            (f" {self.DOT} ", curses.A_DIM),
            (fmt.size(self.m.total_bytes()), curses.A_BOLD),
            (" reclaimable", 0),
            (f" {self.DOT} ", curses.A_DIM),
        ]
        if n_sel:
            parts += [(f"{n_sel} selected ", self.attr(C_OK)),
                      (f"({fmt.size(self.m.selected_bytes())})", self.attr(C_OK, curses.A_BOLD))]
        else:
            parts += [("nothing selected", curses.A_DIM)]
        x = 1
        for text, a in parts:
            x = self.put(1, x, text, a)
        p = self.pending()
        if p:
            spin = self.SPIN[self.tick % len(self.SPIN)]
            msg = f"{spin} sizing {p} folder{'s' if p != 1 else ''}"
            self.put(1, w - len(msg) - 2, msg, self.attr(C_ACCENT))

        # line 2: column headers
        size_w, age_w = 9, 9
        name_w = int((w - 8 - size_w - age_w - 2) * 0.55)
        what_w = w - 8 - size_w - age_w - 2 - name_w
        up, down = ("↑", "↓") if self.fancy else ("^", "v")
        sort = self.m.sort
        hdr = ("      " + self.clip("PROJECT" + (f" {up}" if sort == "name" else ""), name_w)
               + "  " + self.clip("FOLDERS", what_w)
               + ("SIZE" + (f" {down}" if sort == "size" else "")).rjust(size_w)
               + ("OLDEST " + up if sort == "age" else "MODIFIED").rjust(age_w))
        self.put(2, 0, hdr, curses.A_DIM | curses.A_UNDERLINE)

        # list
        list_top, list_h = 3, h - 5
        if self.m.cursor < self.top:
            self.top = self.m.cursor
        elif self.m.cursor >= self.top + list_h:
            self.top = self.m.cursor - list_h + 1
        self.top = max(0, min(self.top, max(len(rows) - list_h, 0)))

        if not rows:
            msg = "No matches for filter." if self.m.filter else "Nothing to clean up here."
            self.put(list_top + 1, 4, msg, curses.A_DIM)

        for i, row in enumerate(rows[self.top: self.top + list_h]):
            y = list_top + i
            idx = self.top + i
            cur = idx == self.m.cursor
            base = curses.A_REVERSE if cur else 0
            if cur:
                self.put(y, 0, " " * w, base)
            if row.target is None:
                p = row.project
                st = self.m.state(p)
                box = {"all": "[x]", "none": "[ ]", "some": "[-]"}[st]
                arrow = self.OPEN if p.root in self.m.expanded else self.CLOSED
                x = self.put(y, 0, f"{arrow} ", base | curses.A_DIM)
                x = self.put(y, x, box + " ", base | (self.attr(C_OK, curses.A_BOLD) if st != "none" else 0))
                x = self.put(y, x, self.clip(fmt.rel(p.root, self.m.root), name_w), base | curses.A_BOLD)
                x = self.put(y, x, "  " + self.clip(p.summary(), what_w), base | curses.A_DIM)
                if p.sized:
                    size_txt, sa = fmt.size(p.freeable), self.size_attr(p.freeable)
                elif p.freeable:
                    size_txt, sa = fmt.size(p.freeable) + "+", self.size_attr(p.freeable)
                else:
                    size_txt, sa = self.ELL, curses.A_DIM
                x = self.put(y, x, size_txt.rjust(size_w), base | sa)
                self.put(y, x, fmt.age(p.mtime).rjust(age_w), base | curses.A_DIM)
            else:
                t = row.target
                on = t.path in self.m.selected
                x = self.put(y, 0, "      ", base)
                x = self.put(y, x, ("[x] " if on else "[ ] "), base | (self.attr(C_OK, curses.A_BOLD) if on else 0))
                x = self.put(y, x, self.clip(t.label, name_w - 4), base)
                what = t.kind
                if t.linked:
                    what += f" {self.DOT} {fmt.size(t.linked)} hardlinked, not freed"
                x = self.put(y, x, "  " + self.clip(what, what_w), base | curses.A_DIM)
                self.put(y, x, fmt.size(t.freeable).rjust(size_w), base | self.size_attr(t.freeable))

        # scroll hint
        if len(rows) > list_h:
            info = f" {self.top + 1}-{min(self.top + list_h, len(rows))}/{len(rows)} "
            self.put(h - 2, w - len(info) - 1, info, curses.A_DIM)

        # message / filter line
        if self.mode == "filter":
            x = self.put(h - 2, 1, "/", self.attr(C_ACCENT, curses.A_BOLD))
            x = self.put(h - 2, x, self.m.filter)
            self.put(h - 2, x, "_" if not self.fancy else "▏", curses.A_BLINK)
            self.put(h - 2, x + 3, "enter keep · esc clear", curses.A_DIM)
        elif self.message and time.time() < self.message_until:
            self.put(h - 2, 1, self.message, self.attr(C_ACCENT))
        elif self.m.filter:
            self.put(h - 2, 1, f"filter: {self.m.filter}  (/ to change, esc to clear)", curses.A_DIM)

        # key hints
        keys = [("↑↓" if self.fancy else "j/k", "move"), ("space", "select"), ("a", "all"),
                ("enter", "expand"), ("s", f"sort:{self.m.sort}"), ("/", "filter"),
                ("d", "delete"), ("q", "quit")]
        x = 1
        for k, label in keys:
            if x + len(k) + len(label) + 3 >= w:
                break
            x = self.put(h - 1, x, k, self.attr(C_ACCENT, curses.A_BOLD))
            x = self.put(h - 1, x, f" {label}  ", curses.A_DIM)
        scr.refresh()

    def draw_help(self) -> None:
        self.scr.erase()
        lines = [
            ("depclean keys", curses.A_BOLD),
            ("", 0),
            ("  ↑ ↓  j k        move                 PgUp PgDn  g G   page / top / bottom", 0),
            ("  space           select project or folder", 0),
            ("  a               select / clear everything shown", 0),
            ("  enter → l       expand a project to pick single folders", 0),
            ("  ← h             collapse", 0),
            ("  s               sort by size, age (oldest first) or name", 0),
            ("  /               filter by path or folder name", 0),
            ("  d               delete selected (asks first)", 0),
            ("  q  esc          quit", 0),
            ("", 0),
            ("Sizes are disk usage. Files hard-linked elsewhere (pnpm store) are not", curses.A_DIM),
            ("counted as reclaimable. MODIFIED is the newest change to the project's", curses.A_DIM),
            ("own files, ignoring dependency folders.", curses.A_DIM),
            ("", 0),
            ("press any key", curses.A_DIM),
        ]
        for i, (text, a) in enumerate(lines):
            self.put(1 + i, 2, text, a)
        self.scr.refresh()

    # actions ----------------------------------------------------------
    def confirm_delete(self) -> bool:
        targets = sorted(self.m.selected_targets(), key=lambda t: -(t.freeable or 0))
        self.scr.timeout(-1)
        try:
            while True:
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                total = sum(t.freeable or 0 for t in targets)
                unsized = any(t.size is None for t in targets)
                head = f"Delete {len(targets)} folder{'s' if len(targets) != 1 else ''}"
                x = self.put(1, 2, head, curses.A_BOLD)
                self.put(1, x, f"  {self.DOT}  frees about {fmt.size(total)}{'+' if unsized else ''}",
                         self.attr(C_OK, curses.A_BOLD))
                room = max(h - 7, 1)
                shown = targets if len(targets) <= room else targets[: room - 1]
                for i, t in enumerate(shown):
                    self.put(3 + i, 4, fmt.size(t.freeable).rjust(9), self.size_attr(t.freeable))
                    self.put(3 + i, 15, self.clip(fmt.rel(t.path, self.m.root), w - 17))
                if len(shown) < len(targets):
                    self.put(3 + len(shown), 15, f"{self.ELL} and {len(targets) - len(shown)} more", curses.A_DIM)
                self.put(h - 2, 2, "This permanently deletes them (not to Trash). ", self.attr(C_ERR, curses.A_BOLD))
                self.put(h - 1, 2, "Press y to delete, any other key to go back.", curses.A_BOLD)
                self.scr.refresh()
                ch = self.scr.getch()
                if ch == curses.KEY_RESIZE:
                    continue
                return ch in (ord("y"), ord("Y"))
        finally:
            self.scr.timeout(120)

    def run_delete(self) -> None:
        targets = self.m.selected_targets()
        root = self.m.root
        try:
            free_before = shutil.disk_usage(root).free
        except OSError:
            free_before = None
        state: Dict[str, object] = {"i": 0, "current": "", "cancel": False}
        results: List[Tuple[Target, Optional[str]]] = []

        def work() -> None:
            for i, t in enumerate(targets):
                if state["cancel"]:
                    break
                state["i"], state["current"] = i, t.path
                results.append((t, deleter.delete(t.path, root)))
            state["i"] = len(targets)

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            self.tick += 1
            self.scr.erase()
            h, w = self.scr.getmaxyx()
            done = int(state["i"])  # type: ignore[arg-type]
            self.put(1, 2, f"{self.SPIN[self.tick % len(self.SPIN)]} Deleting {done + 1}/{len(targets)}",
                     curses.A_BOLD)
            self.put(2, 4, self.clip(fmt.rel(str(state["current"]), root), w - 6), curses.A_DIM)
            bar_w = max(w - 8, 10)
            filled = int(bar_w * done / max(len(targets), 1))
            self.put(4, 4, self.BLOCK * filled, self.attr(C_BAR))
            self.put(4, 4 + filled, self.SHADE * (bar_w - filled), curses.A_DIM)
            self.put(h - 1, 2, "q stops after the current folder" if not state["cancel"] else "stopping…",
                     curses.A_DIM)
            self.scr.refresh()
            ch = self.read_key()
            if ch in (ord("q"), 27):
                state["cancel"] = True
        th.join()

        ok = [t for t, err in results if err is None]
        failed = [(t, err) for t, err in results if err is not None]
        freed = sum(t.freeable or 0 for t in ok)
        try:
            free_after = shutil.disk_usage(root).free
        except OSError:
            free_after = None
        self.m.remove({t.path for t in ok})

        self.scr.timeout(-1)
        try:
            self.scr.erase()
            h, w = self.scr.getmaxyx()
            x = self.put(1, 2, f"Deleted {len(ok)} of {len(targets)} folders", curses.A_BOLD)
            self.put(1, x, f"  {self.DOT}  about {fmt.size(freed)} freed", self.attr(C_OK, curses.A_BOLD))
            if free_before is not None and free_after is not None:
                self.put(2, 2, f"Free disk space: {fmt.size(free_before)} → {fmt.size(free_after)}"
                         if self.fancy else f"Free disk space: {fmt.size(free_before)} -> {fmt.size(free_after)}",
                         curses.A_DIM)
            y = 4
            if failed:
                label = f"{len(failed)} problem{'s' if len(failed) != 1 else ''}:"
                self.put(y, 2, label, self.attr(C_ERR, curses.A_BOLD))
                y += 1
                for t, err in failed[: max(h - y - 3, 1)]:
                    self.put(y, 4, self.clip(f"{fmt.rel(t.path, root)}: {err}", w - 6), self.attr(C_ERR))
                    y += 1
            self.put(h - 1, 2, "press any key", curses.A_DIM)
            self.scr.refresh()
            self.scr.getch()
        finally:
            self.scr.timeout(120)

    # loop -------------------------------------------------------------
    def run(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.scr.keypad(True)
        self.scr.timeout(120)
        while True:
            self.tick += 1
            rows = self.m.rows()
            if self.mode == "help":
                self.draw_help()
            else:
                self.draw(rows)
            ch = self.read_key()
            if ch == -1 or ch == curses.KEY_RESIZE:
                continue
            if self.mode == "help":
                self.mode = "normal"
                continue
            if self.mode == "filter":
                self.on_filter_key(ch)
                continue
            if self.on_key(ch, rows) is False:
                return

    _ESC_SEQ = {"[A": curses.KEY_UP, "[B": curses.KEY_DOWN, "[C": curses.KEY_RIGHT, "[D": curses.KEY_LEFT,
                "OA": curses.KEY_UP, "OB": curses.KEY_DOWN, "OC": curses.KEY_RIGHT, "OD": curses.KEY_LEFT,
                "[H": curses.KEY_HOME, "[F": curses.KEY_END, "OH": curses.KEY_HOME, "OF": curses.KEY_END,
                "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE, "[1~": curses.KEY_HOME, "[4~": curses.KEY_END}

    def read_key(self) -> int:
        """getch(), but decode escape sequences curses didn't recognise so they never read as a lone ESC."""
        ch = self.scr.getch()
        if ch != 27:
            return ch
        self.scr.nodelay(True)
        seq = ""
        try:
            while len(seq) < 6:
                nxt = self.scr.getch()
                if nxt == -1:
                    break
                seq += chr(nxt)
                if seq in self._ESC_SEQ or (len(seq) > 1 and seq[-1].isalpha() or seq.endswith("~")):
                    break
        finally:
            self.scr.nodelay(False)
            self.scr.timeout(120)
        if not seq:
            return 27
        return self._ESC_SEQ.get(seq, -1)  # unknown sequences (alt+key, mouse...) are ignored

    def on_filter_key(self, ch: int) -> None:
        if ch in (10, 13, curses.KEY_ENTER):
            self.mode = "normal"
        elif ch == 27:
            self.m.filter = ""
            self.mode = "normal"
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            self.m.filter = self.m.filter[:-1]
        elif 32 <= ch < 127:
            self.m.filter += chr(ch)
        self.m.cursor = 0
        self.m._cursor_key = None

    def on_key(self, ch: int, rows: List[Row]) -> Optional[bool]:
        m = self.m
        h, _ = self.scr.getmaxyx()
        page = max(h - 6, 1)
        row = rows[m.cursor] if rows else None
        if ch in (ord("q"), 27):
            if ch == 27 and m.filter:
                m.filter = ""
                return None
            return False
        if ch in (curses.KEY_UP, ord("k")):
            m.move(-1, rows)
        elif ch in (curses.KEY_DOWN, ord("j")):
            m.move(1, rows)
        elif ch == curses.KEY_PPAGE:
            m.move(-page, rows)
        elif ch == curses.KEY_NPAGE:
            m.move(page, rows)
        elif ch in (curses.KEY_HOME, ord("g")):
            m.move(-len(rows), rows)
        elif ch in (curses.KEY_END, ord("G")):
            m.move(len(rows), rows)
        elif ch == ord(" ") and row:
            m.toggle(row)
            m.move(1, rows)
        elif ch == ord("a"):
            m.toggle_all()
        elif ch in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT, ord("l")) and row:
            if row.target is None:
                m.expand(row, None if ch in (10, 13, curses.KEY_ENTER) else True)
        elif ch in (curses.KEY_LEFT, ord("h")) and row:
            m.expand(row, False)
        elif ch == ord("s"):
            m.sort = SORTS[(SORTS.index(m.sort) + 1) % len(SORTS)]
            self.flash(f"sorted by {m.sort}" + (" (oldest first)" if m.sort == "age" else ""))
        elif ch == ord("/"):
            self.mode = "filter"
        elif ch == ord("?"):
            self.mode = "help"
        elif ch == ord("d"):
            if not m.selected:
                self.flash("Nothing selected. Press space to select a project.")
            elif self.confirm_delete():
                self.run_delete()
            else:
                self.flash("Cancelled, nothing deleted.")
        return None


def run(root: str, projects: List[Project], sizer: Optional[Sizer]) -> None:
    os.environ.setdefault("ESCDELAY", "25")
    locale.setlocale(locale.LC_ALL, "")
    model = Model(root, projects)
    curses.wrapper(lambda scr: App(scr, model, sizer).run())
