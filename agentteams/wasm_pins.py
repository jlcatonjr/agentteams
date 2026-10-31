"""wasm_pins.py — a pure hash check of an installed WASM runtime against its pins (METAPLAN v2 §7 R10).

:func:`pinned_resolver` returns a resolver that satisfies ``wasm_exec.WasmResolver``: every :meth:`resolve` re-hashes
``<install_dir>/<component>/<version>/<entry>`` against the pins mapping (the parsed
``agentteams/data/wasm_runtime_pins.json``, P03), refusing symlinks anywhere below the install dir. A ``TODO-PIN``
(or any non-hex) hash fails closed (Rule 5). ``wasm_runtime.resolve()`` (P03) wraps this; the standalone
``agentteams_wasm`` bundle exec-loads this exact file, so it imports nothing from agentteams. Stdlib only.

Errors are :class:`WasmPinError`, a ``RuntimeError``, which ``wasm_exec`` reports as a refusal.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")


class WasmPinError(RuntimeError):
    """An installed component is missing, symlinked, unpinned (``TODO-PIN``) or does not match its pin."""


def host_platform() -> str:
    """The pins platform key for this host, e.g. ``darwin-arm64`` or ``linux-x86_64``.

    Returns:
        ``<os>-<arch>``.

    Raises:
        Nothing.
    """
    osname = "darwin" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else \
        "linux" if sys.platform.startswith("linux") else sys.platform
    machine = os.uname().machine if hasattr(os, "uname") else os.environ.get("PROCESSOR_ARCHITECTURE", "")
    arch = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine.lower(), machine)
    return f"{osname}-{arch}"


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(fd, 1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _walk(base: str, parts: Sequence[str]) -> str:
    """Join *parts* under *base*, lstat-checking each component; refuse a symlink or a missing path."""
    path = base
    for part in parts:
        if part in ("", ".", ".."):
            raise WasmPinError(f"path component {part!r} refused")
        path = os.path.join(path, part)
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise WasmPinError(f"{path} is a symlink; refused")
        except FileNotFoundError as exc:
            raise WasmPinError(f"{path} does not exist") from exc
    return path


def _hash_file(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise WasmPinError(f"{path} is not a regular file")
        return _sha256_fd(fd)
    finally:
        os.close(fd)


def tree_digest(top: str | Path) -> tuple[str, int]:
    """The tree digest P03 pins: sha256 over the sorted lines ``"<relpath>\\0<sha256>\\n"`` of every regular file.

    Args:
        top: The tree root.

    Returns:
        ``(hex digest, file count)``.

    Raises:
        WasmPinError: When an entry is a symlink, device, FIFO or socket.
    """
    lines = []
    for dirpath, dirnames, filenames in os.walk(str(top), followlinks=False):
        for name in [*dirnames, *filenames]:
            full = os.path.join(dirpath, name)
            mode = os.lstat(full).st_mode
            rel = os.path.relpath(full, str(top)).replace(os.sep, "/")
            if stat.S_ISREG(mode):
                lines.append(f"{rel}\0{_hash_file(full)}\n")
            elif not stat.S_ISDIR(mode):
                raise WasmPinError(f"tree entry {rel} is not a regular file or directory")
    lines.sort()
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(), len(lines)


@dataclass(frozen=True)
class PinnedResolver:
    """Checks an install dir against a pins mapping on every call (see :func:`pinned_resolver`)."""

    install_dir: str
    pins: Mapping[str, Any]
    platform: str

    def _entry(self, component: str) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
        comp = (self.pins.get("components") or {}).get(component)
        if not isinstance(comp, Mapping):
            raise WasmPinError(f"no pins for component {component!r}")
        plats = comp.get("platforms") or {}
        plat = plats.get(self.platform) or plats.get("any")
        if not isinstance(plat, Mapping):
            raise WasmPinError(f"{component}: no pins for platform {self.platform}")
        version = str(comp.get("version", ""))
        if not _VERSION.match(version):
            raise WasmPinError(f"{component}: bad pinned version {version!r}")
        return comp, plat, version

    def pinned_version(self, component: str) -> str:
        """The pinned version of *component*.

        Args:
            component: E.g. ``"wasmtime"``.

        Returns:
            The version string.

        Raises:
            WasmPinError: When the pins lack it.
        """
        return self._entry(component)[2]

    def expected_sha256(self, component: str) -> str:
        """The pinned sha256 of *component*'s entry file.

        Args:
            component: E.g. ``"wasmtime"``.

        Returns:
            The lowercase hex digest.

        Raises:
            WasmPinError: When the pin is missing or ``TODO-PIN`` (Rule 5: fail closed).
        """
        _, plat, _ = self._entry(component)
        pin = ((plat.get("files") or {}).get(plat.get("entry")) or {}).get("sha256")
        if not isinstance(pin, str) or not _HEX64.fullmatch(pin):
            raise WasmPinError(f"pin incomplete for {component} ({pin!r}): run `agentteams --wasm-pin-update "
                               f"{component} <version>` on a host that can download (Rule 5)")
        return pin

    def resolve(self, component: str) -> Path:
        """Re-hash *component*'s installed entry file and return its path.

        Args:
            component: E.g. ``"wasmtime"``.

        Returns:
            ``<install_dir>/<component>/<version>/<entry>`` (install dir realpath'd).

        Raises:
            WasmPinError: Missing, symlinked, non-regular, unpinned, or not matching its pin.
        """
        _, plat, version = self._entry(component)
        pin = self.expected_sha256(component)
        path = _walk(os.path.realpath(self.install_dir), [component, version, *str(plat.get("entry", "")).split("/")])
        try:
            got = _hash_file(path)
        except OSError as exc:
            raise WasmPinError(f"{component}: cannot read {path} ({exc.strerror}); run agentteams --wasm-install "
                               f"{component}") from exc
        if got != pin:
            raise WasmPinError(f"{component}: {path} does not match its pin (got {got[:12]}, pinned {pin[:12]})")
        return Path(path)

    def resolve_tree(self, component: str) -> Path:
        """Re-hash *component*'s pinned tree (e.g. the CPython stdlib) and return its version dir.

        Args:
            component: E.g. ``"python"``.

        Returns:
            ``<install_dir>/<component>/<version>``.

        Raises:
            WasmPinError: When the tree pin is incomplete or the tree does not match it.
        """
        _, plat, version = self._entry(component)
        tree = plat.get("tree") or {}
        pin, count = tree.get("sha256"), tree.get("count")
        if not isinstance(pin, str) or not _HEX64.fullmatch(pin) or type(count) is not int:
            raise WasmPinError(f"tree pin incomplete for {component}: run `agentteams --wasm-pin-update` (Rule 5)")
        verdir = _walk(os.path.realpath(self.install_dir), [component, version])
        top = _walk(verdir, str(tree.get("root", "")).split("/"))
        if tree_digest(top) != (pin, count):
            raise WasmPinError(f"{component}: tree {top} does not match its pin")
        return Path(verdir)

    def runtime_mounts(self, component: str) -> tuple[tuple[str, str], ...]:
        """The pinned read-only runtime mounts of *component* as ``(host, guest)``, re-verified every call.

        Args:
            component: E.g. ``"python"``.

        Returns:
            ``(<verdir>/<tree>, <guest>)`` per pins ``runtime_mounts`` entry; ``()`` when there are none.

        Raises:
            WasmPinError: As :meth:`resolve_tree`.
        """
        specs = self._entry(component)[0].get("runtime_mounts") or []
        if not specs:
            return ()
        verdir = str(self.resolve_tree(component))
        return tuple((_walk(verdir, str(s["tree"]).split("/")), str(s["guest"])) for s in specs)


def pinned_resolver(install_dir: str | Path, pins: Mapping[str, Any], *, platform: str | None = None
                    ) -> PinnedResolver:
    """A resolver that checks ``<install_dir>/<component>/<version>/<entry>`` against *pins* on every call.

    Args:
        install_dir: The install root (``<project>/.agentteams/wasm``).
        pins: The parsed ``wasm_runtime_pins.json``.
        platform: The pins platform key; default :func:`host_platform`.

    Returns:
        A :class:`PinnedResolver` (satisfies ``wasm_exec.WasmResolver``).

    Raises:
        Nothing (errors surface on use).
    """
    return PinnedResolver(str(install_dir), pins, platform or host_platform())
