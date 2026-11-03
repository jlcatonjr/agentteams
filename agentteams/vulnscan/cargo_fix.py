"""``stage-cargo``: in-major ``Cargo.lock`` fixes with ``cargo update --precise`` (vfr-d.D3, operator decision OD9).

Until D3 a ``Cargo.lock`` was plan-only (:data:`.manifests.PLAN_ONLY_LOCKS`): the fix plan printed
``cargo update -p <name>@<installed> --precise <version>`` for a person. ``stage-cargo`` is that command run by the
executor, for one class of fix only (``@security`` 2026-11-02, conditions in brackets):

- **Never a yanked release**: before cargo runs, each target's crates.io sparse-index entry is read inside the
  same confinement and egress (:func:`fetch_index`); a yanked, missing or unreadable entry refuses
  (:func:`yanked_problem`), since cargo 1.77+ lets ``--precise`` pick a yanked version.
- **In the current major line** (Cargo's caret rule: ``0.13.3`` may become ``0.13.x``, ``7.2.4`` may become
  ``7.x``): the target is the smallest version the advisories name as ``fixed`` that is above the installed one and
  in its line (:func:`in_major_target`). Anything else stays plan-only, or is ``stage-major`` work.
- **S-10 search on the target** [S-10 search]: OSV is queried for the exact target release; any advisory against
  it, a malicious one above all, means no stage. That is also what decides whether an in-major release fixes
  *every* advisory of the row, not only the one whose range names it [the Rule 14 check, done mechanically].
- **crates.io only** [crates.io only]: a lockfile with a git or alternate-registry ``[[package]]`` is refused, and
  cargo's only egress is ``index.crates.io:443`` (:func:`.subproc_confine.run_cargo`): a ``[patch]`` or path
  outside the copied tree fails the run, never widens it.
- **Cargo.toml unchanged; no new [[package]]** [Cargo.toml unchanged]: cargo runs on a scratch copy holding only
  the tree's ``Cargo.toml`` files, its ``Cargo.lock`` and empty ``.rs`` stubs (no source, no ``.cargo`` config, an
  empty ``CARGO_HOME``), in the tools container only, and only ``Cargo.lock`` is copied back. The result must
  differ from the base only in the ``version`` and ``checksum`` lines of exactly the planned packages and in the
  ``"name version"`` references to them (:func:`check_lock_edit`, ``planned``); the executor's ``validate_diff``
  runs the same check without the plan and accepts no other file in this mode.

``cargo update`` reads the index and runs no build script. Nothing here builds or tests: CI does, on the PR.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from agentteams.vulnscan import osv, project_policy, subproc_confine, writer_prs
from agentteams.vulnscan.executor_env import ExecError
from agentteams.vulnscan.manifests import CRATE_NAME, CRATE_VERSION, CRATES_IO_SOURCES, discover
from agentteams.vulnscan.npm_ops import semver_line
from agentteams.vulnscan.versions import is_final, version_key

LOCK = "Cargo.lock"
STAGE = "stage-cargo"   # the op, and the plan row action that names it
#: ``stage-cargo``'s futile result (held per op by :mod:`.retry_guard`).
FUTILE_DETAIL = "no Cargo.lock crate stage-cargo may update"
#: At most this many candidate fixed versions are S-10-searched per crate.
MAX_CANDIDATES = 5
#: The scratch copy refuses a tree with more files than this, or a Cargo.toml larger than :data:`MAX_TOML_BYTES`.
MAX_TREE_FILES = 5000
MAX_TOML_BYTES = 1_000_000
_SKIP_DIRS = frozenset({".git", ".cargo", "target", "node_modules", ".venv"})
_FIELD = re.compile(r'^(name|version|source|checksum) = "([^"\n]*)"\r?\n?$')
_DEP = re.compile(r'^ "([A-Za-z0-9_-]+)(?: ([0-9][0-9A-Za-z.+-]*))?(?: \(([^)\n]*)\))?",\r?\n?$')
_CHECKSUM = re.compile(r"[0-9a-f]{64}")
INDEX_URL = "https://index.crates.io/"
#: A crate's sparse-index file larger than this is not read (the index is remote content: refuse, never truncate).
MAX_INDEX_BYTES = 4_000_000
#: Fetches one URL inside the tools container (its image has Python; the egress proxy allows only the index).
_FETCH = ("import sys,urllib.request\nr=urllib.request.urlopen(sys.argv[1],timeout=60)\n"
          "sys.stdout.write(r.read(int(sys.argv[2])+1).decode('utf-8','replace'))")


def in_major_target(name: str, installed: str, ids: list[str]) -> tuple[str, str]:
    """Return the smallest S-10-clean fixed version in ``installed``'s caret line, and why not when there is none.

    Args:
        name: The crate.
        installed: Its locked version.
        ids: The advisories against it.

    Returns:
        ``(target, "")`` or ``("", reason)``.

    Raises:
        FeedError: OSV could not be read.
    """
    found: set[str] = set()
    for vid in ids:
        if osv.is_malicious(vid):
            return "", "malicious version installed: an incident, not an update"
        intervals = osv.affected_intervals(osv.details(vid), "crates.io", name)
        if intervals is None:
            return "", f"advisory {vid[:40]} cannot be judged"
        found |= {i.hi for i in intervals if i.fixed and i.hi}
    line = semver_line(installed)
    cands = sorted((v for v in found if CRATE_VERSION.fullmatch(v) and is_final(v) and semver_line(v) == line
                    and version_key(v) > version_key(installed)), key=version_key)
    for target in cands[:MAX_CANDIDATES]:
        bad = osv.vulnerable_ids([("crates.io", name, target)])   # Rule S-10 on the exact release adopted
        if any(osv.is_malicious(i) for ids_ in bad.values() for i in ids_):
            return "", f"{target} is reported malicious"
        if not bad:
            return target, ""
    if not cands:
        return "", "no fixed version in the current major line (stage-major work, or stays known)"
    return "", f"every in-major fixed version still has advisories ({', '.join(cands[:MAX_CANDIDATES])}; S-10)"


def sources_problem(text: str) -> str:
    """Return why ``stage-cargo`` refuses a lockfile's sources, or "" (crates.io and local path packages only).

    Args:
        text: The Cargo.lock text.

    Returns:
        The refusal (not valid TOML, or a git or alternate-registry ``[[package]]``), or "".
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return f"Cargo.lock is not valid TOML ({str(exc)[:80]})"
    for pkg in data.get("package") or []:
        source = pkg.get("source") if isinstance(pkg, dict) else "?"
        if source is not None and not (isinstance(source, str) and source.startswith(CRATES_IO_SOURCES)):
            return f"a git or alternate-registry package ({str(pkg.get('name', '?'))[:60]}): crates.io only"
    return ""


