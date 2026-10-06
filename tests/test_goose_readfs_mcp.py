"""Offline tests for scripts/goose-readfs-mcp.py, the read-only filesystem MCP server for Goose.

The server ships in ``scripts/`` (not an importable package), so it is loaded via importlib like
``tests/test_goose_coordination_mcp.py``. Every test is offline: the JSON-RPC handlers are driven
directly against a tmp workspace, plus one subprocess run of the real stdio loop.

The load-bearing properties: no mutating tool exists (and the source contains no write, delete,
exec or network call: a tripwire scan, itself mutation-tested here); every path is confined to the
root after resolving symlinks; secrets inside the root are refused case-insensitively; outputs and
work are bounded, and every cap is visible in the result.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SERVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "goose-readfs-mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_readfs_mcp", _SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("import os\n\ndef f():\n    return 'needle'\n")
    (root / "README.md").write_text("# Title\nsome needle text\n")
    (root / ".env").write_text("API_KEY=s3cret\n")
    (root / ".env.example").write_text("API_KEY=\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]\nurl = https://token@example\n")
    (root / "id_ed25519").write_text("PRIVATE\n")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside secret\n")
    return root, outside


@pytest.fixture
def ws(mod, tree):
    return mod.Workspace(tree[0])


def _call(mod, ws, name, **args):
    resp = mod.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, ws
    )
    result = resp["result"]
    return result["content"][0]["text"], result["isError"]


# --- protocol -----------------------------------------------------------------------------


def test_initialize_and_tool_set_is_exactly_read_only(mod, ws):
    init = mod.handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}, ws)
    assert init["result"]["serverInfo"]["name"] == "agentteams_readfs"
    tools = mod.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ws)["result"]["tools"]
    assert sorted(t["name"] for t in tools) == ["find", "grep", "list_dir", "read_file", "stat"]


def test_unknown_tool_and_method(mod, ws):
    assert _call(mod, ws, "write_file", path="x")[1] is True
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 9, "method": "resources/list"}, ws)
    assert resp["error"]["code"] == -32601
    assert mod.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, ws) is None


@pytest.mark.parametrize("params", ["oops", {"name": "read_file", "arguments": "oops"}])
def test_malformed_params_are_a_clean_error(mod, ws, params):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params}, ws)
    assert resp["result"]["isError"] is True


def test_non_string_path_is_a_clean_error(mod, ws):
    assert _call(mod, ws, "read_file", path=["README.md"])[1] is True


# --- reading ------------------------------------------------------------------------------


def test_read_file_numbers_lines_and_pages(mod, ws):
    text, err = _call(mod, ws, "read_file", path="src/a.py")
    assert not err and "src/a.py (lines 1-4 of 4" in text and "     4      return 'needle'" in text
    text, _ = _call(mod, ws, "read_file", path="src/a.py", offset=3, limit=1)
    assert "lines 3-3 of 4" in text and "def f()" in text and "needle" not in text
    assert "[truncated" in text


def test_binary_is_reported_not_dumped(mod, ws):
    text, err = _call(mod, ws, "read_file", path="blob.bin")
    assert not err and "binary file" in text and "\x00" not in text


def test_list_find_grep_stat(mod, ws):
    listing, _ = _call(mod, ws, "list_dir")
    assert "dir   src/" in listing and "file  README.md" in listing and "file  .env.example" in listing
    for secret in (".env  ", ".git", "id_ed25519"):
        assert secret not in listing
    assert _call(mod, ws, "find", pattern="*.py")[0] == "src/a.py"
    hits, _ = _call(mod, ws, "grep", pattern="needle")
    assert hits.splitlines() == ["README.md:2: some needle text", "src/a.py:4:     return 'needle'",
                                 "[skipped 1 binary or unopenable file(s)]"]
    assert "s3cret" not in _call(mod, ws, "grep", pattern="API_KEY|token")[0]
    info = json.loads(_call(mod, ws, "stat", path="README.md")[0])
    assert info["type"] == "file" and info["path"] == "README.md"


@pytest.mark.parametrize("tool", ["find", "grep"])
def test_empty_pattern_refused(mod, ws, tool):
    assert _call(mod, ws, tool, pattern="")[1] is True


# --- confinement --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["../outside/secret.txt", "src/../../outside/secret.txt"])
def test_dotdot_escape_refused(mod, ws, path):
    text, err = _call(mod, ws, "read_file", path=path)
    assert err and "outside the workspace" in text


def test_absolute_paths(mod, ws, tree):
    assert "outside the workspace" in _call(mod, ws, "read_file", path=str(tree[1] / "secret.txt"))[0]
    assert _call(mod, ws, "read_file", path="/etc/hosts")[1] is True
    text, err = _call(mod, ws, "read_file", path=str(tree[0] / "README.md"))
    assert not err and "Title" in text


def test_filesystem_root_is_refused_as_workspace(mod):
    with pytest.raises(ValueError):
        mod.Workspace(Path("/"))


def test_symlink_file_and_dir_escape_refused(mod, ws, tree):
    root, outside = tree
    (root / "link.txt").symlink_to(outside / "secret.txt")
    (root / "linkdir").symlink_to(outside)
    assert "outside the workspace" in _call(mod, ws, "read_file", path="link.txt")[0]
    assert "outside the workspace" in _call(mod, ws, "read_file", path="linkdir/secret.txt")[0]
    assert "outside the workspace" in _call(mod, ws, "list_dir", path="linkdir")[0]
    assert "outside secret" not in _call(mod, ws, "grep", pattern="outside")[0]
    assert "secret.txt" not in _call(mod, ws, "find", pattern="*")[0]
    assert "points outside the workspace" in _call(mod, ws, "list_dir")[0]


def test_symlink_inside_to_denied_target_refused(mod, ws, tree):
    (tree[0] / "notes.txt").symlink_to(tree[0] / ".env")
    assert _call(mod, ws, "read_file", path="notes.txt")[1] is True
    assert "s3cret" not in _call(mod, ws, "grep", pattern="s3cret")[0]


@pytest.mark.parametrize("path", [".env", ".git/config", "id_ed25519", "src/../.env"])
def test_deny_list_inside_root(mod, ws, path):
    text, err = _call(mod, ws, "read_file", path=path)
    assert err and "deny list" in text


@pytest.mark.parametrize("name", [".ENV", ".Git/config", "ID_ED25519", "cert.P12", "credentials.json",
                                  ".envrc", "prod.tfvars", "keys/server.pem", "deploy/.ssh/config"])
def test_deny_list_is_case_folded_and_covers_secret_files(mod, ws, name):
    assert ws.denied(name), name


def test_env_template_is_readable(mod, ws):
    text, err = _call(mod, ws, "read_file", path=".env.example")
    assert not err and "API_KEY=" in text


def test_extra_deny_globs(mod, tree):
    ws = mod.Workspace(tree[0], ("*.MD",))
    assert _call(mod, ws, "read_file", path="README.md")[1] is True


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_is_refused_without_blocking(mod, ws, tree):
    os.mkfifo(tree[0] / "pipe")
    started = time.monotonic()
    assert _call(mod, ws, "read_file", path="pipe")[1] is True
    assert "pipe" not in _call(mod, ws, "grep", pattern="x")[0]
    assert time.monotonic() - started < 5


def test_swapped_file_is_refused(mod, tree, monkeypatch):
    """A file swapped between the check and the open (different inode) is refused, not read."""
    real = str(tree[0] / "README.md")
    other = os.stat(tree[1] / "secret.txt")
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: other)
    with pytest.raises(mod.ReadfsError, match="changed while being opened"):
        mod._open_regular(mod.Workspace(tree[0]), real, 100)


def test_handle_resolving_outside_is_refused(mod, tree, monkeypatch):
    """A parent directory swapped for an outward link after resolve(): the open handle's path decides."""
    monkeypatch.setattr(mod, "_fd_path", lambda fd: str(tree[1] / "secret.txt"))
    with pytest.raises(mod.ReadfsError, match="outside the workspace while being opened"):
        mod._open_regular(mod.Workspace(tree[0]), str(tree[0] / "README.md"), 100)


