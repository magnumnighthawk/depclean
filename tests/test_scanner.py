import os
from pathlib import Path

from depclean.scanner import scan


def found(root: Path, **kw):
    projects = scan(str(root), **kw)
    return {
        os.path.relpath(t.path, root): (os.path.relpath(p.root, root), t.kind)
        for p in projects for t in p.targets
    }


def test_detects_expected_folders(tree: Path):
    f = found(tree)
    assert f == {
        "webapp/node_modules": ("webapp", "node"),
        "webapp/packages/ui/node_modules": ("webapp", "node"),
        "webapp/.next": ("webapp", "build cache"),
        "pyproj/myenv": ("pyproj", "python venv"),
        "pyproj/src/pkg/__pycache__": ("pyproj", "python cache"),
        "rusty/target": ("rusty", "rust"),
        "android/build": ("android", "gradle"),
        "android/.gradle": ("android", "gradle"),
        "scripts/tool/__pycache__": ("scripts", "python cache"),
    }


def test_never_matches_unproven_folders(tree: Path):
    f = found(tree)
    for keep in ("webapp/dist", "not-rust/target", "not-rust/vendor", "plain-build/build",
                 "pyproj/venv-lookalike", "webapp/.git"):
        assert keep not in f


def test_does_not_descend_into_matches(tree: Path):
    assert not any("left-pad" in k for k in found(tree))


def test_symlinks_not_followed(tree: Path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "node_modules").mkdir()
    os.symlink(outside, tree / "link-to-outside")
    os.symlink(tree / "webapp" / "node_modules", tree / "pyproj" / "node_modules")
    f = found(tree)
    assert not any(k.startswith("link-to-outside") for k in f)
    assert "pyproj/node_modules" not in f


def test_depth_limit(tree: Path):
    f = found(tree, max_depth=1)
    assert "webapp/node_modules" in f
    assert "webapp/packages/ui/node_modules" not in f


def test_exclude(tree: Path):
    f = found(tree, exclude=["*/webapp", "__pycache__"])
    assert not any(k.startswith("webapp") for k in f)
    assert not any("__pycache__" in k for k in f)
    assert "rusty/target" in f


def test_manifest_grouping_without_git(tmp_path: Path):
    (tmp_path / "mono" / "a").mkdir(parents=True)
    (tmp_path / "mono" / "a" / "package.json").write_text("{}")
    (tmp_path / "mono" / "a" / "node_modules").mkdir()
    projects = scan(str(tmp_path))
    assert [os.path.relpath(p.root, tmp_path) for p in projects] == ["mono/a"]


def test_scan_root_itself_is_project(tree: Path):
    projects = scan(str(tree / "webapp"))
    assert len(projects) == 1 and projects[0].root == str(tree / "webapp")


def test_project_mtime_ignores_dependency_contents(tree: Path):
    old = 1_000_000_000
    for dirpath, dirnames, filenames in os.walk(tree / "rusty"):
        for n in dirnames + filenames:
            os.utime(os.path.join(dirpath, n), (old, old), follow_symlinks=False)
    new_file = tree / "rusty" / "target" / "debug" / "fresh"
    new_file.write_text("new")
    os.utime(tree / "rusty" / "target", (old, old))
    (tree / "rusty" / ".DS_Store").write_text("finder")
    (p,) = [p for p in scan(str(tree)) if p.root.endswith("rusty")]
    assert p.mtime == old