def annotate(path: Path, rows: list[Any]) -> None:
    """Turn the ``plan-only`` rows ``stage-cargo`` would take into ``stage-cargo`` rows (their target under "→").

    Args:
        path: The Cargo.lock.
        rows: :func:`.fix.plan_cargo_lock`'s rows for it.

    Raises:
        FeedError: OSV could not be read.
    """
    problem = sources_problem(path.read_text(encoding="utf-8", errors="replace"))
    for c in rows:
        if c.action != "plan-only" or not (CRATE_NAME.fullmatch(c.name) and CRATE_VERSION.fullmatch(c.old)):
            continue
        target, why = ("", problem) if problem else in_major_target(c.name, c.old, c.ids)
        if target:
            c.action, c.new = STAGE, target
            c.note = (f"{STAGE}: `cargo update -p {c.name}@{c.old} --precise {target}` (in the current major line; "
                      f"S-10 search clean; {len(c.ids)} advisories)")
        else:
            c.note += f"; not {STAGE}: {why}"


def index_path(name: str) -> str:
    """Return a crate's file in crates.io's sparse index (``1/a``, ``2/ab``, ``3/a/abc``, ``se/rd/serde``).

    Args:
        name: A crate name (:data:`.manifests.CRATE_NAME`).

    Returns:
        The path below :data:`INDEX_URL`.
    """
    n = name.lower()
    return f"{len(n)}/{n}" if len(n) < 3 else f"3/{n[0]}/{n}" if len(n) == 3 else f"{n[:2]}/{n[2:4]}/{n}"


