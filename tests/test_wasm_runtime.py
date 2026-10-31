"""Tests for agentteams/wasm_runtime.py (P03): pins, fetch, safe extraction, install, verify-on-use.

No external network. Archives are synthesized in ``tmp_path`` with stdlib ``tarfile``/``zipfile``; a fake
pins file points at them and ``fetch`` is injected. One test drives the streaming downloader through
urllib against a loopback ``http.server``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import http.server
import io
import json
import os
import re
import socket
import stat
import subprocess
import tarfile
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest

from agentteams import wasm_runtime as wr

REPO = Path(__file__).resolve().parents[1]
PLAT = "linux-x86_64"
STRIP = "tool-v1.0.0-x86_64-linux"


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tar_xz(path: Path, members: list[tuple]) -> Path:
    """members: (name, data|None for dir, kind) where kind in reg/dir/sym/lnk/chr/fifo."""
    with tarfile.open(path, "w:xz") as tf:
        for name, data, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "dir":
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o777
                tf.addfile(ti)
            elif kind == "reg":
                ti.size = len(data)
                ti.mode = 0o4777  # setuid + world-writable in the archive: must be ignored
                tf.addfile(ti, io.BytesIO(data))
            elif kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, data
                tf.addfile(ti)
            elif kind == "lnk":
                ti.type, ti.linkname = tarfile.LNKTYPE, data
                tf.addfile(ti)
            elif kind == "chr":
                ti.type = tarfile.CHRTYPE
                tf.addfile(ti)
            elif kind == "fifo":
                ti.type = tarfile.FIFOTYPE
                tf.addfile(ti)
    return path


def _zip(path: Path, members: list[tuple], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            if data is None:
                zf.writestr(zipfile.ZipInfo(name), b"")
            else:
                zf.writestr(name, data)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(zi, b"/etc/passwd")
    return path


TOOL = b"\x7fELF fake wasmtime binary"
LICENSE = b"Apache-2.0 fixture license\n"


def _wasmtime_archive(tmp: Path, extra: list[tuple] = ()) -> Path:
    return _tar_xz(tmp / "wt.tar.xz", [
        (STRIP, None, "dir"),
        (f"{STRIP}/wasmtime", TOOL, "reg"),
        (f"{STRIP}/wasmtime-min", b"min", "reg"),
        (f"{STRIP}/LICENSE", LICENSE, "reg"),
        (f"{STRIP}/README.md", b"readme", "reg"),
        *extra,
    ])


PY_WASM = b"\0asm\x01\0\0\0 fake python"
STDLIB = {"lib/python3.15/os.py": b"# os\n", "lib/python3.15/json/__init__.py": b"# json\n"}


def _python_archive(tmp: Path) -> Path:
    return _zip(tmp / "py.zip", [("python.wasm", PY_WASM), ("lib/", None),
                                 *[(k, v) for k, v in STDLIB.items()], ("junk.txt", b"x")])


def _stdlib_digest() -> tuple[str, int]:
    lines = sorted(f"{k[len('lib/'):]}\0{_sha(v)}\n" for k, v in STDLIB.items())
    return _sha("".join(lines).encode()), len(lines)


def _pins(tmp: Path, *, todo: bool = False) -> dict:
    wt = _wasmtime_archive(tmp)
    py = _python_archive(tmp)
    js = tmp / "qjs.wasm"
    js.write_bytes(b"\0asm\x01\0\0\0 qjs")
    tdig, tcount = _stdlib_digest()
    url = "https://github.com/o/r/releases/download/v1.0.0/"
    doc = {
        "schema": wr.PINS_SCHEMA, "updated": "2026-10-31",
        "components": {
            "wasmtime": {
                "version": "1.0.0", "license": "Apache-2.0", "runtime_mounts": [],
                "source": {"repo": "o/wasmtime", "tag": "v{version}"},
                "platforms": {PLAT: {
                    "asset_re": r"^tool-v{version}-x86_64-linux\.tar\.xz$",
                    "url": url + "wt.tar.xz", "size": wt.stat().st_size, "sha256": _sha(wt.read_bytes()),
                    "archive": "tar.xz", "strip": "tool-v{version}-x86_64-linux", "entry": "wasmtime",
                    "keep": ["wasmtime", "LICENSE"],
                    "files": {"wasmtime": {"sha256": wr.TODO_PIN if todo else _sha(TOOL), "exec": True},
                              "LICENSE": {"sha256": _sha(LICENSE)}}}},
            },
            "python": {
                "version": "3.15.0", "license": "PSF-2.0", "runtime_mounts": [{"tree": "lib", "guest": "/lib"}],
                "source": {"repo": "o/python", "tag": "v{version}"},
                "platforms": {"any": {
                    "asset_re": r"^python-{version}-wasi_sdk-[0-9]+\.zip$",
                    "url": url + "py.zip", "size": py.stat().st_size, "sha256": _sha(py.read_bytes()),
                    "archive": "zip", "strip": "", "entry": "python.wasm", "keep": ["python.wasm", "lib/"],
                    "files": {"python.wasm": {"sha256": _sha(PY_WASM)}},
                    "tree": {"root": "lib", "sha256": tdig, "count": tcount}}},
            },
            "javascript": {
                "version": "0.17.0", "license": "MIT", "runtime_mounts": [],
                "source": {"repo": "o/qjs", "tag": "v{version}"},
                "platforms": {"any": {
                    "asset_re": r"^qjs-wasi\.wasm$",
                    "url": url + "qjs.wasm", "size": js.stat().st_size, "sha256": _sha(js.read_bytes()),
                    "archive": "none", "strip": "", "entry": "qjs-wasi.wasm", "keep": ["qjs-wasi.wasm"],
                    "files": {"qjs-wasi.wasm": {"sha256": _sha(js.read_bytes())}}}},
            },
        },
    }
    doc["_fixtures"] = {"wasmtime": str(wt), "python": str(py), "javascript": str(js)}
    return doc


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A project dir, a fixtures dir, a fake pins file, and a local fetch."""
    fx = tmp_path / "fixtures"
    fx.mkdir()
    doc = _pins(fx)
    by_url = {}
    for comp, path in doc.pop("_fixtures").items():
        pe = next(iter(doc["components"][comp]["platforms"].values()))
        by_url[pe["url"]] = Path(path)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    monkeypatch.setattr(wr, "PINS_PATH", pins)
    monkeypatch.setattr(wr, "host_platform", lambda: PLAT)
    project = tmp_path / "proj"
    project.mkdir()
    calls: list[str] = []

    def fetch(url: str, size: int, dest: Path) -> str:
        calls.append(url)
        data = by_url[url].read_bytes()
        dest.write_bytes(data)
        return _sha(data)

    class E:
        pass
    e = E()
    e.project, e.pins, e.doc, e.fetch, e.calls, e.by_url, e.tmp = project, pins, doc, fetch, calls, by_url, tmp_path
    return e


