"""Find removable folders under a root and group them by project."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import rules


@dataclass
class Target:
    """One removable folder."""

    path: str
    kind: str
    project_root: str = ""
    size: Optional[int] = None
    """Disk usage in bytes once measured (hardlinked inodes counted once)."""
    linked: int = 0
    """Bytes in files that have other hard links (e.g. pnpm's store); not freed by deleting."""

    @property
    def freeable(self) -> Optional[int]:
        return None if self.size is None else max(self.size - self.linked, 0)

    @property
    def label(self) -> str:
        rel = os.path.relpath(self.path, self.project_root) if self.project_root else self.path
        return rel


@dataclass
class Project:
    root: str
    targets: List[Target] = field(default_factory=list)
    mtime: Optional[float] = None
    """Newest modification time of the project's own files (dependency folders excluded)."""

    @property
    def sized(self) -> bool:
        return all(t.size is not None for t in self.targets)

    @property
    def freeable(self) -> int:
        return sum(t.freeable or 0 for t in self.targets)

    def summary(self) -> str:
        counts: Dict[str, int] = {}
        for t in self.targets:
            name = os.path.basename(t.path)
            counts[name] = counts.get(name, 0) + 1
        parts = [f"{n} ×{c}" if c > 1 else n for n, c in counts.items()]
        return ", ".join(parts)


def _excluded(path: str, name: str, patterns: Tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def scan(
    root: str,
    max_depth: int = 6,
    exclude: Iterable[str] = (),
    on_progress: Optional[Callable[[int], None]] = None,
) -> List[Project]:
    """Walk *root* and return projects that have removable folders.

    Matched folders are not descended into, symlinks are never followed and
    version-control folders are skipped. *max_depth* limits how many folder
    levels below *root* are searched.
    """
    root = os.path.abspath(root)
    patterns = tuple(exclude)
    markers: Dict[str, Tuple[bool, bool]] = {}  # dir -> (has .git, has manifest)
    newest: Dict[str, float] = {}  # dir -> newest mtime of its own entries
    found: List[Target] = []

    stack: List[Tuple[str, int]] = [(root, 0)]
    visited = 0
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            continue
        visited += 1
        if on_progress is not None and visited % 250 == 0:
            on_progress(visited)

        names = {e.name for e in entries}
        markers[directory] = (".git" in names, not names.isdisjoint(rules.PROJECT_MARKERS))
        latest = 0.0
        for entry in entries:
            if patterns and _excluded(entry.path, entry.name, patterns):
                continue
            if entry.name in rules.SKIP_DIRS:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                rule = rules.match(entry.path, names)
                if rule is not None:
                    # dependency folders don't count towards "last modified"
                    found.append(Target(entry.path, rule.kind))
                    continue
                if depth < max_depth:
                    stack.append((entry.path, depth + 1))
            if entry.name in rules.MTIME_IGNORE:
                continue
            try:
                latest = max(latest, entry.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
        newest[directory] = latest

    if on_progress is not None:
        on_progress(visited)
    return _group(root, found, markers, newest)


def _project_root(target_parent: str, root: str, markers: Dict[str, Tuple[bool, bool]]) -> str:
    """Nearest enclosing git repo, else nearest manifest, else the top-level folder under *root*."""
    nearest_manifest: Optional[str] = None
    d = target_parent
    while True:
        has_git, has_manifest = markers.get(d, (False, False))
        if has_git:
            return d
        if has_manifest and nearest_manifest is None:
            nearest_manifest = d
        if d == root:
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if nearest_manifest is not None:
        return nearest_manifest
    if target_parent == root:
        return root
    rel = os.path.relpath(target_parent, root)
    return os.path.join(root, rel.split(os.sep)[0])


def _group(
    root: str,
    found: List[Target],
    markers: Dict[str, Tuple[bool, bool]],
    newest: Dict[str, float],
) -> List[Project]:
    projects: Dict[str, Project] = {}
    for t in found:
        proot = _project_root(os.path.dirname(t.path), root, markers)
        t.project_root = proot
        projects.setdefault(proot, Project(proot)).targets.append(t)

    # Attribute each folder's newest mtime to the nearest project that contains it.
    owner_cache: Dict[str, Optional[str]] = {}

    def owner(d: str) -> Optional[str]:
        chain = []
        result: Optional[str] = None
        while True:
            if d in owner_cache:
                result = owner_cache[d]
                break
            chain.append(d)
            if d in projects:
                result = d
                break
            if d == root:
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        for c in chain:
            owner_cache[c] = result
        return result

    for d, mtime in newest.items():
        p = owner(d)
        if p is not None and mtime:
            proj = projects[p]
            proj.mtime = mtime if proj.mtime is None else max(proj.mtime, mtime)

    for proj in projects.values():
        proj.targets.sort(key=lambda t: t.path)
    return sorted(projects.values(), key=lambda p: p.root)