def fetch_index(name: str) -> str:
    """Return a crate's sparse-index file, fetched in the tools container through the index-only egress.

    Args:
        name: A crate name.

    Returns:
        The file's text (JSON lines, one per published version).

    Raises:
        ExecError: The fetch failed, or the file is larger than :data:`MAX_INDEX_BYTES`.
    """
    proc = subproc_confine.run_confined("crates-index", ["python3", "-I", "-c", _FETCH, INDEX_URL + index_path(name),
                                                         str(MAX_INDEX_BYTES)],
                                        egress=(subproc_confine.CARGO_ENDPOINT,), env={}, timeout=120)
    text = proc.stdout if isinstance(proc.stdout, str) else ""
    if proc.returncode or len(text.encode("utf-8", "replace")) > MAX_INDEX_BYTES:
        raise ExecError(f"{STAGE}: the crates.io index for {name[:64]} could not be read; nothing is staged")
    return text


def yanked_problem(name: str, version: str, text: str) -> str:
    """Return why ``version`` of ``name`` may not be the target in its sparse-index file ``text``, or "".

    cargo 1.77 and later lets ``--precise`` select a yanked release, so the executor checks first.

    Args:
        name: The crate.
        version: The target version.
        text: :func:`fetch_index`'s result (untrusted).

    Returns:
        ``yanked``, ``not in the index`` or an unreadable-line refusal; "" for a published, unyanked release.
    """
    try:   # every line first: one unreadable line anywhere refuses (the file is read whole, never in part)
        entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        return f"{name}: the crates.io index has an unreadable line; refused"
    found = [e for e in entries if isinstance(e, dict) and str(e.get("name")).lower() == name.lower()
             and e.get("vers") == version]
    if not found:
        return f"{name} {version} is not in the crates.io index; refused"
    # only an explicit "yanked": false counts as published (a missing flag refuses)
    return "" if all(e.get("yanked") is False for e in found) else f"{name} {version} is yanked on crates.io; refused"


def _blocks(lines: list[str]) -> list[dict[str, Any]]:
    """Return each ``[[package]]`` block's line range and fields (``name``, ``version``, ``source``)."""
    out: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "[[package]]":
            out.append({"start": i, "end": len(lines)})
            if len(out) > 1:
                out[-2]["end"] = i
        elif out and (m := _FIELD.match(line)):
            out[-1].setdefault(m[1], m[2])
    return out


