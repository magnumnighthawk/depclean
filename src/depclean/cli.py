"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional, Sequence

from . import __version__, deleter, fmt
from .rules import RULES
from .scanner import Project, scan
from .sizing import Sizer


def _parser() -> argparse.ArgumentParser:
    kinds = "\n".join(
        f"  {r.kind:<13} {', '.join(r.names) or 'any folder'}"
        + (f"  (next to {' / '.join(r.sibling_markers)})" if r.sibling_markers else "")
        + (f"  (containing {' / '.join(r.inner_markers)})" if r.inner_markers else "")
        for r in RULES
    )
    p = argparse.ArgumentParser(
        prog="depclean",
        description="Find and remove dependency and cache folders (node_modules, Python venvs, "
                    "build caches...) in projects you are not working on.",
        epilog=f"Folders it looks for:\n{kinds}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", default=".", help="folder to scan (default: current folder)")
    p.add_argument("-l", "--list", action="store_true", help="print a report and exit; never deletes")
    p.add_argument("--json", action="store_true", help="print the report as JSON (implies --list)")
    p.add_argument("--no-size", action="store_true", help="skip measuring folder sizes (faster)")
    p.add_argument("-d", "--depth", type=int, default=6, metavar="N",
                   help="how many folder levels below PATH to search (default: 6)")
    p.add_argument("-x", "--exclude", action="append", default=[], metavar="GLOB",
                   help="skip paths or folder names matching GLOB (repeatable)")
    p.add_argument("--force-root", action="store_true",
                   help="allow the interactive mode on / or your home folder")
    p.add_argument("-V", "--version", action="version", version=f"depclean {__version__}")
    return p


class _Progress:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str) -> None:
        if self.enabled:
            sys.stderr.write("\r\033[K" + text)
            sys.stderr.flush()

    def done(self) -> None:
        if self.enabled:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def _size_all(projects: List[Project], progress: _Progress) -> None:
    targets = [t for p in projects for t in p.targets]
    sizer = Sizer()
    sizer.submit(targets)
    while sizer.pending:
        progress(f"Measuring sizes… {len(targets) - sizer.pending}/{len(targets)}")
        time.sleep(0.1)
    sizer.wait()


def _report(projects: List[Project], root: str, sized: bool, color: bool) -> str:
    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    projects = sorted(projects, key=lambda p: (-p.freeable, p.root))
    lines = []
    for p in projects:
        size = fmt.size(p.freeable) if sized else ""
        lines.append(f"{c('1', fmt.rel(p.root, root))}  {c('2', fmt.age(p.mtime))}  {c('33;1', size)}".rstrip())
        for t in p.targets:
            extra = f"  ({fmt.size(t.linked)} hardlinked, not freed)" if t.linked else ""
            size = fmt.size(t.freeable).rjust(9) if sized else ""
            lines.append(f"  {size}  {t.label}  {c('2', t.kind + extra)}")
    n = sum(len(p.targets) for p in projects)
    total = sum(p.freeable for p in projects)
    lines.append("")
    summary = f"{len(projects)} project{'s' * (len(projects) != 1)}, {n} folder{'s' * (n != 1)}"
    if sized:
        summary += f", {fmt.size(total)} reclaimable"
    lines.append(c("1", summary))
    return "\n".join(lines)


def _json(projects: List[Project], root: str) -> str:
    return json.dumps({
        "root": root,
        "reclaimable_bytes": sum(p.freeable for p in projects),
        "projects": [
            {
                "path": p.root,
                "last_modified": p.mtime,
                "reclaimable_bytes": p.freeable,
                "folders": [
                    {"path": t.path, "kind": t.kind, "size_bytes": t.size,
                     "hardlinked_bytes": t.linked, "reclaimable_bytes": t.freeable}
                    for t in p.targets
                ],
            }
            for p in sorted(projects, key=lambda p: (-p.freeable, p.root))
        ],
    }, indent=2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isdir(root):
        print(f"depclean: not a folder: {args.path}", file=sys.stderr)
        return 2
    list_mode = args.list or args.json
    interactive = not list_mode
    if interactive and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("depclean: interactive mode needs a terminal; use --list for a report", file=sys.stderr)
        return 2
    if interactive and deleter.is_protected_root(root) and not args.force_root:
        print(f"depclean: refusing to run interactively on {fmt.home_short(root)} "
              "(pass a project folder, or --force-root if you really mean it)", file=sys.stderr)
        return 2

    progress = _Progress(sys.stderr.isatty())
    progress("Scanning…")
    try:
        projects = scan(root, args.depth, args.exclude,
                        on_progress=lambda n: progress(f"Scanning… {n} folders"))
    except KeyboardInterrupt:
        progress.done()
        return 130
    progress.done()

    if list_mode:
        if not args.no_size:
            try:
                _size_all(projects, progress)
            except KeyboardInterrupt:
                progress.done()
                return 130
            progress.done()
        if args.json:
            print(_json(projects, root))
        elif not projects:
            print("Nothing to clean up.")
        else:
            color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
            print(_report(projects, root, not args.no_size, color))
        return 0

    if not projects:
        print(f"Nothing to clean up in {fmt.home_short(root)}.")
        return 0

    from . import tui  # imported late so --list works without curses

    sizer = None
    if not args.no_size:
        sizer = Sizer()
        sizer.submit(t for p in projects for t in p.targets)
    try:
        tui.run(root, projects, sizer)
    except KeyboardInterrupt:
        pass
    finally:
        if sizer is not None:
            sizer.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