def _rewrite_pins(env, mutate) -> None:
    doc = json.loads(env.pins.read_text())
    mutate(doc)
    env.pins.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# the packaged pins file
# ---------------------------------------------------------------------------

def test_pins_file_schema_and_no_short_digests():
    doc = json.loads((REPO / "agentteams" / "data" / "wasm_runtime_pins.json").read_text())
    assert wr.validate_pins(doc) == []
    text = json.dumps(doc)
    for val in re.findall(r'"sha256": "([^"]*)"', text):
        assert val == wr.TODO_PIN or re.fullmatch(r"[0-9a-f]{64}", val), val
    for url in re.findall(r'"url": "([^"]*)"', text):
        assert url.startswith("https://") and wr._host(url) in wr.DOWNLOAD_HOSTS


def test_packaged_pins_carry_the_verified_versions():
    """D9 / §4: the versions and the API digests that were verified against release metadata."""
    doc = wr.load_pins()
    c = doc["components"]
    assert c["wasmtime"]["version"] == "36.0.20"
    assert c["python"]["version"] == "3.15.0"
    assert c["javascript"]["version"] == "0.17.0"
    assert set(c["wasmtime"]["platforms"]) == set(wr.PLATFORMS)
    assert c["wasmtime"]["platforms"]["darwin-arm64"]["sha256"] == \
        "7228786d479b0e5bddf293d07f53b9aecb1a8f1d55a02821640aa65a522e2631"
    # Extracted-file hashes were not downloadable here: they must stay TODO-PIN (Rule 5).
    assert c["wasmtime"]["platforms"]["darwin-arm64"]["files"]["wasmtime"]["sha256"] == wr.TODO_PIN
    assert c["python"]["platforms"]["any"]["tree"]["sha256"] == wr.TODO_PIN


def test_pins_sha256_matches_packaged_file():
    assert wr.PINS_SHA256 == _sha((REPO / "agentteams" / "data" / "wasm_runtime_pins.json").read_bytes())


def test_pins_runtime_mounts_shape():
    doc = wr.load_pins()
    assert doc["components"]["python"]["runtime_mounts"] == [{"tree": "lib", "guest": "/lib"}]
    bad = json.loads(json.dumps(doc))
    bad["components"]["python"]["runtime_mounts"] = [{"tree": "lib", "guest": "/project"}]
    assert any("guest must match" in p for p in wr.validate_pins(bad))
    bad["components"]["python"]["runtime_mounts"] = [{"tree": "stdlib", "guest": "/lib"}]
    assert any("is not pinned" in p for p in wr.validate_pins(bad))


def test_pins_validation_refuses_short_digest_and_foreign_host():
    doc = wr.load_pins()
    bad = json.loads(json.dumps(doc))
    bad["components"]["javascript"]["platforms"]["any"]["sha256"] = "42a732a6"
    bad["components"]["wasmtime"]["platforms"]["linux-x86_64"]["url"] = "https://evil.example/w.tar.xz"
    problems = wr.validate_pins(bad)
    assert any("neither 64 hex" in p for p in problems)
    assert any("allowlisted host" in p for p in problems)


def test_todo_pin_fails_closed_install_and_resolve_packaged(tmp_path, monkeypatch):
    """The real pins: wasmtime/python hold TODO-PIN and are refused before any fetch."""
    def boom(*a):  # pragma: no cover - must not be reached
        raise AssertionError("fetch called for a TODO-PIN component")
    monkeypatch.setattr(wr, "host_platform", lambda: "darwin-arm64")
    for comp in ("wasmtime", "python"):
        with pytest.raises(wr.WasmRuntimeError, match="pin incomplete"):
            wr.install(tmp_path, [comp], fetch=boom)
        with pytest.raises(wr.WasmRuntimeError, match="pin incomplete"):
            wr.resolve(tmp_path, comp)
    assert not (tmp_path / ".agentteams").exists()