def test_fd_path_reports_the_real_file(mod, tree):
    fd = os.open(tree[0] / "README.md", os.O_RDONLY)
    try:
        reported = mod._fd_path(fd)
    finally:
        os.close(fd)
    if reported is None:
        pytest.skip("this OS cannot report a handle's path")
    assert os.path.realpath(reported) == os.path.realpath(tree[0] / "README.md")


def test_allowlist_does_not_override_operator_deny(mod, tree):
    ws = mod.Workspace(tree[0], (".env.example",))
    assert _call(mod, ws, "read_file", path=".env.example")[1] is True


def test_signing_related_source_files_stay_readable(mod, ws):
    assert not ws.denied("tests/test_signing_key_isolation.py")
    assert not ws.denied("references/authorized-verify-keys/provision-operator-signing-key.sh")


def test_non_string_tool_name_is_a_clean_error(mod, ws):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": ["read_file"], "arguments": {}}}, ws)
    assert resp["result"]["isError"] is True


def test_grep_reports_binary_skips(mod, ws):
    text, _ = _call(mod, ws, "grep", pattern="binary")
    assert "[skipped 1 binary or unopenable file(s)]" in text


# --- bounds, all visible ------------------------------------------------------------------


def test_read_and_match_caps(mod, ws, tree):
    (tree[0] / "big.txt").write_text("".join(f"line {i} needle\n" for i in range(5000)))
    text, _ = _call(mod, ws, "read_file", path="big.txt")
    assert f"lines 1-{mod.MAX_READ_LINES} of 5000" in text and "[truncated" in text
    hits, _ = _call(mod, ws, "grep", pattern="needle", glob="big.txt")
    assert f"[truncated at {mod.MAX_RESULTS} matches]" in hits
    assert _call(mod, ws, "grep", pattern="(")[1] is True  # invalid regex is a clean error