def check_lock_edit(rel: str, old: bytes, new: bytes, *, planned: dict[tuple[str, str], str] | None = None) -> None:
    """Refuse a ``Cargo.lock`` edit that is anything but in-major crates.io version moves (``stage-cargo``).

    The line count must be unchanged (no new or removed ``[[package]]`` or dependency). A changed line must be a
    crates.io package's ``version`` (a final release above the old one, in its caret line) or its ``checksum``
    (64 hex digits, only beside a version move), or a ``"name version"`` dependency reference following such a
    move. Every other byte is unchanged. With ``planned`` the moves must be exactly those ``{(name, old): new}``.

    Args:
        rel: Path for messages.
        old: The base lockfile (``b""`` for a new one: refused).
        new: The lockfile now.
        planned: The moves :func:`fix_result` asked cargo for, or None (``validate_diff``: any in-major move).

    Raises:
        ValueError: Anything else changed.
    """
    a = old.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    b = new.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    if not a or len(a) != len(b):
        raise ValueError(f"{rel}: lines were added or removed (a new [[package]] or dependency): refused")
    blocks = _blocks(a)
    moves: dict[tuple[str, str], str] = {}
    checksums: list[tuple[int, dict[str, Any]]] = []
    deps: list[tuple[str, str]] = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        blk = next((k for k in blocks if k["start"] < i < k["end"]), None)
        mx, my = _FIELD.match(x), _FIELD.match(y)
        dx, dy = _DEP.match(x), _DEP.match(y)
        if blk and mx and my and mx[1] == my[1] == "version":
            src, name = str(blk.get("source", "")), str(blk.get("name", ""))
            if not (src.startswith(CRATES_IO_SOURCES) and CRATE_NAME.fullmatch(name) and blk.get("version") == mx[2]
                    and CRATE_VERSION.fullmatch(my[2]) and is_final(my[2])
                    and version_key(my[2]) > version_key(mx[2]) and semver_line(my[2]) == semver_line(mx[2])):
                raise ValueError(f"{rel}: {name[:60]} {mx[2][:40]} -> {my[2][:40]} is not an in-major crates.io "
                                 "upgrade to a final release")
            moves[(name, mx[2])] = my[2]
            blk["moved"] = True
        elif blk and mx and my and mx[1] == my[1] == "checksum" and _CHECKSUM.fullmatch(my[2]):
            checksums.append((i, blk))
        elif dx and dy and dx[1] == dy[1] and dx[2] and dy[2] and dx[3] == dy[3]:
            deps.append((dx[1], f"{dx[2]}>{dy[2]}"))
        else:
            raise ValueError(f"{rel}:{i + 1}: only version, checksum and dependency-version lines may change")
    if any(not blk.get("moved") for _i, blk in checksums):
        raise ValueError(f"{rel}: a checksum changed without its package's version")
    for name, move in deps:
        was, now = move.split(">", 1)
        if moves.get((name, was)) != now:
            raise ValueError(f"{rel}: a dependency reference to {name[:60]} changed without that package moving")
    if planned is not None and moves != planned:
        raise ValueError(f"{rel}: cargo moved {sorted(moves)} but stage-cargo asked for {sorted(planned)}")


def _scratch_tree(src: Path, dst: Path) -> None:
    """Copy ``src``'s ``Cargo.toml`` files and ``Cargo.lock`` into ``dst``, with an empty stub for every ``.rs``.

    Raises:
        ExecError: The tree has too many files or an oversized Cargo.toml.
    """
    count = 0
    for top, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not (Path(top) / d).is_symlink()]
        rel = Path(top).relative_to(src)
        for f in files:
            p = Path(top) / f
            if p.is_symlink() or not (f in ("Cargo.toml", LOCK) or f.endswith(".rs")):
                continue
            count += 1
            if count > MAX_TREE_FILES:
                raise ExecError(f"{STAGE}: the crate tree has more than {MAX_TREE_FILES} files; fix it by hand")
            (dst / rel).mkdir(parents=True, exist_ok=True)
            if f.endswith(".rs"):
                (dst / rel / f).write_bytes(b"")   # cargo needs targets to exist, never their source
            elif p.stat().st_size > MAX_TOML_BYTES:
                raise ExecError(f"{STAGE}: {rel / f} is larger than {MAX_TOML_BYTES} bytes")
            else:
                shutil.copyfile(p, dst / rel / f)