def test_todo_pin_fails_closed_fixture(env):
    _rewrite_pins(env, lambda d: d["components"]["wasmtime"]["platforms"][PLAT]["files"]["wasmtime"]
                  .update(sha256=wr.TODO_PIN))
    with pytest.raises(wr.WasmRuntimeError, match="pin incomplete"):
        wr.install(env.project, ["javascript", "wasmtime"], fetch=env.fetch)
    assert env.calls == []   # refused before any network, even for the complete component
    assert wr.status(env.project)["wasmtime"] == "pin incomplete"


# ---------------------------------------------------------------------------
# platform mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plat,machine,want", [
    ("darwin", "arm64", "darwin-arm64"), ("darwin", "x86_64", "darwin-x86_64"),
    ("linux", "x86_64", "linux-x86_64"), ("linux", "aarch64", "linux-aarch64"),
    ("win32", "AMD64", "windows-x86_64"),
])
def test_host_platform_mapping(monkeypatch, plat, machine, want):
    monkeypatch.setattr(wr.sys, "platform", plat)
    monkeypatch.setattr(wr._platform, "machine", lambda: machine)
    assert wr.host_platform() == want


def test_host_platform_unknown_raises(monkeypatch):
    monkeypatch.setattr(wr.sys, "platform", "sunos5")
    monkeypatch.setattr(wr._platform, "machine", lambda: "sparc")
    with pytest.raises(wr.WasmRuntimeError):
        wr.host_platform()


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def test_http_scheme_refused(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="refusing download URL"):
        wr._fetch("http://github.com/x", 1, tmp_path / "d")
    with pytest.raises(wr.WasmRuntimeError, match="refusing download URL"):
        wr._fetch("https://evil.example/x", 1, tmp_path / "d")
    assert not (tmp_path / "d").exists()


