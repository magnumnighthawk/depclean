import json
from pathlib import Path

from depclean import fmt
from depclean.cli import main
from depclean.scanner import scan
from depclean.tui import Model


def model(tree: Path) -> Model:
    projects = scan(str(tree))
    sizes = {"webapp": 500, "pyproj": 300, "rusty": 900}
    for p in projects:
        for t in p.targets:
            t.size = sizes.get(Path(p.root).name, 10) // len(p.targets)
    return Model(str(tree), projects)


def test_sort_and_cursor_follows_item(tree: Path):
    m = model(tree)
    rows = m.rows()
    assert Path(rows[0].project.root).name == "rusty"
    m.move(1, rows)
    key = m.rows()[m.cursor].key
    m.sort = "name"
    rows = m.rows()
    assert rows[m.cursor].key == key


def test_toggle_project_and_partial(tree: Path):
    m = model(tree)
    web = next(r for r in m.rows() if r.project.root.endswith("webapp"))
    m.toggle(web)
    assert m.state(web.project) == "all"
    m.expand(web, True)
    child = next(r for r in m.rows() if r.target is not None)
    m.toggle(child)
    assert m.state(web.project) == "some"
    m.toggle(web)
    assert m.state(web.project) == "all"
    m.toggle(web)
    assert m.state(web.project) == "none"


def test_toggle_all_respects_filter(tree: Path):
    m = model(tree)
    m.filter = "rust"
    m.toggle_all()
    assert {Path(t.project_root).name for t in m.selected_targets()} == {"rusty"}
    m.toggle_all()
    assert not m.selected


def test_remove(tree: Path):
    m = model(tree)
    rusty = next(p for p in m.projects if p.root.endswith("rusty"))
    m.selected = {rusty.targets[0].path}
    m.remove({rusty.targets[0].path})
    assert rusty not in m.projects and not m.selected


def test_cli_json(tree: Path, capsys):
    assert main([str(tree), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["reclaimable_bytes"] > 0
    kinds = {f["kind"] for p in data["projects"] for f in p["folders"]}
    assert {"node", "python venv", "rust", "gradle"} <= kinds


def test_cli_list_no_size(tree: Path, capsys):
    assert main([str(tree), "--list", "--no-size"]) == 0
    out = capsys.readouterr().out
    assert "webapp" in out and "9 folders" in out


def test_cli_interactive_needs_tty(tree: Path, capsys):
    assert main([str(tree)]) == 2
    assert "--list" in capsys.readouterr().err


def test_cli_missing_path(capsys):
    assert main(["/definitely/not/here"]) == 2


def test_fmt():
    assert fmt.size(None) == "…"
    assert fmt.size(512) == "512 B"
    assert fmt.size(1536) == "1.5 KB"
    assert fmt.size(5 * 1024 ** 3) == "5.0 GB"
    assert fmt.age(None) == "-"
    assert fmt.age(0 + 1, now=1 + 3 * 86400) == "3d"
    assert fmt.age(1, now=1 + 400 * 86400) == "1.1y"
