"""_wasm_pin_update.py — the operator pin updater behind ``agentteams --wasm-pin-update``.

Private helper split out of ``agentteams/wasm_runtime.py`` (CH-07 module ceiling); reached through
``wasm_runtime.pin_update`` / ``wasm_runtime.default_pins_out`` and called only by ``cli/wasm_commands.py``.
It reads release metadata from api.github.com, applies S-10, and re-pins from a download verified with the
same ``_fetch`` and safe extractor ``install`` uses. The pins file is written only after every platform verified.
"""

from __future__ import annotations

import copy
import datetime as _dt
import difflib
import json
import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agentteams import wasm_runtime as wr
from agentteams._wasm_archive import (
    _CHUNK,
    WasmNetworkError,
    WasmRuntimeError,
    _extract,
    _hash_file_nofollow,
    _make_writable,
    tree_digest,
)
from agentteams.wasm_runtime import (
    API_HOST,
    FETCH_RETRIES,
    FETCH_TIMEOUT_S,
    _check_component,
    _check_download_url,
    _host,
    _SAFE_SEGMENT,
    _strip_for,
    _VERSION,
    validate_pins,
)

#: S-10 cooldown, days.
COOLDOWN_DAYS = 14

_API_MAX_BYTES = 8 << 20
_GHSA = re.compile(r"^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$")



def _api_get(url: str) -> Any:
    """GET JSON from api.github.com (https, no redirects, bounded)."""
    import http.client
    import urllib.error
    import urllib.request

    if not url.startswith("https://") or _host(url) != API_HOST:
        raise WasmRuntimeError(f"refusing metadata URL: {url}")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
            raise WasmRuntimeError(f"refusing redirect from {url} to {newurl}")

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "agentteams-wasm-pin-update/1"})
    last: Exception | None = None
    for _attempt in range(FETCH_RETRIES + 1):
        try:
            parts: list[bytes] = []
            total = 0
            with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
                while True:   # read to EOF: a single bounded read() can return a short body
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _API_MAX_BYTES:
                        raise WasmRuntimeError(f"{url}: response exceeds {_API_MAX_BYTES} bytes")
                    parts.append(chunk)
            return json.loads(b"".join(parts).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise WasmNetworkError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError,
                ValueError) as exc:
            last = exc   # transient (a truncated chunked body, a cut-off JSON document): retry
    raise WasmNetworkError(f"{url}: failed after {FETCH_RETRIES + 1} attempts: {last}")


def _vtuple(tag: str) -> tuple[int, ...] | None:
    v = tag[1:] if tag.startswith("v") else tag
    return tuple(int(x) for x in v.split(".")) if _VERSION.match(v) else None