def test_redirect_off_allowlist_refused():
    handler = wr._redirect_handler_class()()
    req = urllib.request.Request("https://github.com/o/r/releases/download/v1/a")
    with pytest.raises(wr.WasmRuntimeError, match="refusing download URL"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/a")
    with pytest.raises(wr.WasmRuntimeError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://release-assets.githubusercontent.com/a")
    ok = handler.redirect_request(req, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/a")
    assert ok.full_url == "https://release-assets.githubusercontent.com/a"
    assert handler.max_redirections == wr.MAX_REDIRECTS


@pytest.fixture
def loopback(tmp_path):
    """A loopback http.server serving tmp_path/www (no external network)."""
    www = tmp_path / "www"
    www.mkdir()

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(www), **k)

        def log_message(self, *a):
            pass
    try:
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    except OSError as exc:  # pragma: no cover - sandbox without loopback
        pytest.skip(f"loopback bind unavailable: {exc}")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield www, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def _open_local(url: str):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10)


def test_stream_through_urllib_from_loopback(loopback, tmp_path):
    www, base = loopback
    data = os.urandom(3 * (1 << 20) + 17)
    (www / "a.bin").write_bytes(data)
    with _open_local(base + "a.bin") as resp:
        got = wr._stream_to(resp, len(data), tmp_path / "out")
    assert got == _sha(data) and (tmp_path / "out").read_bytes() == data


def test_fetch_size_overrun_aborts(loopback, tmp_path):
    www, base = loopback
    (www / "big.bin").write_bytes(b"x" * ((1 << 20) * 2 + 1))
    with _open_local(base + "big.bin") as resp, pytest.raises(wr.WasmRuntimeError, match="exceeds its pinned size"):
        wr._stream_to(resp, 1 << 20, tmp_path / "out")


def test_fetch_short_body_refused(loopback, tmp_path):
    www, base = loopback
    (www / "s.bin").write_bytes(b"abc")
    with _open_local(base + "s.bin") as resp, pytest.raises(wr.WasmRuntimeError, match="pinned size"):
        wr._stream_to(resp, 10, tmp_path / "out")


def test_fetch_refuses_existing_symlink_dest(loopback, tmp_path):
    www, base = loopback
    (www / "s.bin").write_bytes(b"abc")
    os.symlink(tmp_path / "victim", tmp_path / "out")
    with _open_local(base + "s.bin") as resp, pytest.raises(OSError):
        wr._stream_to(resp, 3, tmp_path / "out")
    assert not (tmp_path / "victim").exists()


def test_fetch_sha_mismatch_quarantines(env):
    def bad_fetch(url, size, dest):
        data = env.by_url[url].read_bytes()
        dest.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        return "0" * 64
    with pytest.raises(wr.WasmRuntimeError, match="does not match its pin"):
        wr.install(env.project, ["javascript"], fetch=bad_fetch)
    idir = env.project / wr.INSTALL_REL
    assert not (idir / "javascript").exists()
    quarantined = list((idir / ".quarantine").iterdir())
    assert len(quarantined) == 1
    assert not [p for p in idir.iterdir() if p.name.startswith(".staging-")]


def test_fetch_return_value_is_not_trusted(env):
    """A fetch that lies about the sha256 is caught by the on-disk re-hash."""
    def liar(url, size, dest):
        dest.write_bytes(b"\0" * size)
        return next(iter(env.doc["components"]["javascript"]["platforms"].values()))["sha256"]
    with pytest.raises(wr.WasmRuntimeError, match="does not match its pin"):
        wr.install(env.project, ["javascript"], fetch=liar)


# ---------------------------------------------------------------------------
# extraction refusals
# ---------------------------------------------------------------------------

def _ex_tar(tmp: Path, members, *, strip=STRIP, keep=("wasmtime", "LICENSE", "lib/")):
    a = _tar_xz(tmp / "t.tar.xz", members)
    d = tmp / "out"
    d.mkdir()
    return wr._extract(a, "tar.xz", d, strip=strip, keep=list(keep), entry="wasmtime"), d


def _ex_zip(tmp: Path, members, *, symlink=None, keep=("python.wasm", "lib/")):
    a = _zip(tmp / "z.zip", members, symlink=symlink)
    d = tmp / "out"
    d.mkdir()
    return wr._extract(a, "zip", d, strip="", keep=list(keep), entry="python.wasm"), d


@pytest.mark.parametrize("name,why", [
    ("/etc/passwd", "absolute path"),
    (f"{STRIP}/../../evil", "bad path segment"),
    (f"{STRIP}/./wasmtime", "bad path segment"),
    (f"{STRIP}\\..\\evil", "backslash"),
    ("C:/evil", "drive letter"),
])
def test_extract_refuses_unsafe_tar_names(tmp_path, name, why):
    with pytest.raises(wr.WasmRuntimeError, match=why):
        _ex_tar(tmp_path, [(name, b"x", "reg")])
    assert not (tmp_path / "evil").exists()


def test_extract_refuses_abs_path_zip(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="absolute path"):
        _ex_zip(tmp_path, [("/abs.py", b"x")])


def test_extract_refuses_dotdot_zip(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="bad path segment"):
        _ex_zip(tmp_path, [("lib/../../evil.py", b"x")])


def test_extract_refuses_backslash_zip(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="backslash"):
        _ex_zip(tmp_path, [("lib\\..\\evil.py", b"x")])


def test_extract_refuses_nul_in_zip_name(tmp_path):
    a = _zip(tmp_path / "n.zip", [("lib/a.pyQb.py", b"x")])
    a.write_bytes(a.read_bytes().replace(b"lib/a.pyQb.py", b"lib/a.py\0b.py"))  # zipfile truncates at NUL
    d = tmp_path / "out"
    d.mkdir()
    with pytest.raises(wr.WasmRuntimeError, match="NUL"):
        wr._extract(a, "zip", d, strip="", keep=["lib/"], entry="python.wasm")


def test_extract_refuses_symlink_tar(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="is a link"):
        _ex_tar(tmp_path, [(f"{STRIP}/wasmtime", "/bin/sh", "sym")])


def test_extract_refuses_hardlink_tar(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="is a link"):
        _ex_tar(tmp_path, [(f"{STRIP}/LICENSE", b"l", "reg"), (f"{STRIP}/wasmtime", f"{STRIP}/LICENSE", "lnk")])


def test_extract_refuses_symlink_zip(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="symlink"):
        _ex_zip(tmp_path, [("python.wasm", b"x")], symlink="lib/evil")


@pytest.mark.parametrize("kind", ["chr", "fifo"])
def test_extract_refuses_device(tmp_path, kind):
    with pytest.raises(wr.WasmRuntimeError, match="device/FIFO"):
        _ex_tar(tmp_path, [(f"{STRIP}/wasmtime", None, kind)])


def test_extract_refuses_duplicate_member(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="duplicate"):
        _ex_tar(tmp_path, [(f"{STRIP}/wasmtime", b"a", "reg"), (f"{STRIP}/wasmtime", b"b", "reg")])


def test_extract_refuses_too_many_members(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "MAX_MEMBERS", 3)
    with pytest.raises(wr.WasmRuntimeError, match="more than 3 members"):
        _ex_tar(tmp_path, [(f"{STRIP}/f{i}", b"x", "reg") for i in range(5)])


def test_extract_refuses_zip_bomb_ratio(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="expands more than"):
        _ex_zip(tmp_path, [("python.wasm", b"\0" * (4 << 20))])


def test_extract_refuses_oversize_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "MAX_FILE_BYTES", 10)
    with pytest.raises(wr.WasmRuntimeError, match="exceeds 10 bytes"):
        _ex_tar(tmp_path, [(f"{STRIP}/wasmtime", b"x" * 11, "reg")])


def test_extract_refuses_total_overrun(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "MAX_TOTAL_BYTES", 15)
    with pytest.raises(wr.WasmRuntimeError, match="expands past"):
        _ex_tar(tmp_path, [(f"{STRIP}/wasmtime", b"x" * 10, "reg"), (f"{STRIP}/LICENSE", b"y" * 10, "reg")])


def test_extract_refuses_member_outside_strip_prefix(tmp_path):
    with pytest.raises(wr.WasmRuntimeError, match="outside the pinned prefix"):
        _ex_tar(tmp_path, [("other-dir/wasmtime", b"x", "reg")])


def test_extract_refuses_encrypted_zip_entry(tmp_path):
    a = tmp_path / "e.zip"
    _zip(a, [("python.wasm", b"x")])
    raw = bytearray(a.read_bytes())
    # set the encryption flag bit in both the local header and the central directory entry
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        i = raw.find(sig)
        off = i + (6 if sig == b"PK\x03\x04" else 8)
        raw[off] |= 0x1
    a.write_bytes(bytes(raw))
    d = tmp_path / "out"
    d.mkdir()
    with pytest.raises(wr.WasmRuntimeError, match="encrypted"):
        wr._extract(a, "zip", d, strip="", keep=["python.wasm"], entry="python.wasm")