def run_updates(lock: Path, moves: dict[tuple[str, str], str]) -> str:
    """Run ``cargo update -p <name>@<old> --precise <new>`` for each move on a scratch copy; write back the lockfile.

    Args:
        lock: The worktree's Cargo.lock (a ``Cargo.toml`` beside it).
        moves: ``{(name, old): new}``.

    Returns:
        cargo's output.

    Raises:
        ExecError: Not in the tools container, a target is yanked or its index unreadable (:func:`yanked_problem`),
            cargo failed, or the result breaks :func:`check_lock_edit`.
    """
    if subproc_confine.active() is None:   # like stage-major's npm install: never on the host
        raise ExecError(f"{STAGE} refuses to run cargo outside the tools container (confinement is off or opted out)")
    if not (lock.parent / "Cargo.toml").is_file() or lock.is_symlink():
        raise ExecError(f"{STAGE}: no Cargo.toml beside {lock.name}, or the lockfile is a symlink")
    for (name, _was), now in sorted(moves.items()):   # fail closed: an unreadable index refuses as well
        if why := yanked_problem(name, now, fetch_index(name)):
            raise ExecError(f"{STAGE}: {why}")
    old = lock.read_bytes()
    out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentteams-cargo-", dir=subproc_confine.scratch_parent()) as tmp_name:
        tmp = Path(tmp_name).resolve()
        work, home = tmp / "ws", tmp / "cargo-home"
        work.mkdir()
        home.mkdir()
        _scratch_tree(lock.parent, work)
        env = {"CARGO_HOME": str(home), "CARGO_REGISTRIES_CRATES_IO_PROTOCOL": "sparse", "CARGO_TERM_COLOR": "never",
               "CARGO_NET_GIT_FETCH_WITH_CLI": "false", "CARGO_HTTP_TIMEOUT": "60"}
        for (name, was), now in sorted(moves.items()):
            proc = subproc_confine.run_cargo(["update", "-p", f"{name}@{was}", "--precise", now], workdir=work,
                                             scratch_root=tmp, env=env)
            out.append((proc.stdout or "") + (proc.stderr or ""))
            if proc.returncode:
                raise ExecError(f"{STAGE}: cargo update -p {name}@{was} --precise {now} failed: "
                                f"{out[-1].strip()[-300:]}")
        new = (work / LOCK).read_bytes()
    try:
        check_lock_edit(LOCK, old, new, planned=moves)
    except ValueError as exc:
        raise ExecError(f"{STAGE} refuses cargo's lockfile: {exc}") from exc
    with open(os.open(lock, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW), "wb") as fh:
        fh.write(new)
    return "\n".join(out)


def fix_result(wt: Path, claims: dict[str, list[int]]) -> Any:
    """Make ``stage-cargo``'s change in the executor's worktree and describe it as a :class:`.fix.FixResult`.

    Args:
        wt: The executor's worktree.
        claims: :func:`.writer_prs.staging_claims`' result (rows another open PR updates are ``pr-held``).

    Returns:
        The result (``resolved``: advisory occurrences removed).

    Raises:
        ExecError: Nothing to update (:data:`FUTILE_DETAIL`), or a run failed or was refused.
    """
    from agentteams.vulnscan import _http, fix   # lazy: fix imports this module

    res = fix.FixResult(applied=True, resolved=0)
    policies = project_policy.PolicyCache()
    for lock in (p for p in discover(wt) if p.name == LOCK):
        rel = str(lock.relative_to(wt))
        repo = project_policy.repo_dir(lock, wt)
        pol = policies.get(repo)
        if pol.level == "off" or project_policy.is_excluded(lock.relative_to(repo).as_posix(), pol.exclude_paths):
            continue
        try:
            rows = fix.plan_cargo_lock(lock, label=rel, private=pol.private_packages)
        except _http.FeedError as exc:
            res.feed_errors.append(f"{rel}: {exc}")
            continue
        writer_prs.leave_to_prs([c for c in rows if c.action == STAGE], claims)
        res.changes += rows
        moves = {(c.name, c.old): c.new for c in rows if c.action == STAGE}
        if not moves:
            continue
        old = lock.read_bytes()
        run_updates(lock, moves)
        for c in rows:
            if (c.name, c.old) in moves:
                c.action = "fix"
        fix._add_resolved(res, lock, old, rel, pol.private_packages)
    if not res.feed_errors and not any(c.action in ("fix", "pr-held") for c in res.changes):
        raise ExecError(f"{FUTILE_DETAIL} ({len(res.changes)} vulnerable crate(s); see the fix plan)")
    return res
