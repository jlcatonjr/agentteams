"""_wasm_pin_update.py — the operator pin updater behind ``agentteams --wasm-pin-update``.

Private helper split out of ``agentteams/wasm_runtime.py`` (CH-07 module ceiling); reached through
``wasm_runtime.pin_update`` / ``wasm_runtime.default_pins_out`` and called only by ``cli/wasm_commands.py``.
It reads release metadata from api.github.com, applies S-10, and re-pins from a download verified with the
same ``download`` and safe extractor ``install`` uses. The pins file is written only after every platform verified.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import difflib
import json
import os
import re
import secrets
import shutil
import subprocess
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    version_dir_files,
)
from agentteams.wasm_pins import is_pinned_version
from agentteams.wasm_runtime import (
    API_HOST,
    FETCH_RETRIES,
    FETCH_TIMEOUT_S,
    SAFE_SEGMENT,
    check_component,
    check_download_url,
    strip_for,
    url_host,
    validate_pins,
)

#: S-10 cooldown, days.
COOLDOWN_DAYS = 14

#: The security-gate action a ``--remediation`` adoption is recorded under (S3): a PASS row in the project's
#: ``references/security-decisions.log.csv`` whose ``action_reviewed`` is ``wasm-pin-remediation`` or
#: ``wasm-pin-remediation:<component>`` and whose conditions/evidence name every GHSA id passed.
WASM_PIN_REMEDIATION = "wasm-pin-remediation"

_API_MAX_BYTES = 8 << 20
_GHSA = re.compile(r"^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$")
_GHSA_IN_TEXT = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}")
_DECISION_TEXT_FIELDS = ("action_reviewed", "scope", "conditions", "evidence", "effect_targets")


def _api_get(url: str) -> Any:
    """GET JSON from api.github.com (https, no redirects, bounded)."""
    import http.client
    import urllib.error
    import urllib.request

    if not url.startswith("https://") or url_host(url) != API_HOST:
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


def _remediation_decision(decision_dir: Path | None, component: str, remediation: Sequence[str]) -> dict[str, str]:
    """Return the recorded @security decision that clears adopting *remediation* early, or refuse (S3).

    Read-only: the gate is checked with ``check_clearance`` (HALTs, author, signature, chain); the row is
    consumed only when the pins file is written (:func:`_consume_remediation`).

    Raises:
        WasmRuntimeError: No decisions log, no unconsumed PASS row for the action, a row for another component,
            a GHSA id the row does not name, or a gate refusal.
    """
    from agentteams.cli import security_gate

    for gid in remediation:
        if not _GHSA.match(gid):
            raise WasmRuntimeError(f"malformed advisory id {gid!r}")
    hint = (f"--remediation needs a recorded @security decision: a PASS row in references/security-decisions.log.csv "
            f"with action_reviewed={WASM_PIN_REMEDIATION}:{component} naming {', '.join(remediation)} in its conditions "
            "or evidence")
    if decision_dir is None or not (decision_dir / "references" / "security-decisions.log.csv").is_file():
        raise WasmRuntimeError(f"{hint} (no decisions log under {decision_dir or '(no project)'})")
    try:
        row = security_gate._latest_security_decision(decision_dir, action=WASM_PIN_REMEDIATION)
        if row is None:
            raise WasmRuntimeError(f"{hint} (none is recorded, or the newest one is consumed)")
        with _component_suffix_is_enforced():
            allowed, reason = security_gate.check_clearance(decision_dir, action=WASM_PIN_REMEDIATION)
        if not allowed:   # HALT (C-2), unapproved author, bad signature, broken chain, unverified conditions
            raise WasmRuntimeError(f"{WASM_PIN_REMEDIATION}: the security gate refuses: {reason}")
        reviewed = (row.get("action_reviewed") or "").strip().lower()
        if reviewed not in (WASM_PIN_REMEDIATION, f"{WASM_PIN_REMEDIATION}:{component}"):
            raise WasmRuntimeError(f"{hint} (the newest {WASM_PIN_REMEDIATION} decision is {reviewed!r})")
        named = set(_GHSA_IN_TEXT.findall(" ".join(row.get(k) or "" for k in _DECISION_TEXT_FIELDS)))
        missing = [g for g in remediation if g not in named]
        if missing:
            raise WasmRuntimeError(f"{hint} (the decision does not name {', '.join(missing)})")
    except (RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, WasmRuntimeError):
            raise
        raise WasmRuntimeError(f"{WASM_PIN_REMEDIATION}: {exc}") from exc
    return row


@contextlib.contextmanager
def _component_suffix_is_enforced() -> Iterator[None]:
    """Silence the gate's W11 "the suffix restricts nothing" warning: here the ``:<component>`` suffix IS enforced
    (:func:`_remediation_decision` refuses a row naming another component)."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".*reads as a scoped clearance.*")
        yield