def test_extract_reports_skipped_members(tmp_path):
    notices, d = _ex_tar(tmp_path, [(STRIP, None, "dir"), (f"{STRIP}/wasmtime", b"w", "reg"),
                                    (f"{STRIP}/wasmtime-min", b"m", "reg")])
    assert sorted(p.name for p in d.iterdir()) == ["wasmtime"]
    assert notices and "wasmtime-min" in notices[0]   # Rule 12: never silently dropped


def test_extract_ignores_archive_modes_and_sets_exec_only_from_pins(env):
    rep = wr.install(env.project, ["wasmtime"], fetch=env.fetch)
    verdir = rep.root / "wasmtime" / "1.0.0"
    assert stat.S_IMODE(os.stat(verdir / "wasmtime").st_mode) == 0o555
    assert stat.S_IMODE(os.stat(verdir / "LICENSE").st_mode) == 0o444
    assert stat.S_IMODE(os.stat(verdir).st_mode) == 0o555
    assert not os.stat(verdir / "wasmtime").st_mode & (stat.S_ISUID | stat.S_ISGID)
    assert sorted(p.name for p in verdir.iterdir()) == ["LICENSE", "wasmtime"]
    assert any("wasmtime-min" in n and "README.md" in n for n in rep.notices)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_install_atomic_then_already_ok(env):
    rep = wr.install(env.project, ["wasmtime", "python", "javascript"], fetch=env.fetch)
    assert rep.installed == ("wasmtime", "python", "javascript") and rep.already_ok == ()
    idir = env.project / wr.INSTALL_REL
    assert (idir / ".gitignore").read_text() == "*\n"
    assert not [p for p in idir.iterdir() if p.name.startswith(".staging-")]
    info = json.loads((idir / "installed.json").read_text())
    assert info["javascript"]["version"] == "0.17.0"
    n = len(env.calls)
    rep2 = wr.install(env.project, ["wasmtime", "python", "javascript"], fetch=env.fetch)
    assert rep2.already_ok == ("wasmtime", "python", "javascript") and rep2.installed == ()
    assert len(env.calls) == n   # nothing re-downloaded
    assert wr.status(env.project) == {"wasmtime": "ok 1.0.0", "python": "ok 3.15.0", "javascript": "ok 0.17.0"}


def test_install_dry_run_writes_nothing(env):
    rep = wr.install(env.project, ["javascript"], fetch=env.fetch, dry_run=True)
    assert env.calls == [] and not (env.project / ".agentteams").exists()
    assert any("qjs.wasm" in n for n in rep.notices)


def test_install_reverify_failure_quarantines_not_deletes(env):
    wr.install(env.project, ["javascript"], fetch=env.fetch)
    f = env.project / wr.INSTALL_REL / "javascript" / "0.17.0" / "qjs-wasi.wasm"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_bytes(b"tampered")
    rep = wr.install(env.project, ["javascript"], fetch=env.fetch)
    assert rep.installed == ("javascript",) and len(rep.quarantined) == 1
    q = Path(rep.quarantined[0])
    assert (q / "qjs-wasi.wasm").read_bytes() == b"tampered"   # kept for inspection
    assert wr.resolve(env.project, "javascript") == f


def test_install_extracted_mismatch_quarantines(env):
    _rewrite_pins(env, lambda d: d["components"]["wasmtime"]["platforms"][PLAT]["files"]["LICENSE"]
                  .update(sha256="1" * 64))
    with pytest.raises(wr.WasmRuntimeError, match="extracted files fail verification"):
        wr.install(env.project, ["wasmtime"], fetch=env.fetch)
    idir = env.project / wr.INSTALL_REL
    assert not (idir / "wasmtime").exists()
    assert len(list((idir / ".quarantine").iterdir())) == 1


def test_install_refuses_symlinked_agentteams_dir(env):
    elsewhere = env.tmp / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, env.project / ".agentteams")
    with pytest.raises(wr.WasmRuntimeError, match="symlink"):
        wr.install(env.project, ["javascript"], fetch=env.fetch)
    assert list(elsewhere.iterdir()) == []


def test_install_refuses_symlinked_wasm_dir(env):
    elsewhere = env.tmp / "elsewhere"
    elsewhere.mkdir()
    (env.project / ".agentteams").mkdir()
    os.symlink(elsewhere, env.project / ".agentteams" / "wasm")
    with pytest.raises(wr.WasmRuntimeError, match="symlink"):
        wr.install(env.project, ["javascript"], fetch=env.fetch)


def test_install_refuses_tracked_install_dir(env):
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-C", str(env.project)]
    subprocess.run([*git, "init", "-q"], check=True)
    (env.project / ".agentteams" / "wasm").mkdir(parents=True)
    (env.project / ".agentteams" / "wasm" / "x").write_text("x")
    subprocess.run([*git, "add", "-f", ".agentteams/wasm/x"], check=True)
    with pytest.raises(wr.WasmRuntimeError, match="tracked by git"):
        wr.install(env.project, ["javascript"], fetch=env.fetch)


def test_install_refuses_home_and_root(env, monkeypatch):
    with pytest.raises(wr.WasmRuntimeError, match="filesystem root or home"):
        wr.install(Path("/"), ["javascript"], fetch=env.fetch)
    monkeypatch.setattr(wr.Path, "home", classmethod(lambda cls: env.project))
    with pytest.raises(wr.WasmRuntimeError, match="filesystem root or home"):
        wr.install(env.project, ["javascript"], fetch=env.fetch)


