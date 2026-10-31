"""wasm_runtime.py — pinned provisioning and verify-on-use for the WASM runtime and guest interpreters.

The operator runs one explicit command (``agentteams --wasm-install``) that downloads wasmtime and the
guest interpreters (CPython-WASI, QuickJS-ng WASI) into ``<project>/.agentteams/wasm/``. Nothing here runs
during rendering: ``urllib`` is imported lazily inside :func:`download`, and only ``cli/wasm_commands.py``
calls :func:`install` or :func:`pin_update` (a test greps for that).

**Trust anchor.** The packaged ``agentteams/data/wasm_runtime_pins.json``, which sits inside the agentteams
install (integrity-pinned), never anything in the project tree. ``installed.json`` is informational only.
Every artifact is pinned by URL, size and sha256; extracted files are pinned by sha256 and the CPython
stdlib by a tree digest. A pin that still reads the literal ``TODO-PIN`` makes :func:`install` and
:func:`resolve` refuse that component (Rule 5, fail closed): a value is pinned only after it was read from
release metadata or computed from a verified download.

**Verify on use.** :func:`resolve` and :func:`resolve_tree` re-hash on every call. There is no size/mtime
cache: mtime is attacker-settable, and the contract is to re-hash.

**Archive safety.** Extraction is hand-rolled, member by member (no ``extractall``), so behaviour does not
depend on the Python patch release. Absolute paths, ``..``/``.`` segments, backslashes, drive letters, NUL,
links, devices, FIFOs, sparse members, encrypted or exotic zip entries, duplicates, and members outside the
pinned prefix are refused. Sizes are counted while streaming, never taken from headers.

Archive layout and download policy: plan P03 §5 (``tmp/by-week/2026-W44/wasm-sandbox/``).
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteams import wasm_pins
from agentteams._wasm_archive import (  # re-exported: one error type and one verifier for all callers
    _CHUNK,
    WasmNetworkError,
    WasmRuntimeError,
    _chmod_readonly,
    _extract,
    _hash_file_nofollow,
    _lstat_walk,
    _make_writable,
    _member_problem,
    _mkdirs,
    _verify_version_dir,
    tree_digest,
)
from agentteams.wasm_pins import is_pinned_version

#: The provisionable components, in install order.
COMPONENTS: tuple[str, ...] = ("wasmtime", "python", "javascript")

#: Install root, relative to the project root (inside the ``.agentteams`` control plane).
INSTALL_REL = ".agentteams/wasm"

#: The literal that marks a pin not yet verified. Any occurrence fails the component closed.
TODO_PIN = "TODO-PIN"

#: Expected ``schema`` value of the pins file.
PINS_SCHEMA = "agentteams-wasm-runtime-pins/v1"

#: The packaged pins file (the trust anchor).
PINS_PATH: Path = Path(__file__).resolve().parent / "data" / "wasm_runtime_pins.json"

#: Hosts a download may start on or be redirected to.
DOWNLOAD_HOSTS: frozenset[str] = frozenset({
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
})

#: The one host the pin updater reads release metadata from.
API_HOST = "api.github.com"

#: Platform keys :func:`host_platform` can return.
PLATFORMS: tuple[str, ...] = (
    "darwin-arm64", "darwin-x86_64", "linux-x86_64", "linux-aarch64", "windows-x86_64",
)

ARCHIVE_KINDS: frozenset[str] = frozenset({"tar.xz", "zip", "none"})

MAX_REDIRECTS = 5
FETCH_TIMEOUT_S = 60.0
FETCH_RETRIES = 2

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_GUEST = re.compile(r"^/lib(/[A-Za-z0-9._-]+)*$")
#: A path segment safe to join under the install dir (a version, a component).
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._+-]+$")
_SKIP_DIRS = frozenset({"exec", "scratch"})   # owned by P02 (R3/R8); install never touches them


@dataclass(frozen=True)
class InstallReport:
    """What :func:`install` did.

    Attributes:
        installed: Components newly installed.
        already_ok: Components already present and re-verified.
        quarantined: Paths moved aside under ``.quarantine/`` (never deleted).
        notices: Operator-facing notices (skipped archive members, unofficial builds, dry-run plan).
        root: The install root ``<project>/.agentteams/wasm``.
    """

    installed: tuple[str, ...]
    already_ok: tuple[str, ...]
    quarantined: tuple[str, ...]
    notices: tuple[str, ...]
    root: Path


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------

def _pins_bytes(path: Path | None = None) -> bytes:
    return (path or PINS_PATH).read_bytes()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: sha256 of the packaged pins file (P10 checks a bundle copy against it).
PINS_SHA256: str = _sha256_bytes(_pins_bytes())


def validate_pins(doc: Mapping[str, Any]) -> list[str]:
    """Check the pins document's shape; return every problem found.

    Args:
        doc: The decoded pins JSON.

    Returns:
        Problem descriptions; empty when the document is well formed. ``TODO-PIN`` values are well
        formed (they fail closed at use, not at load).

    Raises:
        Nothing: every problem is returned, not raised.
    """
    problems: list[str] = []
    if doc.get("schema") != PINS_SCHEMA:
        problems.append(f"schema is not {PINS_SCHEMA!r}")
    comps = doc.get("components")
    if not isinstance(comps, Mapping):
        return problems + ["components missing"]
    for name, entry in comps.items():
        where = f"components.{name}"
        if name not in COMPONENTS:
            problems.append(f"{where}: unknown component")
            continue
        if not is_pinned_version(entry.get("version")):
            problems.append(f"{where}.version is not a dotted version")
        src = entry.get("source") or {}
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", str(src.get("repo", ""))):
            problems.append(f"{where}.source.repo malformed")
        plats = entry.get("platforms")
        if not isinstance(plats, Mapping) or not plats:
            problems.append(f"{where}.platforms missing")
            continue
        for pkey, pe in plats.items():
            pw = f"{where}.platforms.{pkey}"
            if pkey != "any" and pkey not in PLATFORMS:
                problems.append(f"{pw}: unknown platform")
            problems.extend(_validate_platform_entry(pe, pw))
        for i, m in enumerate(entry.get("runtime_mounts", [])):
            mw = f"{where}.runtime_mounts[{i}]"
            if not _RUNTIME_GUEST.match(str(m.get("guest", ""))):
                problems.append(f"{mw}: guest must match /lib[/...]")
            for pkey, pe in plats.items():
                tree = pe.get("tree") or {}
                if tree.get("root") != m.get("tree") or "sha256" not in tree:
                    problems.append(f"{mw}: tree {m.get('tree')!r} is not pinned on {pkey}")
    return problems


def _validate_platform_entry(pe: Mapping[str, Any], pw: str) -> list[str]:
    problems: list[str] = []
    url = str(pe.get("url", ""))
    if not url.startswith("https://") or url_host(url) not in DOWNLOAD_HOSTS:
        problems.append(f"{pw}.url is not https on an allowlisted host")
    if not isinstance(pe.get("size"), int) or pe["size"] <= 0:
        problems.append(f"{pw}.size must be a positive int")
    for key, val in _digest_values(pe):
        if val != TODO_PIN and not _HEX64.match(str(val)):
            problems.append(f"{pw}.{key} is neither 64 hex chars nor {TODO_PIN}")
    if pe.get("archive") not in ARCHIVE_KINDS:
        problems.append(f"{pw}.archive must be one of {sorted(ARCHIVE_KINDS)}")
    entry = str(pe.get("entry", ""))
    if not entry or entry not in (pe.get("files") or {}):
        problems.append(f"{pw}.entry must be listed in files")
    for rel in list(pe.get("files") or {}) + [str(k) for k in pe.get("keep", [])]:
        if _member_problem(rel.rstrip("/")):
            problems.append(f"{pw}: unsafe relative path {rel!r}")
    tree = pe.get("tree")
    if tree is not None:
        cnt = tree.get("count")
        if cnt != TODO_PIN and not (isinstance(cnt, int) and cnt > 0):
            problems.append(f"{pw}.tree.count must be a positive int or {TODO_PIN}")
    return problems


def _digest_values(pe: Mapping[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = [("sha256", pe.get("sha256"))]
    for rel, spec in (pe.get("files") or {}).items():
        out.append((f"files[{rel}].sha256", spec.get("sha256")))
    if pe.get("tree") is not None:
        out.append(("tree.sha256", pe["tree"].get("sha256")))
    return out


def load_pins(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the pins file.

    Args:
        path: A pins file; ``None`` reads the packaged one.

    Returns:
        The decoded document.

    Raises:
        WasmRuntimeError: The file is unreadable, not JSON, or malformed.
    """
    try:
        doc = json.loads(_pins_bytes(path).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise WasmRuntimeError(f"cannot read WASM runtime pins: {exc}") from exc
    problems = validate_pins(doc)
    if problems:
        raise WasmRuntimeError("WASM runtime pins file is malformed: " + "; ".join(problems))
    return doc


def pinned(component: str) -> dict[str, Any]:
    """Return a copy of the packaged pins entry for ``component``.

    Args:
        component: One of :data:`COMPONENTS`.

    Returns:
        The pins entry (version, license, source, platforms, ...).

    Raises:
        WasmRuntimeError: Unknown component or malformed pins file.
    """
    check_component(component)
    return copy.deepcopy(load_pins()["components"][component])


def check_component(component: str) -> None:
    """Refuse a name that is not one of :data:`COMPONENTS`.

    Args:
        component: The candidate component name.

    Returns:
        None.

    Raises:
        WasmRuntimeError: Unknown component.
    """
    if component not in COMPONENTS:
        raise WasmRuntimeError(f"unknown WASM component {component!r}; expected one of {', '.join(COMPONENTS)}")


def host_platform() -> str:
    """Map the running host to a pins platform key (one mapping: :func:`agentteams.wasm_pins.host_platform`).

    Returns:
        One of :data:`PLATFORMS`.

    Raises:
        WasmRuntimeError: The OS/architecture pair has no pinned build.
    """
    plat = wasm_pins.host_platform()
    if plat not in PLATFORMS:
        raise WasmRuntimeError(f"no pinned WASM runtime build for this host ({plat})")
    return plat


def _platform_entry(entry: Mapping[str, Any], plat: str) -> dict[str, Any]:
    plats = entry["platforms"]
    if plat in plats:
        return dict(plats[plat])
    if "any" in plats:
        return dict(plats["any"])
    raise WasmRuntimeError(f"no pinned build for platform {plat}")


def _incomplete(pe: Mapping[str, Any]) -> list[str]:
    bad = [key for key, val in _digest_values(pe) if val == TODO_PIN]
    if pe.get("tree") is not None and pe["tree"].get("count") == TODO_PIN:
        bad.append("tree.count")
    return bad


def _refuse_incomplete(component: str, version: str, pe: Mapping[str, Any]) -> None:
    bad = _incomplete(pe)
    if bad:
        raise WasmRuntimeError(
            f"{component} {version}: pin incomplete ({', '.join(bad)} = {TODO_PIN}): run "
            f"`agentteams --wasm-pin-update {component} {version}` on a host that can download "
            "(Rule 5: no unverified hash is trusted)"
        )


def strip_for(pe: Mapping[str, Any], version: str) -> str:
    """The archive prefix a platform entry strips, with ``{version}`` filled in.

    Args:
        pe: A pins platform entry.
        version: The component version.

    Returns:
        The prefix (``""`` for none).

    Raises:
        Nothing.
    """
    return str(pe.get("strip", "")).replace("{version}", version)


# ---------------------------------------------------------------------------
# Resolve (verify on use). Every hash check is the bundle-shared ``wasm_pins.PinnedResolver`` (R10): this
# module adds only the install-dir checks, the operator-facing messages and the one error type.
# ---------------------------------------------------------------------------

def _install_dir(project_root: Path, *, create: bool = False) -> Path:
    root = Path(project_root).resolve()
    parts = INSTALL_REL.split("/")
    cur = root
    for part in parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if not create:
                raise WasmRuntimeError(
                    f"no WASM runtime installed under {root}: run `agentteams --wasm-install --project {root}`"
                ) from None
            os.mkdir(cur, 0o700)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise WasmRuntimeError(f"{cur} must be a real directory (not a symlink); refusing")
    return cur


def resolve(project_root: Path, component: str, *, platform: str | None = None) -> Path:
    """Return the verified entry file of an installed component, re-hashing it now (see :meth:`Resolver.resolve`).

    Args:
        project_root: The project root.
        component: One of :data:`COMPONENTS`.
        platform: Platform key override (tests); ``None`` uses the host.

    Returns:
        The entry file path (e.g. ``.agentteams/wasm/wasmtime/36.0.20/wasmtime``).

    Raises:
        WasmRuntimeError: Unknown component, pin incomplete, absent, symlinked, or hash mismatch; the
            message names the fix.
    """
    return Resolver(project_root, platform=platform).resolve(component)


def resolve_tree(project_root: Path, component: str, *, platform: str | None = None) -> Path:
    """Re-verify a component's pinned tree (the CPython stdlib) and its entry; return the version dir.

    Args:
        project_root: The project root.
        component: A component whose pins carry a ``tree``.
        platform: Platform key override (tests).

    Returns:
        ``.agentteams/wasm/<component>/<version>``.

    Raises:
        WasmRuntimeError: No tree pinned, pin incomplete, or any file added, changed, removed or linked.
    """
    return Resolver(project_root, platform=platform).resolve_tree(component)


def status(project_root: Path, *, platform: str | None = None) -> dict[str, str]:
    """Report each component's state. Read-only and network-free.

    Args:
        project_root: The project root.
        platform: Platform key override (tests).

    Returns:
        ``{component: "ok <ver>" | "absent" | "MISMATCH: <why>" | "pin incomplete" | "unsupported: <why>"}``.

    Raises:
        WasmRuntimeError: The packaged pins file is unreadable or malformed (a per-component failure is
            reported in the result, never raised).
    """
    out: dict[str, str] = {}
    pins = load_pins()
    for comp in COMPONENTS:
        entry = pins["components"][comp]
        version = str(entry["version"])
        try:
            pe = _platform_entry(entry, platform or host_platform())
        except WasmRuntimeError as exc:
            out[comp] = f"unsupported: {exc}"
            continue
        if _incomplete(pe):
            out[comp] = "pin incomplete"
            continue
        verdir = Path(project_root).resolve() / INSTALL_REL / comp / version
        if not os.path.lexists(verdir):
            out[comp] = "absent"
            continue
        try:
            if pe.get("tree"):
                resolve_tree(project_root, comp, platform=platform)
            else:
                resolve(project_root, comp, platform=platform)
            out[comp] = f"ok {version}"
        except WasmRuntimeError as exc:
            out[comp] = f"MISMATCH: {exc}"
    return out


class Resolver:
    """The injected resolver P02 (``WasmResolver`` protocol) and P04 receive; neither imports this module.

    A thin wrapper over :class:`agentteams.wasm_pins.PinnedResolver` (the one implementation of the hash
    checks and the lstat/symlink walk): it checks the install dir itself is a real directory, refuses a
    ``TODO-PIN`` with the pin-update hint, and turns :class:`~agentteams.wasm_pins.WasmPinError` into
    :class:`WasmRuntimeError`. The pins file is re-read and every method re-verifies on every call.

    Args:
        project_root: The project root the install lives under.
        platform: Platform key override (tests); ``None`` uses the host.
    """

    def __init__(self, project_root: Path, *, platform: str | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.platform = platform

    def _pinned(self, component: str, *, installed: bool) -> tuple[wasm_pins.PinnedResolver, dict[str, Any]]:
        check_component(component)
        pins = load_pins()
        plat = self.platform or host_platform()
        pe = _platform_entry(pins["components"][component], plat)
        if installed:   # TODO-PIN is refused before the install is looked at (Rule 5)
            _refuse_incomplete(component, str(pins["components"][component]["version"]), pe)
        idir = _install_dir(self.project_root) if installed else self.project_root / INSTALL_REL
        return wasm_pins.pinned_resolver(idir, pins, platform=plat), pe

    def _call(self, component: str, method: str, *, installed: bool = True) -> Any:
        inner = self._pinned(component, installed=installed)[0]
        try:
            return getattr(inner, method)(component)
        except (wasm_pins.WasmPinError, OSError) as exc:   # one error type for this module's callers
            hint = f" — run `agentteams --wasm-install {component} --project {self.project_root}`" if installed else ""
            raise WasmRuntimeError(f"{exc}{hint}") from exc

    def resolve(self, component: str) -> Path:
        """Verified entry file (``PinnedResolver.resolve``).

        Args:
            component: One of :data:`COMPONENTS`.

        Returns:
            The entry path.

        Raises:
            WasmRuntimeError: Unknown component, pin incomplete, absent, symlinked, or hash mismatch.
        """
        return self._call(component, "resolve")

    def pinned_version(self, component: str) -> str:
        """The pinned version string (``PinnedResolver.pinned_version``); needs no install.

        Args:
            component: One of :data:`COMPONENTS`.

        Returns:
            The version, e.g. ``"36.0.20"``.

        Raises:
            WasmRuntimeError: Unknown component, or no pins for it on this platform.
        """
        return self._call(component, "pinned_version", installed=False)

    def expected_sha256(self, component: str) -> str:
        """The pinned sha256 of the entry file (``PinnedResolver.expected_sha256``); needs no install.

        Args:
            component: One of :data:`COMPONENTS`.

        Returns:
            The lowercase hex digest.

        Raises:
            WasmRuntimeError: Unknown component, or the pin is missing or ``TODO-PIN`` (Rule 5).
        """
        return self._call(component, "expected_sha256", installed=False)

    def resolve_tree(self, component: str) -> Path:
        """Verified entry file and pinned tree (``PinnedResolver.resolve`` + ``resolve_tree``); the version dir.

        Args:
            component: A component with a pinned tree.

        Returns:
            The version directory.

        Raises:
            WasmRuntimeError: No tree pinned, pin incomplete, or any file added, changed, removed or linked.
        """
        pe = self._pinned(component, installed=False)[1]
        if not pe.get("tree"):
            raise WasmRuntimeError(f"{component} pins no tree")
        self.resolve(component)
        return self._call(component, "resolve_tree")

    def runtime_mounts(self, component: str) -> tuple[tuple[Path, str], ...]:
        """Verified ``(host_dir, guest_name)`` read-only mounts the interpreter needs (``PinnedResolver.runtime_mounts``).

        Args:
            component: One of :data:`COMPONENTS`.

        Returns:
            Mount pairs from the pins' ``runtime_mounts``; empty when none. Re-hashed every call.

        Raises:
            WasmRuntimeError: Verification failed.
        """
        mounts = self._call(component, "runtime_mounts")
        return tuple((Path(host), guest) for host, guest in mounts)


# ---------------------------------------------------------------------------
# Fetch (lazy urllib; allowlisted redirects; size-bounded streaming sha256)
# ---------------------------------------------------------------------------

def url_host(url: str) -> str:
    """The lowercased host of *url* (``""`` when it has none).

    Args:
        url: Any URL.

    Returns:
        The host name.

    Raises:
        Nothing.
    """
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


def check_download_url(url: str) -> None:
    """Refuse a download URL that is not https on :data:`DOWNLOAD_HOSTS`.

    Args:
        url: The URL to start a download on (or a redirect target).

    Returns:
        None.

    Raises:
        WasmRuntimeError: The scheme or host is not allowlisted.
    """
    if not url.startswith("https://") or url_host(url) not in DOWNLOAD_HOSTS:
        raise WasmRuntimeError(f"refusing download URL (https on {sorted(DOWNLOAD_HOSTS)} only): {url}")


def _redirect_handler_class() -> type:
    import urllib.request

    class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
        """Follow a redirect only to https on :data:`DOWNLOAD_HOSTS`, at most :data:`MAX_REDIRECTS`."""

        max_redirections = MAX_REDIRECTS

        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                             newurl: str) -> Any:
            """Refuse a redirect off the allowlist (urllib hook).

            Args:
                req: The request being redirected.
                fp: The response body.
                code: HTTP status.
                msg: Status message.
                headers: Response headers.
                newurl: Redirect target.

            Returns:
                The follow-up request.

            Raises:
                WasmRuntimeError: Target is not https on an allowlisted host.
            """
            check_download_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return _AllowlistRedirect


def _stream_to(resp: Any, size: int, dest: Path) -> str:
    """Stream ``resp`` into a new file ``dest``, aborting past ``size`` bytes; return sha256 hex.

    Raises:
        WasmRuntimeError: Size overrun or short body.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(dest, flags, 0o600)
    h = hashlib.sha256()
    n = 0
    try:
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            n += len(chunk)
            if n > size:
                raise WasmRuntimeError(f"download exceeds its pinned size {size}; aborted")
            h.update(chunk)
            os.write(fd, chunk)
    finally:
        os.close(fd)
    if n != size:
        raise WasmRuntimeError(f"download is {n} bytes, pinned size {size}")
    return h.hexdigest()


def download(url: str, size: int, dest: Path) -> str:
    """Download ``url`` into new file ``dest`` (HTTPS, allowlisted redirects, size-bounded).

    Args:
        url: An https URL on :data:`DOWNLOAD_HOSTS`.
        size: The pinned size; more bytes abort the download.
        dest: A path that must not exist yet.

    Returns:
        The sha256 hex digest of what was written.

    Raises:
        WasmRuntimeError: URL off the allowlist, redirect off the allowlist, or size mismatch.
        WasmNetworkError: Every attempt failed at the network layer.
    """
    import http.client
    import urllib.error
    import urllib.request

    check_download_url(url)
    opener = urllib.request.build_opener(_redirect_handler_class())
    last: Exception | None = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            if os.path.lexists(dest):
                os.unlink(dest)
            req = urllib.request.Request(url, headers={"User-Agent": "agentteams-wasm-install/1"})
            with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
                check_download_url(resp.geturl())
                return _stream_to(resp, size, dest)
        except urllib.error.HTTPError as exc:
            raise WasmNetworkError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            last = exc
    raise WasmNetworkError(f"{url}: failed after {FETCH_RETRIES + 1} attempts: {last}")


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _quarantine(idir: Path, path: Path, component: str) -> Path:
    qroot = _mkdirs(idir, [".quarantine"])
    target = qroot / f"{_utc_stamp()}-{component}-{secrets.token_hex(4)}"
    if path.is_dir():
        _make_writable(path)
    os.replace(path, target)
    return target


def _check_project_root(project_root: Path) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise WasmRuntimeError(f"project root {root} is not a directory")
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise WasmRuntimeError(f"refusing to install into {root} (filesystem root or home directory)")
    return root


def _refuse_tracked(root: Path) -> None:
    """Refuse when anything under ``.agentteams/wasm`` is tracked by git (it must stay untracked)."""
    try:
        from agentteams.git_exec import run_git
        proc = run_git(root, "ls-files", "--", INSTALL_REL, timeout=30, devnull_stdin=True)
    except (OSError, subprocess.TimeoutExpired):  # no git binary: nothing can be tracked
        return
    if proc.returncode == 0 and proc.stdout.strip():
        raise WasmRuntimeError(f"{INSTALL_REL} is tracked by git in {root}; untrack it before installing")


def _plan_lines(component: str, entry: Mapping[str, Any], pe: Mapping[str, Any], idir: Path) -> list[str]:
    version = entry["version"]
    lines = [
        f"{component} {version} ({entry.get('license', '?')})",
        f"  url     {pe['url']}",
        f"  size    {pe['size']}",
        f"  sha256  {pe['sha256']}",
        f"  target  {idir / component / str(version)}",
    ]
    if component == "python":
        lines.append("  note    CPython-WASI is an unofficial third-party build; trust rests on the digest pin")
    return lines


def install_plan(project_root: Path, components: Sequence[str], *, platform: str | None = None) -> list[str]:
    """Describe what :func:`install` would fetch. Network-free and write-free.

    Args:
        project_root: The project root.
        components: Components to describe.
        platform: Platform key override.

    Returns:
        Human-readable plan lines.

    Raises:
        WasmRuntimeError: Unknown component, unsupported platform, or a pin incomplete.
    """
    pins = load_pins()
    plat = platform or host_platform()
    idir = Path(project_root).resolve() / INSTALL_REL
    lines: list[str] = []
    for comp in components:
        check_component(comp)
        entry = pins["components"][comp]
        pe = _platform_entry(entry, plat)
        _refuse_incomplete(comp, entry["version"], pe)
        lines.extend(_plan_lines(comp, entry, pe, idir))
    return lines


def install(project_root: Path, components: Sequence[str], *, platform: str | None = None,
            dry_run: bool = False, fetch: Callable[[str, int, Path], str] | None = None) -> InstallReport:
    """Download, verify and install pinned components under ``<project>/.agentteams/wasm``.

    Operator-only (D4): called from ``cli/wasm_commands.py`` and nowhere else; never during render.
    Every pin is checked for ``TODO-PIN`` before any network access. The archive's size and sha256 are
    checked before extraction; extracted files are verified in staging before an atomic move into place.
    A previous install that fails re-verification is moved to ``.quarantine/``, never deleted.

    Args:
        project_root: The project root.
        components: Subset of :data:`COMPONENTS`.
        platform: Platform key override; ``None`` uses the host.
        dry_run: Return the plan in ``notices`` without network access or writes.
        fetch: ``fetch(url, size, dest) -> sha256hex`` (tests inject a local one). The result is
            re-hashed from disk regardless.

    Returns:
        The :class:`InstallReport`.

    Raises:
        WasmRuntimeError: Refusal or verification failure.
        WasmNetworkError: Download failure.
    """
    comps = list(dict.fromkeys(components))
    pins = load_pins()
    plat = platform or host_platform()
    plan = install_plan(project_root, comps, platform=plat)   # refuses TODO-PIN before any network
    root = _check_project_root(project_root)
    if dry_run:
        return InstallReport((), (), (), tuple(plan), root / INSTALL_REL)
    _refuse_tracked(root)
    idir = _install_dir(root, create=True)
    gi = idir / ".gitignore"
    if not os.path.lexists(gi):
        gi.write_text("*\n", encoding="utf-8")
    fetch = fetch or download
    installed: list[str] = []
    already: list[str] = []
    quarantined: list[str] = []
    notices: list[str] = []
    for comp in comps:
        entry = pins["components"][comp]
        version = str(entry["version"])
        pe = _platform_entry(entry, plat)
        if not SAFE_SEGMENT.match(version):
            raise WasmRuntimeError(f"{comp}: unsafe version {version!r}")
        final = idir / comp / version
        if os.path.lexists(final):
            try:
                _lstat_walk(idir, [comp, version], leaf_dir=True)
                _verify_version_dir(final, pe)
                already.append(comp)
                continue
            except WasmRuntimeError as exc:
                notices.append(f"{comp} {version}: existing install failed re-verification ({exc})")
        staging = idir / f".staging-{secrets.token_hex(8)}"
        os.mkdir(staging, 0o700)
        try:
            archive = staging / "download"
            fetch(str(pe["url"]), int(pe["size"]), archive)
            got, n = _hash_file_nofollow(archive)
            if n != pe["size"] or got != pe["sha256"]:
                q = _quarantine(idir, archive, comp)
                quarantined.append(str(q))
                raise WasmRuntimeError(
                    f"{comp} {version}: download does not match its pin (size {n}/{pe['size']}, "
                    f"sha256 {got}); kept at {q}"
                )
            tree_dir = staging / "tree"
            os.mkdir(tree_dir, 0o700)
            notices.extend(f"{comp}: {m}" for m in _extract(
                archive, str(pe["archive"]), tree_dir, strip=strip_for(pe, version),
                keep=list(pe.get("keep") or [pe["entry"]]), entry=str(pe["entry"])))
            try:
                _verify_version_dir(tree_dir, pe)
            except WasmRuntimeError as exc:
                q = _quarantine(idir, tree_dir, comp)
                quarantined.append(str(q))
                raise WasmRuntimeError(f"{comp} {version}: extracted files fail verification ({exc}); kept at {q}") from exc
            parent = _mkdirs(idir, [comp])
            if os.path.lexists(final):
                q = _quarantine(idir, final, comp)
                quarantined.append(str(q))
            os.replace(tree_dir, parent / version)
            _chmod_readonly(parent / version, pe)
            installed.append(comp)
            _record_installed(idir, comp, version, plat, str(pe["sha256"]))
        finally:
            _make_writable(staging)
            shutil.rmtree(staging, ignore_errors=True)
    return InstallReport(tuple(installed), tuple(already), tuple(quarantined), tuple(notices), idir)


def _record_installed(idir: Path, comp: str, version: str, plat: str, archive_sha: str) -> None:
    """Update the informational ``installed.json`` (never a trust anchor)."""
    path = idir / "installed.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() and not path.is_symlink() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[comp] = {"version": version, "platform": plat, "archive_sha256": archive_sha,
                  "installed_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")}
    tmp = idir / f".installed-{secrets.token_hex(4)}.json"
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Pin update (operator tool behind --wasm-pin-update; implemented in _wasm_pin_update)
# ---------------------------------------------------------------------------

def default_pins_out() -> Path | None:
    """The packaged pins file when it sits in a git work tree (an editable checkout), else ``None``.

    Returns:
        :data:`PINS_PATH` or ``None`` (a wheel install is never modified in place).

    Raises:
        Nothing.
    """
    from agentteams import _wasm_pin_update
    return _wasm_pin_update.default_pins_out()


def pin_update(component: str, version: str, *, remediation: Sequence[str] = (), tofu: bool = False,
               pins_path: Path, fetch: Callable[[str, int, Path], str] | None = None,
               api_get: Callable[[str], Any] | None = None, now: _dt.datetime | None = None,
               work_dir: Path | None = None, decision_dir: Path | None = None) -> str:
    """Re-pin ``component`` at ``version`` from a verified download; write ``pins_path``; return the diff.

    Operator-only (``--wasm-pin-update``); see :func:`agentteams._wasm_pin_update.pin_update`.

    Args:
        component: One of :data:`COMPONENTS`.
        version: Target version.
        remediation: GHSA ids justifying adoption inside the S-10 cooldown (each named by a recorded
            ``wasm-pin-remediation`` decision in ``decision_dir``).
        tofu: Accept an asset with no API digest.
        pins_path: The pins file to read and rewrite.
        fetch: Download function (tests).
        api_get: Metadata getter (tests).
        now: Clock override for S-10.
        work_dir: Scratch directory for downloads.
        decision_dir: The project root holding ``references/security-decisions.log.csv`` (S3).

    Returns:
        The unified diff.

    Raises:
        WasmRuntimeError: Refusal or mismatch; ``pins_path`` is left unchanged.
        WasmNetworkError: Metadata or download failure.
    """
    from agentteams import _wasm_pin_update
    return _wasm_pin_update.pin_update(component, version, remediation=remediation, tofu=tofu,
                                       pins_path=pins_path, fetch=fetch, api_get=api_get, now=now,
                                       work_dir=work_dir, decision_dir=decision_dir)
