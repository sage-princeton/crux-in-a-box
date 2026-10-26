import time
from pathlib import Path

import pytest

from crux_scaffold.errors import ToolError
from crux_scaffold.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "workspace").mkdir()
    return Workspace(tmp_path / "workspace")


@pytest.mark.parametrize("rel", ["../outside.txt", "/etc/passwd", "site/../../outside.txt"])
def test_paths_outside_the_workspace_are_rejected(ws, rel):
    with pytest.raises(ToolError, match="outside the workspace"):
        ws.resolve(rel)


def test_symlinks_cannot_escape_the_workspace(ws, tmp_path):
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "key.txt").write_text("nope")
    (ws.root / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(ToolError, match="outside the workspace"):
        ws.read_file("link/key.txt")


def test_write_then_read_creates_parent_directories(ws):
    assert ws.write_file("site/content/new.json", "[]") == "wrote 2 characters to site/content/new.json"
    assert ws.read_file("site/content/new.json") == "[]"


def test_list_files_is_recursive_and_skips_tool_directories(ws):
    for rel in ("a.txt", "site/b.py", ".git/HEAD", "site/__pycache__/b.pyc"):
        ws.write_file(rel, "x")
    assert ws.list_files(".").splitlines() == ["a.txt", "site/b.py"]


def test_run_shell_runs_in_the_workspace_and_reports_the_exit_code(ws):
    first, rest = ws.run_shell("pwd; exit 3", 10).split("\n", 1)
    assert first == "exit_code=3"
    assert Path(rest.strip()).resolve() == ws.root.resolve()


def test_run_shell_kills_the_whole_command_on_timeout(ws):
    started = time.monotonic()
    assert ws.run_shell("sleep 5; echo late", 1) == "exit_code=timeout after 1s"
    assert time.monotonic() - started < 3


def test_run_shell_keeps_the_tail_of_long_output(ws):
    out = ws.run_shell("python3 -c \"print('x' * 30000 + 'END')\"", 10)
    assert out.startswith("exit_code=0\n... ")
    assert out.rstrip().endswith("END")
    assert len(out) < 20_200