def test_install_never_touches_exec_or_scratch(env):
    wr.install(env.project, ["javascript"], fetch=env.fetch)
    idir = env.project / wr.INSTALL_REL
    for d in ("exec/run1", "scratch/agent"):
        (idir / d).mkdir(parents=True)
        (idir / d / "keep.txt").write_text("p02")
    f = idir / "javascript" / "0.17.0" / "qjs-wasi.wasm"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_bytes(b"tampered")
    wr.install(env.project, ["javascript"], fetch=env.fetch)
    assert (idir / "exec/run1/keep.txt").read_text() == "p02"
    assert (idir / "scratch/agent/keep.txt").read_text() == "p02"


# ---------------------------------------------------------------------------
# resolve / resolve_tree / status / Resolver
# ---------------------------------------------------------------------------

def test_resolve_rehashes_every_call(env):
    wr.install(env.project, ["wasmtime"], fetch=env.fetch)
    p = wr.resolve(env.project, "wasmtime")
    assert p.name == "wasmtime" and p.read_bytes() == TOOL
    os.chmod(p.parent, 0o755)
    os.chmod(p, 0o755)
    p.write_bytes(TOOL + b"!")
    with pytest.raises(wr.WasmRuntimeError, match="sha256 mismatch"):
        wr.resolve(env.project, "wasmtime")


def test_resolve_refuses_symlinked_entry(env):
    wr.install(env.project, ["wasmtime"], fetch=env.fetch)
    p = env.project / wr.INSTALL_REL / "wasmtime" / "1.0.0" / "wasmtime"
    real = env.tmp / "real-wasmtime"
    real.write_bytes(TOOL)
    os.chmod(p.parent, 0o755)
    p.unlink()
    os.symlink(real, p)
    with pytest.raises(wr.WasmRuntimeError, match="symlink"):
        wr.resolve(env.project, "wasmtime")


def test_resolve_refuses_symlinked_version_dir(env):
    wr.install(env.project, ["wasmtime"], fetch=env.fetch)
    comp = env.project / wr.INSTALL_REL / "wasmtime"
    moved = env.tmp / "moved"
    os.chmod(comp / "1.0.0", 0o755)   # a 0o555 dir cannot be moved to another parent
    os.replace(comp / "1.0.0", moved)
    os.symlink(moved, comp / "1.0.0")
    with pytest.raises(wr.WasmRuntimeError, match="symlink"):
        wr.resolve(env.project, "wasmtime")


def test_resolve_absent_names_the_fix(env):
    with pytest.raises(wr.WasmRuntimeError, match="--wasm-install"):
        wr.resolve(env.project, "javascript")


def test_resolve_unknown_component(env):
    with pytest.raises(wr.WasmRuntimeError, match="unknown WASM component"):
        wr.resolve(env.project, "ruby")


def _lib(env) -> Path:
    lib = env.project / wr.INSTALL_REL / "python" / "3.15.0" / "lib"
    for dirpath, _d, _f in os.walk(lib):
        os.chmod(dirpath, 0o755)
    return lib


def test_resolve_tree_detects_added_file(env):
    wr.install(env.project, ["python"], fetch=env.fetch)
    assert wr.resolve_tree(env.project, "python").name == "3.15.0"
    (_lib(env) / "python3.15" / "sitecustomize.py").write_text("import os\n")
    with pytest.raises(wr.WasmRuntimeError, match="does not match its pin"):
        wr.resolve_tree(env.project, "python")


def test_resolve_tree_detects_changed_file(env):
    wr.install(env.project, ["python"], fetch=env.fetch)
    f = _lib(env) / "python3.15" / "os.py"
    os.chmod(f, 0o644)
    f.write_text("# evil\n")
    with pytest.raises(wr.WasmRuntimeError, match="does not match its pin"):
        wr.resolve_tree(env.project, "python")


def test_resolve_tree_detects_symlink(env):
    wr.install(env.project, ["python"], fetch=env.fetch)
    os.symlink("/etc/passwd", _lib(env) / "python3.15" / "link.py")
    with pytest.raises(wr.WasmRuntimeError, match="not a regular file"):
        wr.resolve_tree(env.project, "python")


def test_resolve_tree_refuses_component_without_tree(env):
    wr.install(env.project, ["javascript"], fetch=env.fetch)
    with pytest.raises(wr.WasmRuntimeError, match="pins no tree"):
        wr.resolve_tree(env.project, "javascript")


def test_resolver_protocol(env):
    wr.install(env.project, ["python", "javascript"], fetch=env.fetch)
    r = wr.Resolver(env.project)
    for name in ("resolve", "pinned_version", "resolve_tree", "runtime_mounts"):
        assert callable(getattr(r, name))
    assert r.pinned_version("python") == "3.15.0"
    assert r.resolve("javascript").name == "qjs-wasi.wasm"
    mounts = r.runtime_mounts("python")
    assert mounts == ((env.project.resolve() / wr.INSTALL_REL / "python" / "3.15.0" / "lib", "/lib"),)
    assert r.runtime_mounts("javascript") == ()
    (_lib(env) / "python3.15" / "evil.py").write_text("x")
    with pytest.raises(wr.WasmRuntimeError):   # re-hashed on every call
        r.runtime_mounts("python")


