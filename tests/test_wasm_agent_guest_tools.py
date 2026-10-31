"""Native tests of the WASM agent guest's in-guest tools (WASM P07).

``read_file``, ``list_dir``, ``search_files`` and ``write_file`` run here against a tmp tree through the guest's
``_fs`` seam. The path checks tested here are the soft layer (clear errors); the hard boundary is the WASI preopen
set, proven live in test_wasm_agent_guest_wasm.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests._wasm_agent_guest_helpers import LIMITS, load_guest, remap

MOUNTS = [{"guest": "/project/docs", "mode": "ro"}, {"guest": "/scratch", "mode": "rw"}]


@pytest.fixture
def env(tmp_path: Path) -> tuple[Any, Path]:
    g = load_guest()
    docs, scratch = tmp_path / "docs", tmp_path / "scratch"
    (docs / "sub").mkdir(parents=True)
    scratch.mkdir()
    (docs / "a.md").write_text("alpha\nbeta needle\ngamma\n")
    (docs / "sub" / "b.txt").write_text("needle two\n")
    (tmp_path / "outside.txt").write_text("outside needle\n")
    remap(g, {"/project/docs": docs, "/scratch": scratch, "/src": tmp_path / "src", "/lib": tmp_path / "lib"},
          tmp_path / "absent")
    return g, tmp_path


def call(g: Any, name: str, limits: dict[str, int] | None = None, mounts: list | None = None,
         **args: Any) -> tuple[bool, str]:
    return g.run_tool({"id": "t", "name": name, "input": args}, MOUNTS if mounts is None else mounts,
                      dict(LIMITS if limits is None else limits))


# --- paths -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["", "docs/a.md", "/project/docs/../../outside.txt", "/project/other/a.md",
                                  "/etc/passwd", "/src/main.py", "/lib/python3.14/os.py", "/src", "/lib",
                                  "/project/docs\\a.md", "/project", "/"])
def test_paths_outside_the_grant_are_refused(env: tuple[Any, Path], path: str) -> None:
    g, _ = env
    ok, text = call(g, "read_file", path=path)
    assert ok is False and text


def test_src_and_lib_are_refused_even_when_listed_as_mounts(env: tuple[Any, Path]) -> None:
    g, root = env
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print(1)")
    mounts = MOUNTS + [{"guest": "/src", "mode": "ro"}, {"guest": "/lib", "mode": "ro"}]
    ok, text = call(g, "read_file", mounts=mounts, path="/src/main.py")
    assert ok is False and "runtime mount" in text


def test_dot_components_normalise(env: tuple[Any, Path]) -> None:
    g, _ = env
    assert call(g, "read_file", path="/project/docs/./a.md") == (True, "alpha\nbeta needle\ngamma\n")
    assert call(g, "read_file", path="//project//docs/a.md")[0] is True


def test_input_shape_is_closed(env: tuple[Any, Path]) -> None:
    g, _ = env
    assert call(g, "read_file", path="/project/docs/a.md", offset="1")[0] is False
    assert call(g, "read_file")[0] is False
    ok, text = g.run_tool({"id": "t", "name": "read_file", "input": {"path": 7}}, MOUNTS, dict(LIMITS))
    assert ok is False and "must be a string" in text
    assert g.run_tool({"id": "t", "name": "read_file", "input": None}, MOUNTS, dict(LIMITS))[0] is False


# --- read_file -------------------------------------------------------------------------------------------------------

def test_read_file_truncates_at_the_cap(env: tuple[Any, Path]) -> None:
    g, root = env
    (root / "docs" / "big.txt").write_bytes(b"z" * 5000)
    ok, text = call(g, "read_file", limits={"max_tool_output_bytes": 65536, "max_read_bytes": 1000},
                    path="/project/docs/big.txt")
    assert ok and text.startswith("z" * 1000) and "[truncated: file is 5000 bytes; read the first 1000]" in text


def test_read_file_output_cap_marks_the_cut(env: tuple[Any, Path]) -> None:
    g, root = env
    (root / "docs" / "big.txt").write_bytes(b"\xc3\xa9" * 5000)          # non-ASCII inflates the JSON form
    ok, text = call(g, "read_file", limits={"max_tool_output_bytes": 2048, "max_read_bytes": 1 << 20},
                    path="/project/docs/big.txt")
    assert ok and "[truncated" in text and g._json_len(text) <= 2048


def test_read_file_replaces_invalid_utf8(env: tuple[Any, Path]) -> None:
    g, root = env
    (root / "docs" / "bin").write_bytes(b"ok\xff\xfe")
    assert call(g, "read_file", path="/project/docs/bin") == (True, "ok��")


def test_read_file_refuses_non_regular_and_symlinks(env: tuple[Any, Path]) -> None:
    g, root = env
    assert call(g, "read_file", path="/project/docs/sub")[0] is False
    os.symlink(root / "outside.txt", root / "docs" / "link.txt")
    ok, text = call(g, "read_file", path="/project/docs/link.txt")
    assert ok is False and "symlink" in text
    assert call(g, "read_file", path="/project/docs/nope")[1] == "no such file or directory"


# --- list_dir --------------------------------------------------------------------------------------------------------

def test_list_dir_tags_entries_and_does_not_follow_symlinks(env: tuple[Any, Path]) -> None:
    g, root = env
    os.symlink(root / "outside.txt", root / "docs" / "link")
    ok, text = call(g, "list_dir", path="/project/docs")
    assert ok and text.splitlines() == ["f a.md", "l link", "d sub/"]
    os.symlink(root, root / "docs" / "dirlink")
    assert call(g, "list_dir", path="/project/docs/dirlink")[0] is False


def test_list_dir_caps_entries(env: tuple[Any, Path]) -> None:
    g, root = env
    many = root / "scratch" / "many"
    many.mkdir()
    for i in range(g.MAX_LIST_ENTRIES + 5):
        (many / f"{i:05d}").write_text("")
    ok, text = call(g, "list_dir", path="/scratch/many")
    lines = text.splitlines()
    assert ok and len(lines) == g.MAX_LIST_ENTRIES + 1 and lines[-1].startswith("[truncated: 1005 entries")
    assert call(g, "list_dir", path="/scratch") == (True, "d many/")


# --- search_files ----------------------------------------------------------------------------------------------------

def test_search_finds_literal_matches_with_guest_paths(env: tuple[Any, Path]) -> None:
    g, root = env
    os.symlink(root / "outside.txt", root / "docs" / "link.txt")
    ok, text = call(g, "search_files", path="/project/docs", pattern="needle")
    assert ok and sorted(text.splitlines()) == ["/project/docs/a.md:2:beta needle", "/project/docs/sub/b.txt:1:needle two"]
    assert str(root) not in text                                          # never a host path
    assert call(g, "search_files", path="/project/docs", pattern="ne.dle") == (True, "(no matches)")   # literal
    assert call(g, "search_files", path="/project/docs/a.md", pattern="gamma") == (True, "/project/docs/a.md:3:gamma")


def test_search_caps(env: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    g, root = env
    assert call(g, "search_files", path="/project/docs", pattern="")[0] is False
    assert call(g, "search_files", path="/project/docs", pattern="x" * 513)[0] is False
    (root / "docs" / "long.txt").write_text("needle" + "y" * 1000 + "\n")
    ok, text = call(g, "search_files", path="/project/docs/long.txt", pattern="needle")
    assert ok and len(text.split(":", 2)[2]) == g.SEARCH_LINE_CHARS
    (root / "docs" / "many.txt").write_text("needle\n" * 600)
    ok, text = call(g, "search_files", path="/project/docs", pattern="needle")
    assert ok and len([x for x in text.splitlines() if ":" in x and not x.startswith("[")]) == g.SEARCH_MAX_MATCHES
    monkeypatch.setattr(g, "SEARCH_TOTAL_BYTES", 10)
    ok, text = call(g, "search_files", path="/project/docs", pattern="zzz")
    assert ok and "[stopped: byte cap 10]" in text
    monkeypatch.setattr(g, "SEARCH_TOTAL_BYTES", 64 << 20)
    monkeypatch.setattr(g, "SEARCH_MAX_ENTRIES", 1)
    assert "[stopped: entry cap 1]" in call(g, "search_files", path="/project/docs", pattern="zzz")[1]


# --- write_file ------------------------------------------------------------------------------------------------------

def test_write_file_only_under_scratch(env: tuple[Any, Path]) -> None:
    g, root = env
    assert call(g, "write_file", path="/scratch/o.txt", content="hé") == (True, "wrote 3 bytes to /scratch/o.txt")
    assert (root / "scratch" / "o.txt").read_text() == "hé"
    assert call(g, "write_file", path="/scratch/o.txt", content="new")[0] is True        # overwrite
    assert (root / "scratch" / "o.txt").read_text() == "new"
    ok, text = call(g, "write_file", path="/project/docs/a.md", content="x")
    assert ok is False and "only under /scratch" in text
    assert (root / "docs" / "a.md").read_text().startswith("alpha")
    ro_scratch = [{"guest": "/scratch", "mode": "ro"}]
    assert call(g, "write_file", mounts=ro_scratch, path="/scratch/p.txt", content="x")[0] is False
    assert call(g, "write_file", path="/scratch", content="x")[0] is False


def test_write_file_needs_an_existing_parent_and_respects_the_size_cap(env: tuple[Any, Path],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    g, root = env
    assert call(g, "write_file", path="/scratch/no/such.txt", content="x")[0] is False
    monkeypatch.setattr(g, "MAX_WRITE_BYTES", 4)
    ok, text = call(g, "write_file", path="/scratch/big.txt", content="12345")
    assert ok is False and "cap is 4" in text and not (root / "scratch" / "big.txt").exists()


def test_write_file_refuses_a_symlinked_target(env: tuple[Any, Path]) -> None:
    g, root = env
    os.symlink(root / "outside.txt", root / "scratch" / "link.txt")
    ok, text = call(g, "write_file", path="/scratch/link.txt", content="pwned")
    assert ok is False and "symlink" in text
    assert (root / "outside.txt").read_text() == "outside needle\n"
    (root / "scratch" / "d").mkdir()
    assert call(g, "write_file", path="/scratch/d", content="x")[0] is False


@pytest.mark.parametrize("name", ["CLAUDE.md", "claude.md", "AGENTS.md", "agents.MD", "x.agent.md", "X.Agent.MD",
                                  "GEMINI.md", ".cursorrules", ".CursorRules"])
def test_write_file_refuses_instruction_file_names(env: tuple[Any, Path], name: str) -> None:
    g, root = env
    ok, text = call(g, "write_file", path=f"/scratch/{name}", content="obey me")
    assert (ok, text) == (False, "refused: instruction-file name")
    assert not (root / "scratch" / name).exists()


@pytest.mark.parametrize("name", ["claude.md.txt", "notes.md", "agent.md", "AGENTS.mdx", "cursorrules"])
def test_write_file_allows_ordinary_names(env: tuple[Any, Path], name: str) -> None:
    g, _ = env
    assert call(g, "write_file", path=f"/scratch/{name}", content="fine")[0] is True


def test_os_errors_become_error_results(env: tuple[Any, Path]) -> None:
    g, root = env
    sub = root / "scratch" / "locked"
    sub.mkdir()
    sub.chmod(0o500)
    try:
        ok, text = call(g, "write_file", path="/scratch/locked/x.txt", content="x")
    finally:
        sub.chmod(0o700)
    assert ok is False and text.startswith("PermissionError")