def test_walk_cap_is_reported_not_silent(mod, ws, tree, monkeypatch):
    for i in range(30):
        (tree[0] / "src" / f"f{i}.txt").write_text("x\n")
    monkeypatch.setattr(mod, "MAX_ENTRIES_VISITED", 10)
    text, _ = _call(mod, ws, "find", pattern="*.nomatch")
    assert "[stopped: walked more than 10 entries" in text and "(no matches)" not in text


def test_depth_cap_is_reported(mod, ws, tree, monkeypatch):
    deep = tree[0] / "d1" / "d2" / "d3"
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("x\n")
    monkeypatch.setattr(mod, "MAX_DEPTH", 1)
    text, _ = _call(mod, ws, "find", pattern="x.txt", path="d1")
    assert "[not descended below depth 1" in text and "d1/d2/d3/x.txt" not in text


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="no interval timer")
def test_catastrophic_regex_times_out(mod, ws, tree, monkeypatch):
    (tree[0] / "evil.txt").write_text("a" * 40 + "b\n")
    monkeypatch.setattr(mod, "MAX_GREP_SECONDS", 0.5)
    started = time.monotonic()
    text, err = _call(mod, ws, "grep", pattern="(a+)+$", glob="evil.txt")
    assert err and "exceeded" in text and time.monotonic() - started < 5


def test_large_file_skip_is_reported(mod, ws, tree, monkeypatch):
    (tree[0] / "huge.log").write_text("needle\n" * 100)
    monkeypatch.setattr(mod, "MAX_GREP_FILE_BYTES", 50)
    text, _ = _call(mod, ws, "grep", pattern="needle", glob="huge.log")
    assert "[skipped 1 file(s) over 50 bytes]" in text


# --- read-only by construction (a tripwire scan, itself mutation-tested) --------------------

_FORBIDDEN_ATTRS = {
    "write", "write_text", "write_bytes", "unlink", "remove", "rmdir", "rename", "replace", "mkdir",
    "makedirs", "chmod", "chown", "symlink", "symlink_to", "hardlink_to", "link", "truncate", "ftruncate",
    "touch", "utime", "system", "popen", "spawn", "posix_spawn", "execl", "execv", "execve", "execvp",
    "fork", "kill", "rmtree", "copy", "copyfile", "move", "mkfifo", "mknod", "spawnl", "spawnle",
    "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawnp", "execle", "execlp",
    "execlpe", "execvpe", "pwrite", "pwritev", "writev", "removedirs", "renames", "killpg", "sendfile",
    "lchmod", "lchown", "chflags", "lchflags", "setxattr", "removexattr",
}
_FORBIDDEN_MODULES = {"subprocess", "socket", "shutil", "urllib", "http", "ftplib", "ctypes",
                      "multiprocessing", "asyncio", "tempfile", "importlib", "pty", "sqlite3", "zipfile",
                      "tarfile", "gzip", "bz2", "lzma", "dbm", "shelve", "runpy", "ssl", "smtplib", "webbrowser"}
_FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "getattr", "setattr", "globals"}
_ALLOWED_OPEN_FLAGS = {"O_RDONLY", "O_NONBLOCK", "O_NOFOLLOW", "O_CLOEXEC"}