def test_status_reports_mismatch(env):
    wr.install(env.project, ["javascript"], fetch=env.fetch)
    f = env.project / wr.INSTALL_REL / "javascript" / "0.17.0" / "qjs-wasi.wasm"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_bytes(b"x")
    assert wr.status(env.project)["javascript"].startswith("MISMATCH")
    assert wr.status(env.project)["wasmtime"] == "absent"


def test_status_is_network_free(env, monkeypatch):
    wr.install(env.project, ["javascript"], fetch=env.fetch)

    def no_socket(*a, **k):
        raise AssertionError("network access during status()")
    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(wr, "_fetch", no_socket)
    assert wr.status(env.project)["javascript"] == "ok 0.17.0"


# ---------------------------------------------------------------------------
# no network or install during render; only the CLI calls install / pin_update
# ---------------------------------------------------------------------------

def test_render_never_installs(tmp_path, monkeypatch):
    import build_team
    from tests._fresh_intel import seed_offline_intel

    def trap(*a, **k):
        raise AssertionError("render touched the WASM downloader / network")
    monkeypatch.setattr(wr, "_fetch", trap)
    monkeypatch.setattr(wr, "install", trap)
    monkeypatch.setattr(wr, "pin_update", trap)
    out = tmp_path / ".github" / "agents"
    flags = seed_offline_intel(out, "copilot-vscode")
    real_socket = socket.socket

    def guarded(family=socket.AF_INET, *a, **k):
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("render opened an internet socket")
        return real_socket(family, *a, **k)
    monkeypatch.setattr(socket, "socket", guarded)
    rc = build_team.main(["--description", str(REPO / "examples" / "software-project" / "brief.json"),
                          "--framework", "copilot-vscode", "--output", str(out), "--yes", "--no-scan", *flags])
    assert rc == 0
    assert not (tmp_path / ".agentteams" / "wasm").exists()


def test_only_cli_calls_install():
    pat = re.compile(r"\b(?:wasm_runtime\.)?(install|pin_update)\(")
    offenders = []
    for p in (REPO / "agentteams").rglob("*.py"):
        rel = p.relative_to(REPO).as_posix()
        if rel in ("agentteams/wasm_runtime.py", "agentteams/cli/wasm_commands.py"):
            continue
        text = p.read_text(encoding="utf-8")
        if "wasm_runtime" in text and pat.search(text):
            offenders.append(rel)
    assert offenders == []
    cli = (REPO / "agentteams/cli/wasm_commands.py").read_text()
    assert "wasm_runtime.install(" in cli and "wasm_runtime.pin_update(" in cli


def test_urllib_is_imported_lazily():
    """Importing the module must not pull in the network stack (render may import it for status)."""
    code = ("import sys; import agentteams.wasm_runtime; "
            "print(int(any(m in sys.modules for m in ('urllib.request','http.client'))))")
    out = subprocess.run([os.sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO,
                         env={**os.environ, "PYTHONPATH": str(REPO)}, check=True)
    assert out.stdout.strip() == "0"


# ---------------------------------------------------------------------------
# pin_update
# ---------------------------------------------------------------------------

NOW = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)


def _release(tag, published, assets=(), body="", draft=False, prerelease=False):
    return {"tag_name": tag, "published_at": published, "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/o/r/releases/tag/{tag}", "body": body, "assets": list(assets)}


def _asset(name, path: Path, *, digest=True):
    a = {"name": name, "size": path.stat().st_size,
         "browser_download_url": f"https://github.com/o/r/releases/download/x/{name}"}
    if digest:
        a["digest"] = "sha256:" + _sha(path.read_bytes())
    return a


def _pin_env(env, *, todo=True):
    """Pins with the wasmtime entry hashes reset to TODO-PIN, plus fake API + fetch."""
    if todo:
        _rewrite_pins(env, lambda d: d["components"]["wasmtime"]["platforms"][PLAT]["files"].update(
            {"wasmtime": {"sha256": wr.TODO_PIN, "exec": True}, "LICENSE": {"sha256": wr.TODO_PIN}}))
    wt = env.by_url[env.doc["components"]["wasmtime"]["platforms"][PLAT]["url"]]
    assets = [_asset("tool-v1.0.0-x86_64-linux.tar.xz", wt), _asset("unrelated.txt", wt)]
    files = {a["browser_download_url"]: wt for a in assets}

    def fetch(url, size, dest):
        dest.write_bytes(files[url].read_bytes())
        return _sha(files[url].read_bytes())
    return assets, fetch


def _api(release, releases):
    def get(url):
        return releases if url.endswith("per_page=100") else release
    return get


def test_pin_update_fills_todo_pins_from_verified_download(env):
    assets, fetch = _pin_env(env)
    rel = _release("v1.0.0", "2026-10-01T00:00:00Z", assets)
    diff = wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch,
                         api_get=_api(rel, [rel]), now=NOW)
    doc = json.loads(env.pins.read_text())
    pe = doc["components"]["wasmtime"]["platforms"][PLAT]
    assert pe["files"] == {"wasmtime": {"sha256": _sha(TOOL), "exec": True}, "LICENSE": {"sha256": _sha(LICENSE)}}
    assert doc["components"]["wasmtime"]["provenance"] == "download-verified"
    assert doc["components"]["wasmtime"]["s10"] == "cooldown-clear"
    assert wr.TODO_PIN in diff and "+" in diff
    assert wr.validate_pins(doc) == []


