import os
import stat
from pathlib import Path

import pytest

from depclean import deleter
from depclean.scanner import scan
from depclean.sizing import Sizer, dir_size


def test_dir_size_counts_hardlinks_once(tmp_path: Path):
    d = tmp_path / "node_modules"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 100_000)
    store = tmp_path / "store"
    store.write_bytes(b"y" * 200_000)
    os.link(store, d / "linked")
    os.link(store, d / "linked2")
    total, linked = dir_size(str(d))
    assert linked >= 200_000
    assert 300_000 <= total < 300_000 + 64_000
    assert total - linked >= 100_000


def test_sizer_fills_targets(tree: Path):
    projects = scan(str(tree))
    targets = [t for p in projects for t in p.targets]
    s = Sizer()
    s.submit(targets)
    s.wait()
    assert all(t.size is not None and t.size > 0 for t in targets)
    assert s.pending == 0


def test_delete_removes_target(tree: Path):
    target = tree / "rusty" / "target"
    assert deleter.delete(str(target), str(tree)) is None
    assert not target.exists()
    assert (tree / "rusty" / "Cargo.toml").exists()


def test_delete_handles_read_only_contents(tree: Path):
    target = tree / "webapp" / "node_modules"
    ro = target / "left-pad"
    os.chmod(ro / "index.js", stat.S_IRUSR)
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert deleter.delete(str(target), str(tree)) is None
    finally:
        if ro.exists():
            os.chmod(ro, stat.S_IRWXU)
    assert not target.exists()


@pytest.mark.parametrize("rel,reason", [
    ("not-rust/target", "no longer matches"),
    ("missing/node_modules", "no longer exists"),
    ("plain-build/build", "no longer matches"),
])
def test_delete_refuses_unsafe(tree: Path, rel, reason):
    err = deleter.delete(str(tree / rel), str(tree))
    assert err is not None and reason in err
    if (tree / rel).parent.exists() and rel != "missing/node_modules":
        assert (tree / rel).exists()


def test_delete_refuses_symlink_and_outside(tree: Path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("o") / "node_modules"
    outside.mkdir()
    link = tree / "webapp" / "packages" / "node_modules"
    os.symlink(outside, link)
    assert "symlink" in deleter.delete(str(link), str(tree))
    assert "outside" in deleter.delete(str(outside), str(tree))
    assert "scan root" in deleter.delete(str(tree / "webapp" / "node_modules"), str(tree / "webapp" / "node_modules"))
    assert outside.exists()


def test_protected_roots():
    assert deleter.is_protected_root("/")
    assert deleter.is_protected_root(os.path.expanduser("~"))
    assert not deleter.is_protected_root(os.path.join(os.path.expanduser("~"), "code"))