def _consume_remediation(decision_dir: Path) -> None:
    """Spend the remediation decision (one decision, one re-pin); a failure refuses the write."""
    from agentteams.cli import security_gate

    try:
        with _component_suffix_is_enforced():
            security_gate._assert_destructive_action_allowed(decision_dir, action=WASM_PIN_REMEDIATION, consume=True)
    except (RuntimeError, OSError, ValueError) as exc:
        raise WasmRuntimeError(f"{WASM_PIN_REMEDIATION}: {exc}") from exc


def _vtuple(tag: str) -> tuple[int, ...] | None:
    v = tag[1:] if tag.startswith("v") else tag
    return tuple(int(x) for x in v.split(".")) if is_pinned_version(v) else None


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
    for rel, p in version_dir_files(verdir, tree_root):   # the same file set install verifies
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

    Raises:
        Nothing: a missing git binary or a git timeout reads as "not a work tree".
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
               work_dir: Path | None = None, decision_dir: Path | None = None) -> str:
    """Re-pin ``component`` at ``version`` from a verified download; write ``pins_path``; return the diff.

    Operator-only (``--wasm-pin-update``). Reads release metadata from api.github.com, applies S-10,
    downloads every platform asset with :func:`wasm_runtime.download`, checks it against the API digest, extracts it
    with :func:`_extract`, and computes file and tree hashes. ``pins_path`` is written only after every
    platform verified; any failure leaves it byte-identical. Never commits.

    Args:
        component: One of :data:`COMPONENTS` (its existing entry supplies the layout templates).
        version: Target version, e.g. ``"36.0.20"``.
        remediation: GHSA ids that justify adopting a release inside the cooldown; each must be named by a
            recorded ``wasm-pin-remediation`` decision in ``decision_dir`` (consumed when the pins are written).
        tofu: Accept an asset with no API digest (recorded as ``"provenance": "tofu"``).
        pins_path: The pins file to read and rewrite.
        fetch: Download function (tests inject one).
        api_get: Metadata getter (tests inject one).
        now: Clock override for S-10.
        work_dir: Scratch directory for downloads; ``None`` makes a private temp dir.
        decision_dir: The project root whose ``references/security-decisions.log.csv`` records the remediation
            decision; required with ``remediation``.

    Returns:
        The unified diff between the old and new pins file.

    Raises:
        WasmRuntimeError: S-10 refusal, a remediation without its recorded decision, missing digest without
            ``tofu``, layout change, or mismatch.
        WasmNetworkError: Metadata or download failure.
    """
    import tempfile

    check_component(component)
    if not SAFE_SEGMENT.match(version) or not is_pinned_version(version):
        raise WasmRuntimeError(f"unsafe version {version!r}")
    if remediation:   # before any network access
        _remediation_decision(decision_dir, component, list(remediation))
    old_text = pins_path.read_text(encoding="utf-8")
    doc = json.loads(old_text)
    problems = validate_pins(doc)
    if problems:
        raise WasmRuntimeError("pins file is malformed: " + "; ".join(problems))
    entry = doc["components"][component]
    repo = entry["source"]["repo"]
    tag = str(entry["source"]["tag"]).replace("{version}", version)
    api_get = api_get or _api_get
    fetch = fetch or wr.download   # module lookup: honours a patched downloader
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
            check_download_url(url)
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
            _extract(archive, str(pe["archive"]), out, strip=strip_for(pe, version),
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
    try:
        if remediation and decision_dir is not None:   # _remediation_decision refused a None dir up front
            _consume_remediation(decision_dir)
        os.replace(tmp, pins_path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    return "".join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        fromfile=f"a/{pins_path.name}", tofile=f"b/{pins_path.name}"))