def test_pin_update_writes_only_after_verified_fetch(env):
    assets, _ = _pin_env(env)
    before = env.pins.read_bytes()

    def bad(url, size, dest):
        dest.write_bytes(b"\0" * size)
        return "0" * 64
    rel = _release("v1.0.0", "2026-10-01T00:00:00Z", assets)
    with pytest.raises(wr.WasmRuntimeError, match="does not match release metadata"):
        wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=bad, api_get=_api(rel, [rel]), now=NOW)
    assert env.pins.read_bytes() == before


def test_pin_update_refuses_missing_digest_without_tofu(env):
    assets, fetch = _pin_env(env)
    for a in assets:
        a.pop("digest")
    before = env.pins.read_bytes()
    rel = _release("v1.0.0", "2026-10-01T00:00:00Z", assets)
    with pytest.raises(wr.WasmRuntimeError, match="no sha256 digest"):
        wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch, api_get=_api(rel, [rel]), now=NOW)
    assert env.pins.read_bytes() == before
    wr.pin_update("wasmtime", "1.0.0", tofu=True, pins_path=env.pins, fetch=fetch, api_get=_api(rel, [rel]), now=NOW)
    assert json.loads(env.pins.read_text())["components"]["wasmtime"]["provenance"] == "tofu"


def test_pin_update_s10_cooldown(env):
    assets, fetch = _pin_env(env)
    young = _release("v1.0.0", "2026-10-29T00:00:00Z", assets, body="fixes GHSA-aaaa-bbbb-cccc")
    before = env.pins.read_bytes()
    with pytest.raises(wr.WasmRuntimeError, match="cooldown"):
        wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch, api_get=_api(young, [young]), now=NOW)
    assert env.pins.read_bytes() == before
    with pytest.raises(wr.WasmRuntimeError, match="not named in the release notes"):
        wr.pin_update("wasmtime", "1.0.0", remediation=["GHSA-zzzz-zzzz-zzzz"], pins_path=env.pins,
                      fetch=fetch, api_get=_api(young, [young]), now=NOW)
    with pytest.raises(wr.WasmRuntimeError, match="malformed advisory"):
        wr.pin_update("wasmtime", "1.0.0", remediation=["CVE-1"], pins_path=env.pins,
                      fetch=fetch, api_get=_api(young, [young]), now=NOW)
    wr.pin_update("wasmtime", "1.0.0", remediation=["GHSA-aaaa-bbbb-cccc"], pins_path=env.pins,
                  fetch=fetch, api_get=_api(young, [young]), now=NOW)
    assert json.loads(env.pins.read_text())["components"]["wasmtime"]["s10"] == "remediation:GHSA-aaaa-bbbb-cccc"


def test_pin_update_s10_refuses_when_a_newer_cooled_release_exists(env):
    assets, fetch = _pin_env(env)
    old = _release("v1.0.0", "2026-09-01T00:00:00Z", assets)
    newer = _release("v1.0.1", "2026-10-01T00:00:00Z")
    other_line = _release("v2.0.0", "2026-10-01T00:00:00Z")
    fresh = _release("v1.0.2", "2026-10-30T00:00:00Z")   # inside cooldown: does not count
    with pytest.raises(wr.WasmRuntimeError, match="newer: v1.0.1"):
        wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch,
                      api_get=_api(old, [old, newer, other_line, fresh]), now=NOW)
    wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch,
                  api_get=_api(old, [old, other_line, fresh]), now=NOW)


def test_pin_update_refuses_draft_and_prerelease(env):
    assets, fetch = _pin_env(env)
    for kw in ({"draft": True}, {"prerelease": True}):
        rel = _release("v1.0.0", "2026-09-01T00:00:00Z", assets, **kw)
        with pytest.raises(wr.WasmRuntimeError, match="draft or prerelease"):
            wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch, api_get=_api(rel, [rel]), now=NOW)


def test_pin_update_refuses_off_allowlist_asset_url(env):
    assets, fetch = _pin_env(env)
    assets[0]["browser_download_url"] = "https://evil.example/tool.tar.xz"
    rel = _release("v1.0.0", "2026-09-01T00:00:00Z", assets)
    with pytest.raises(wr.WasmRuntimeError, match="refusing download URL"):
        wr.pin_update("wasmtime", "1.0.0", pins_path=env.pins, fetch=fetch, api_get=_api(rel, [rel]), now=NOW)


def test_pin_update_refuses_missing_runtime_tree(env):
    py_no_lib = _zip(env.tmp / "nolib.zip", [("python.wasm", PY_WASM)])
    asset = _asset("python-3.15.0-wasi_sdk-33.zip", py_no_lib)

    def fetch(url, size, dest):
        dest.write_bytes(py_no_lib.read_bytes())
        return _sha(py_no_lib.read_bytes())
    rel = _release("v3.15.0", "2026-09-01T00:00:00Z", [asset])
    before = env.pins.read_bytes()
    with pytest.raises(wr.WasmRuntimeError, match="absent from the new archive"):
        wr.pin_update("python", "3.15.0", pins_path=env.pins, fetch=fetch, api_get=_api(rel, [rel]), now=NOW)
    assert env.pins.read_bytes() == before


def test_metadata_getter_refuses_other_hosts():
    with pytest.raises(wr.WasmRuntimeError, match="refusing metadata URL"):
        wr._api_get("https://evil.example/repos/x")
    with pytest.raises(wr.WasmRuntimeError, match="refusing metadata URL"):
        wr._api_get("http://api.github.com/repos/x")
