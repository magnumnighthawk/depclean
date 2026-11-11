"""Which folders depclean considers safe to remove.

Every rule is conservative: a folder is only matched when its name and a
marker file prove it is a dependency/cache folder that a package manager or
build tool can recreate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AbstractSet, Optional, Tuple


@dataclass(frozen=True)
class Rule:
    kind: str
    """Short label shown in the UI, e.g. ``"node"``."""

    names: Tuple[str, ...] = ()
    """Folder names this rule applies to. Empty means any name."""

    sibling_markers: Tuple[str, ...] = ()
    """If set, at least one of these must exist next to the folder."""

    inner_markers: Tuple[str, ...] = ()
    """If set, at least one of these must exist inside the folder."""

    reinstall: str = ""
    """Hint for how to get the folder back."""


RULES: Tuple[Rule, ...] = (
    Rule("node", ("node_modules",), reinstall="npm/pnpm/yarn/bun install"),
    Rule("python venv", (), inner_markers=("pyvenv.cfg",),
         reinstall="recreate the venv, then pip/uv/poetry install"),
    Rule("python cache",
         ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox"),
         reinstall="recreated automatically"),
    Rule("build cache",
         (".next", ".nuxt", ".turbo", ".parcel-cache", ".svelte-kit", ".angular"),
         reinstall="recreated on next build/dev run"),
    Rule("rust", ("target",), sibling_markers=("Cargo.toml",), reinstall="cargo build"),
    Rule("cocoapods", ("Pods",), sibling_markers=("Podfile",), reinstall="pod install"),
    Rule("gradle", (".gradle", "build"),
         sibling_markers=("build.gradle", "build.gradle.kts",
                          "settings.gradle", "settings.gradle.kts"),
         reinstall="gradle build"),
)

# Folders that are never searched or removed.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".Trash", ".Trashes"})

# OS metadata files that change without you touching the project; ignored for "last modified".
MTIME_IGNORE = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".localized"})

# Files/folders that mark the root of a project, used to group results.
PROJECT_MARKERS = frozenset({
    "package.json", "deno.json", "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "Pipfile", "Cargo.toml", "go.mod", "Gemfile",
    "composer.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "Podfile", "pubspec.yaml",
})

_NAMED = {name: rule for rule in RULES for name in rule.names}
_ANY_NAME = tuple(rule for rule in RULES if not rule.names)


def _accepts(rule: Rule, path: str, sibling_names: AbstractSet[str]) -> bool:
    if rule.sibling_markers and not any(m in sibling_names for m in rule.sibling_markers):
        return False
    if rule.inner_markers and not any(
        os.path.isfile(os.path.join(path, m)) for m in rule.inner_markers
    ):
        return False
    return True


def match(path: str, sibling_names: AbstractSet[str]) -> Optional[Rule]:
    """Return the rule matching the directory at *path*, if any.

    *sibling_names* is the set of entry names in the parent directory
    (the caller already has it from listing the parent).
    """
    name = os.path.basename(path)
    if name in SKIP_DIRS:
        return None
    rule = _NAMED.get(name)
    if rule is not None and _accepts(rule, path, sibling_names):
        return rule
    for rule in _ANY_NAME:
        if _accepts(rule, path, sibling_names):
            return rule
    return None


def match_path(path: str) -> Optional[Rule]:
    """Like :func:`match` but lists the parent itself. Used to re-verify before deleting."""
    try:
        siblings = set(os.listdir(os.path.dirname(path)))
    except OSError:
        return None
    return match(path, siblings)