def _parse_time(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _s10_check(release: Mapping[str, Any], releases: Sequence[Mapping[str, Any]], version: str,
               remediation: Sequence[str], now: _dt.datetime) -> str:
    """Apply S-10; return the ``s10`` pins value.

    Raises:
        WasmRuntimeError: Draft/prerelease, cooldown not cleared, a newer cleared release exists, or a
            remediation id is malformed or absent from the line's release notes.
    """
    if release.get("draft") or release.get("prerelease"):
        raise WasmRuntimeError(f"{release.get('tag_name')} is a draft or prerelease")
    target = _vtuple(version)
    if target is None:
        raise WasmRuntimeError(f"unparseable version {version!r}")
    stable = [r for r in releases if not r.get("draft") and not r.get("prerelease")
              and (_vtuple(str(r.get("tag_name", ""))) or (None,))[0] == target[0]]
    if remediation:
        bodies = "\n".join(str(r.get("body") or "") for r in stable
                           if (_vtuple(str(r.get("tag_name", ""))) or ()) <= target)
        bodies += "\n" + str(release.get("body") or "")
        for gid in remediation:
            if not _GHSA.match(gid):
                raise WasmRuntimeError(f"malformed advisory id {gid!r}")
            if gid not in bodies:
                raise WasmRuntimeError(f"{gid} is not named in the release notes up to {version}")
        return "remediation:" + ",".join(remediation)
    cutoff = now - _dt.timedelta(days=COOLDOWN_DAYS)
    if _parse_time(str(release["published_at"])) > cutoff:
        raise WasmRuntimeError(
            f"{version} was published {release['published_at']}, inside the {COOLDOWN_DAYS}-day S-10 cooldown; "
            "pass --remediation GHSA-... (recorded @security decision) to adopt a fix release early"
        )
    newer = [str(r["tag_name"]) for r in stable
             if (_vtuple(str(r["tag_name"])) or ()) > target and _parse_time(str(r["published_at"])) <= cutoff]
    if newer:
        raise WasmRuntimeError(f"{version} is not the newest cooled-down release on its line (newer: {', '.join(newer)})")
    return "cooldown-clear"


def _hash_extracted(verdir: Path, pe: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    old_files = pe.get("files") or {}
    tree = pe.get("tree")
    tree_root = str(tree["root"]) if tree else None
    files: dict[str, Any] = {}
    for dirpath, dirnames, filenames in os.walk(verdir, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(verdir).as_posix()
            if tree_root and rel.startswith(tree_root + "/"):
                continue
            spec: dict[str, Any] = {"sha256": _hash_file_nofollow(p)[0]}
            if (old_files.get(rel) or {}).get("exec"):
                spec["exec"] = True
            files[rel] = spec
    new_tree = None
    if tree:
        root = verdir / tree_root
        if not root.is_dir() or root.is_symlink():
            raise WasmRuntimeError(f"pinned tree {tree_root}/ is absent from the new archive")
        digest, count = tree_digest(root)
        new_tree = {"root": tree_root, "sha256": digest, "count": count}
    return files, new_tree


def default_pins_out() -> Path | None:
    """The packaged pins file when it sits in a git work tree (an editable checkout), else ``None``.

    Returns:
        :data:`wasm_runtime.PINS_PATH` or ``None`` (a wheel install is never modified in place).
    """
    try:
        from agentteams.git_exec import run_git
        proc = run_git(wr.PINS_PATH.parent, "rev-parse", "--is-inside-work-tree", timeout=30, devnull_stdin=True)
    except (OSError, subprocess.TimeoutExpired):  # no git binary: not a work tree
        return None
    return wr.PINS_PATH if proc.returncode == 0 and proc.stdout.strip() == "true" else None


def pin_update(component: str, version: str, *, remediation: Sequence[str] = (), tofu: bool = False,
               pins_path: Path, fetch: Callable[[str, int, Path], str] | None = None,
               api_get: Callable[[str], Any] | None = None, now: _dt.datetime | None = None,
               work_dir: Path | None = None) -> str:
    """Re-pin ``component`` at ``version`` from a verified download; write ``pins_path``; return the diff.

    Operator-only (``--wasm-pin-update``). Reads release metadata from api.github.com, applies S-10,
    downloads every platform asset with :func:`_fetch`, checks it against the API digest, extracts it
    with :func:`_extract`, and computes file and tree hashes. ``pins_path`` is written only after every
    platform verified; any failure leaves it byte-identical. Never commits.

    Args:
        component: One of :data:`COMPONENTS` (its existing entry supplies the layout templates).
        version: Target version, e.g. ``"36.0.20"``.
        remediation: GHSA ids that justify adopting a release inside the cooldown.
        tofu: Accept an asset with no API digest (recorded as ``"provenance": "tofu"``).
        pins_path: The pins file to read and rewrite.
        fetch: Download function (tests inject one).
        api_get: Metadata getter (tests inject one).
        now: Clock override for S-10.
        work_dir: Scratch directory for downloads; ``None`` makes a private temp dir.

    Returns:
        The unified diff between the old and new pins file.

    Raises:
        WasmRuntimeError: S-10 refusal, missing digest without ``tofu``, layout change, or mismatch.
        WasmNetworkError: Metadata or download failure.
    """
    import tempfile

    _check_component(component)
    if not _SAFE_SEGMENT.match(version) or not _VERSION.match(version):
        raise WasmRuntimeError(f"unsafe version {version!r}")
    old_text = pins_path.read_text(encoding="utf-8")
    doc = json.loads(old_text)
    problems = validate_pins(doc)
    if problems:
        raise WasmRuntimeError("pins file is malformed: " + "; ".join(problems))
    entry = doc["components"][component]
    repo = entry["source"]["repo"]
    tag = str(entry["source"]["tag"]).replace("{version}", version)
    api_get = api_get or _api_get
    fetch = fetch or wr._fetch   # module lookup: honours a patched downloader
    now = now or _dt.datetime.now(_dt.UTC)
    release = api_get(f"https://{API_HOST}/repos/{repo}/releases/tags/{tag}")
    releases = api_get(f"https://{API_HOST}/repos/{repo}/releases?per_page=100")
    s10 = _s10_check(release, releases, version, list(remediation), now)
    assets = release.get("assets") or []
    new_entry = copy.deepcopy(entry)
    new_entry.update({"version": version, "release": str(release.get("html_url", "")),
                      "published_at": str(release.get("published_at", "")), "s10": s10,
                      "provenance": "tofu" if tofu else "download-verified"})
    owned_tmp = work_dir is None
    work = Path(tempfile.mkdtemp(prefix="agentteams-pin-")) if owned_tmp else work_dir
    try:
        for pkey, pe in entry["platforms"].items():
            pat = re.compile(str(pe["asset_re"]).replace("{version}", re.escape(version)))
            match = [a for a in assets if pat.match(str(a.get("name", "")))]
            if len(match) != 1:
                raise WasmRuntimeError(f"{component} {version} {pkey}: expected one asset matching "
                                       f"{pat.pattern}, found {len(match)}")
            asset = match[0]
            url, size = str(asset["browser_download_url"]), int(asset["size"])
            _check_download_url(url)
            digest = str(asset.get("digest") or "")
            if digest.startswith("sha256:"):
                digest = digest[len("sha256:"):]
            elif not tofu:
                raise WasmRuntimeError(f"{asset['name']} has no sha256 digest in release metadata; "
                                       "re-run with --tofu only on a recorded @security decision")
            else:
                digest = ""
            pdir = work / f"{component}-{pkey}"
            os.mkdir(pdir, 0o700)
            archive = pdir / "download"
            fetch(url, size, archive)
            got, n = _hash_file_nofollow(archive)
            if n != size or (digest and got != digest):
                raise WasmRuntimeError(f"{asset['name']}: download does not match release metadata "
                                       f"(size {n}/{size}, sha256 {got} vs {digest or '(none)'})")
            out = pdir / "x"
            os.mkdir(out, 0o700)
            _extract(archive, str(pe["archive"]), out, strip=_strip_for(pe, version),
                     keep=list(pe.get("keep") or [pe["entry"]]), entry=str(pe["entry"]))
            files, tree = _hash_extracted(out, pe)
            if pe["entry"] not in files:
                raise WasmRuntimeError(f"{asset['name']}: entry {pe['entry']!r} absent after extraction")
            npe = new_entry["platforms"][pkey]
            npe.update({"url": url, "size": size, "sha256": got, "files": files})
            if tree is not None:
                npe["tree"] = tree
    finally:
        if owned_tmp:
            _make_writable(work)
            shutil.rmtree(work, ignore_errors=True)
    doc["components"][component] = new_entry
    doc["updated"] = now.date().isoformat()
    problems = validate_pins(doc)
    if problems:
        raise WasmRuntimeError("updated pins would be malformed: " + "; ".join(problems))
    new_text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    tmp = pins_path.with_name(f".{pins_path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, pins_path)
    return "".join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        fromfile=f"a/{pins_path.name}", tofile=f"b/{pins_path.name}"))
