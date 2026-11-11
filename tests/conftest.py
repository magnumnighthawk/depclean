from pathlib import Path

import pytest


def make(root: Path, spec: dict) -> None:
    """Create files/folders from a nested dict. str values are file contents, dicts are folders."""
    for name, value in spec.items():
        p = root / name
        if isinstance(value, dict):
            p.mkdir(parents=True, exist_ok=True)
            make(p, value)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    make(tmp_path, {
        "webapp": {
            ".git": {"HEAD": "ref"},
            "package.json": "{}",
            "node_modules": {"left-pad": {"index.js": "x" * 5000, "node_modules": {"inner": {"a.js": "y"}}}},
            "packages": {"ui": {"package.json": "{}", "node_modules": {"react": {"i.js": "z" * 100}}}},
            ".next": {"cache": {"f": "c"}},
            "dist": {"bundle.js": "keep"},
        },
        "pyproj": {
            "pyproject.toml": "",
            "myenv": {"pyvenv.cfg": "home = /usr", "lib": {"site.py": "s"}},
            "venv-lookalike": {"lib": {"x.py": "no pyvenv.cfg here"}},
            "src": {"pkg": {"__pycache__": {"m.pyc": "b"}, "m.py": "code"}},
        },
        "rusty": {"Cargo.toml": "", "target": {"debug": {"bin": "b" * 2000}}},
        "not-rust": {"target": {"keep.txt": "k"}, "vendor": {"x": "y"}},
        "android": {"build.gradle": "", "build": {"o": "1"}, ".gradle": {"c": "2"}},
        "plain-build": {"build": {"keep": "1"}},
        "scripts": {"tool": {"__pycache__": {"t.pyc": "b"}}},
    })
    return tmp_path