def _write_capable_calls(source: str) -> list[str]:
    """Return every construct in ``source`` that could write, delete, execute or reach the network."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            found += [f"import {n}" for n in names if n.split(".")[0] in _FORBIDDEN_MODULES]
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            # The one legitimate write: JSON-RPC replies on the stdio transport (serve's `stdout`).
            # Rule 12: this exemption would also pass a file handle deliberately named `stdout`;
            # the scan is a tripwire, and the integrity pin plus review cover deliberate evasion.
            if not (node.attr == "write" and getattr(node.value, "id", "") == "stdout"):
                found.append(f".{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            found.append(f"{node.id} (line {node.lineno})")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                found.append(f"bare open() (line {node.lineno}); use _open_regular")
            elif isinstance(func, ast.Attribute) and func.attr == "open":
                # The only permitted open is os.open(<path>, _READ_FLAGS), with _READ_FLAGS checked below.
                owner = getattr(func.value, "id", "")
                flags = node.args[1] if len(node.args) > 1 else None
                if owner != "os" or not (isinstance(flags, ast.Name) and flags.id == "_READ_FLAGS") or node.keywords:
                    found.append(f"{owner or '?'}.open() (line {node.lineno})")
            elif isinstance(func, ast.Attribute) and func.attr == "fdopen":
                mode = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(mode, ast.Constant) and set(mode.value) <= {"r", "b"}):
                    found.append(f"fdopen() not read-only (line {node.lineno})")
    return found


def test_source_has_no_write_delete_exec_or_network_calls():
    source = _SERVER_PATH.read_text()
    assert _write_capable_calls(source) == []
    assert _read_flags_problems(source) == []


def _read_flags_problems(source: str) -> list[str]:
    """_READ_FLAGS may combine only allowed os.O_* flags (and 0 for a missing one): no ints, no names."""
    tree = ast.parse(source)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_READ_FLAGS"]
    if len(assigns) != 1:
        return ["_READ_FLAGS must be assigned exactly once"]
    rebinds = [n for n in ast.walk(tree)
               if (isinstance(n, ast.AugAssign) and getattr(n.target, "id", "") == "_READ_FLAGS")
               or (isinstance(n, ast.NamedExpr) and n.target.id == "_READ_FLAGS")]
    if rebinds:
        return [f"_READ_FLAGS rebound at line {rebinds[0].lineno}"]
    problems = []
    for n in ast.walk(assigns[0].value):
        if isinstance(n, ast.Attribute) and n.attr not in _ALLOWED_OPEN_FLAGS:
            problems.append(f"flag {n.attr}")
        elif isinstance(n, ast.Constant) and not (n.value == 0 or n.value in _ALLOWED_OPEN_FLAGS):
            problems.append(f"constant {n.value!r}")
        elif isinstance(n, ast.Name) and n.id not in {"os", "hasattr"}:
            problems.append(f"name {n.id}")
    return problems


@pytest.mark.parametrize("injected", [
    'open(real, "w")', 'os.remove(real)', 'import subprocess', 'Path(real).write_text("x")',
    'io.open(real, "w")', 'os.open(real, os.O_WRONLY)', 'getattr(os, "rem" + "ove")(real)',
    '__import__("shutil")', 'os.fdopen(3, "wb")', 'Path(real).symlink_to("/")',
    'os.open(real, _READ_FLAGS | os.O_WRONLY)', 'f = os.O_WRONLY; os.open(real, f)', 'os.open(real, 1)',
    'os.spawnl(0, "x")', 'os.pwrite(3, b"x", 0)', 'import sqlite3',
])
def test_scan_catches_injected_mutations(injected):
    source = _SERVER_PATH.read_text().replace(
        "def _is_binary(", f"def _evil(real):\n    {injected}\n\n\ndef _is_binary(", 1)
    assert _write_capable_calls(source), f"scan missed: {injected}"


@pytest.mark.parametrize("bad", ["_READ_FLAGS = os.O_RDONLY | os.O_WRONLY", "_READ_FLAGS = os.O_RDONLY | 1",
                                 "_READ_FLAGS = os.O_RDONLY | EXTRA",
    "_READ_FLAGS = os.O_RDONLY\n_READ_FLAGS |= os.O_WRONLY",
    "_READ_FLAGS = os.O_RDONLY\nX = (_READ_FLAGS := os.O_WRONLY)"])
def test_read_flags_check_catches_widening(bad):
    source = _SERVER_PATH.read_text()
    start = source.index("_READ_FLAGS = (")
    end = source.index("\n\n", start)
    assert _read_flags_problems(source[:start] + bad + source[end:])


def test_stdio_loop_end_to_end(tree):
    root, _ = tree
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "README.md"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "../outside/secret.txt"}}},
    ]
    stdin = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
    out = subprocess.run([sys.executable, str(_SERVER_PATH), "--root", str(root)], input=stdin,
                         capture_output=True, text=True, timeout=30, check=True).stdout.splitlines()
    replies = [json.loads(x) for x in out]
    assert [r.get("id") for r in replies] == [1, 2, 3, None]
    assert "Title" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["isError"] is True
    assert replies[3]["error"]["code"] == -32700
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_cli_refuses_filesystem_root():
    proc = subprocess.run([sys.executable, str(_SERVER_PATH), "--root", "/"], input="",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0 and "filesystem root" in proc.stderr
