"""Report publishing: the executor commits each run's reports to the operator's private ops repository.

The **executor** (never the agent) publishes. It writes the run's already-fenced report, review and
summary to ``reports/<YYYY-MM-DD>/<run_id>/`` on a dedicated ``reports`` branch of a private
repository the operator owns, and pushes that branch **without force**.

Publishing reaches across repositories, so it is constraint-relaxing (Rule 15) and needs its own
**publish grant**, a separate record type with its own purpose tag (:data:`PUBLISH_PURPOSE`). Its
signature can never be replayed as a remediation grant, or the reverse. A publish grant:

- is Ed25519-signed by the operator through the same flow and verify-key store as
  :mod:`agentteams.vulnscan.grant`;
- pins the local checkout (``ops_repo``), the ``remote_url``, the GitHub repository ``node_id``
  and the owner's numeric ``owner_id``, plus the ``governance_dir`` whose decision log is checked
  for a ``vulnscan-publish`` HALT (C-2);
- runs at most :data:`MAX_DAYS` days and :data:`MAX_USES` publishes, counted from the ledger's
  ``publish-begun`` rows;
- is capped at :data:`MAX_ACTIVE` valid grant: more refuses them all (Rule 15(d)).

Before committing and again before pushing, :func:`preflight` confirms the remote is the pinned,
private repository of the pinned owner, that the authenticated ``gh`` user is that owner, that
Actions and Pages are off, and that the ``reports`` branch carries no workflows.

Records live in ``<config>/vulnscan-publish-grants/<grant_id>.json``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import executor, grant, ledger, paths

PUBLISH_PURPOSE = "agentteams-vulnscan-publish-grant-v1"
PUBLISH_ACTION = "vulnscan-publish"          # decision-log action id a HALT would name
MAX_DAYS = 30
MAX_USES = 400
MAX_ACTIVE = 1
MAX_FILES = 10
MAX_TOTAL_BYTES = 1_000_000
BRANCH = "reports"
FIELDS = ("grant_id", "issued", "expires", "ops_repo", "remote_url", "node_id", "owner_id", "governance_dir",
          "max_uses", "key_id")
_ID_RE = re.compile(r"^vp-[0-9a-f]{16}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{1,128}$")
_NWO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[A-Za-z][A-Za-z0-9._-]*(?![A-Za-z0-9._-])")
_WIN_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z][A-Za-z0-9._-]*(?![A-Za-z0-9._-])")
_GH_TIMEOUT = 120
_PRIVATE_IP_RE = re.compile(r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
                            r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])")


class PublishError(grant.GrantError):
    """A publish grant could not be built, verified or used (a :class:`grant.GrantError`)."""


# ---------------------------------------------------------------- grant records

def grants_dir() -> Path:
    """Return the operator-owned publish-grant directory.

    Returns:
        ``<config>/vulnscan-publish-grants``.
    """
    return paths.config_dir() / "vulnscan-publish-grants"


def signed_values(record: dict[str, Any]) -> list[str]:
    """Return the exact Ed25519 payload: purpose tag + canonical JSON of every bound field.

    Args:
        record: Publish-grant record.

    Returns:
        Field list for ``signed_ledger.ed25519_sign``/``ed25519_verify``.

    Raises:
        KeyError: A bound field is missing.
    """
    body = {name: record[name] for name in FIELDS}
    return [PUBLISH_PURPOSE, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def _check_remote_url(url: str) -> None:
    if (not isinstance(url, str) or not url or len(url) > 512 or url.startswith("-")
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
        raise PublishError("remote_url must be a non-empty URL without whitespace or a leading '-'")


def build(*, ops_repo: str, remote_url: str, node_id: str, owner_id: int, governance_dir: str, days: int,
          max_uses: int, key_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Build an unsigned publish grant, enforcing every bound.

    Args:
        ops_repo: Local checkout of the private ops repository (its top level).
        remote_url: The exact ``remote.origin.url`` of that checkout.
        node_id: GitHub repository node id (``gh repo view --json id``).
        owner_id: GitHub numeric id of the repository owner, who must also be the ``gh`` user.
        governance_dir: Team directory containing ``references/security-decisions.log.csv``, checked
            for a ``vulnscan-publish`` HALT on every publish.
        days: Lifetime (1..MAX_DAYS).
        max_uses: Publish cap (1..MAX_USES).
        key_id: Operator verify-key id.
        now: Clock override (tests).

    Returns:
        The unsigned record.

    Raises:
        PublishError: Any bound is violated.
    """
    now = now or datetime.now(UTC)
    if not 1 <= days <= MAX_DAYS:
        raise PublishError(f"days must be 1..{MAX_DAYS}")
    if not 1 <= max_uses <= MAX_USES:
        raise PublishError(f"max_uses must be 1..{MAX_USES}")
    if not _KEY_ID_RE.match(key_id):
        raise PublishError(f"malformed key_id {key_id!r}")
    if not isinstance(node_id, str) or not _NODE_ID_RE.match(node_id):
        raise PublishError(f"malformed node_id {node_id!r}")
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise PublishError("owner_id must be a positive integer (the GitHub numeric user id)")
    _check_remote_url(remote_url)
    repo = Path(ops_repo).expanduser().resolve()
    if str(repo) in ("/", str(Path.home().resolve())):
        raise PublishError("ops_repo may not be / or the home directory itself")
    if not repo.is_dir():
        raise PublishError(f"ops_repo {repo} is not a directory")
    try:
        top = executor.git(repo, "rev-parse", "--show-toplevel").strip()
    except executor.ExecError as exc:
        raise PublishError(f"ops_repo {repo} is not a git repository: {exc}") from exc
    if not top or Path(top).resolve() != repo:
        raise PublishError(f"ops_repo {repo} is not the top level of a git repository")
    gov = Path(governance_dir).expanduser().resolve() if governance_dir else None
    if gov is None or not (gov / "references" / "security-decisions.log.csv").is_file():
        raise PublishError("governance_dir must be a team directory containing references/security-decisions.log.csv")
    return {"grant_id": "vp-" + secrets.token_hex(8),
            "issued": now.replace(microsecond=0).isoformat(),
            "expires": (now + timedelta(days=days)).replace(microsecond=0).isoformat(),
            "ops_repo": str(repo), "remote_url": remote_url, "node_id": node_id, "owner_id": owner_id,
            "governance_dir": str(gov), "max_uses": max_uses, "key_id": key_id}


def sign(record: dict[str, Any], *, confirm_sha256: str | None = None,
         allow_checkout: bool = False) -> dict[str, Any] | None:
    """Sign a publish grant through :func:`grant.operator_sign`.

    Args:
        record: Unsigned publish grant from :func:`build`.
        confirm_sha256: Exact review digest, for non-terminal confirmation.
        allow_checkout: ``--allow-checkout-signing`` (as for other operator signatures).

    Returns:
        The signed record, or None after printing why nothing was signed.
    """
    return grant.operator_sign(record, digest_kind="vulnscan-publish-grant", fields=FIELDS, values=signed_values,
                               banner="About to sign a STANDING vulnscan PUBLISH grant. Within these bounds, the "
                                      "executor pushes each\nrun's report, review and summary to this repository's "
                                      "'reports' branch without asking you:",
                               confirm_sha256=confirm_sha256, allow_checkout=allow_checkout)


def install(record: dict[str, Any]) -> Path:
    """Write a signed publish grant into the operator-owned directory (mode 600).

    Args:
        record: Signed publish grant.

    Returns:
        The file path.

    Raises:
        PublishError: Unsigned or malformed record.
    """
    if not _ID_RE.match(str(record.get("grant_id", ""))) or not record.get("signature"):
        raise PublishError("refusing to install an unsigned or malformed publish grant")
    d = grants_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"{record['grant_id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    return path


def revoke(grant_id: str) -> bool:
    """Delete a publish grant (a narrowing; no signature needed).

    Args:
        grant_id: Grant to remove.

    Returns:
        Whether a file was removed.
    """
    if not _ID_RE.match(grant_id):
        return False
    path = grants_dir() / f"{grant_id}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def verify(record: dict[str, Any], *, now: datetime | None = None) -> None:
    """Verify one publish grant's shape, bounds, expiry and signature.

    Args:
        record: Publish grant as read from disk.
        now: Clock override (tests).

    Raises:
        PublishError: Anything is wrong (fail closed).
    """
    from agentteams.cli.signed_ledger import ed25519_verify

    now = now or datetime.now(UTC)
    if not isinstance(record, dict) or set(record) != {*FIELDS, "signature"}:
        raise PublishError("unexpected or missing fields")
    if not _ID_RE.match(str(record["grant_id"])):
        raise PublishError("malformed grant_id")
    owner = record["owner_id"]
    if isinstance(owner, bool) or not isinstance(owner, int) or owner <= 0:
        raise PublishError("malformed owner_id")
    if not isinstance(record["node_id"], str) or not _NODE_ID_RE.match(record["node_id"]):
        raise PublishError("malformed node_id")
    _check_remote_url(record["remote_url"])
    issued = datetime.fromisoformat(record["issued"])
    expires = datetime.fromisoformat(record["expires"])
    if expires - issued > timedelta(days=MAX_DAYS, minutes=1) or not 1 <= int(record["max_uses"]) <= MAX_USES:
        raise PublishError("bounds exceed the publish-grant limits")
    if not issued <= now < expires:
        raise PublishError(f"not valid now (valid {record['issued']} .. {record['expires']})")
    try:
        ok = ed25519_verify(grant.verify_key(str(record["key_id"])), signed_values(record),
                            str(record["signature"]))
    except RuntimeError as exc:
        raise PublishError(f"cannot verify: {exc}") from exc
    if not ok:
        raise PublishError("signature does not verify")


def load_active(*, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (valid publish grants, problems). Over :data:`MAX_ACTIVE`, every grant is refused.

    Args:
        now: Clock override (tests).

    Returns:
        Valid records and one message per rejected file.
    """
    d = grants_dir()
    if not d.is_dir():
        return [], []
    st = d.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        return [], [f"{d} is not owned by you or is group/world-writable — refusing all publish grants"]
    valid: list[dict[str, Any]] = []
    problems: list[str] = []
    for f in sorted(d.glob("vp-*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            verify(rec, now=now)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        valid.append(rec)
    if len(valid) > MAX_ACTIVE:
        cap = f"{len(valid)} valid publish grants exceed the aggregate cap of {MAX_ACTIVE} — refusing all"
        return [], [*problems, cap]
    return valid, problems


def all_ops_repos() -> set[Path]:
    """Return the ``ops_repo`` of every publish-grant file, valid or not (expired, revoked-later, unsigned).

    Only ever used to **refuse** remediation on those paths, so an unverified file can narrow what
    the executor does but never widen it.

    Returns:
        Resolved paths.
    """
    return {Path(v).expanduser().resolve() for v in grant.unverified_values(grants_dir(), "ops_repo")}


def uses(grant_id: str, rows: list[dict[str, Any]] | None = None) -> int:
    """Count publishes charged to a grant (every ``publish-begun`` row).

    Args:
        grant_id: Publish-grant id.
        rows: Pre-read ledger rows (optional; read and verified otherwise).

    Returns:
        Number of reserved publishes.

    Raises:
        ValueError: The ledger fails verification (when ``rows`` is not given).
    """
    return sum(1 for r in (rows if rows is not None else ledger.read())
               if r.get("kind") == "publish-begun" and r.get("grant_id") == grant_id)


# ---------------------------------------------------------------- content

def _host_names() -> list[str]:
    node = platform.node().strip().lower()
    return sorted({n for n in (node, node.split(".")[0]) if len(n) >= 3 and n != "localhost"}, key=len, reverse=True)


def rewrite_paths(text: str) -> str:
    """Scrub local identity from text that leaves the machine (Rule S-8).

    Rewrites ``str(Path.home())``, ``/Users/<name>``, ``/home/<name>`` and ``C:\\Users\\<name>``
    (each followed by ``/``, ``\\`` or a non-name character) to ``~``; this machine's hostname
    (full and short, case-insensitive) to ``<this-host>``; and RFC 1918 IPv4 addresses to
    ``<private-ip>``. Over-matching only redacts more.

    Args:
        text: Any text.

    Returns:
        The text with home paths rewritten.
    """
    home = str(Path.home()).rstrip("/")
    if len(home) > 1:
        text = re.sub(re.escape(home) + r"(?![A-Za-z0-9._-])", "~", text)
    text = _HOME_PATH_RE.sub("~", text)
    text = _WIN_HOME_RE.sub("~", text)
    for name in _host_names():
        text = re.sub(r"(?<![A-Za-z0-9-])" + re.escape(name) + r"(?![A-Za-z0-9-])", "<this-host>", text, flags=re.I)
    return _PRIVATE_IP_RE.sub("<private-ip>", text)


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fence_opener(line: str) -> tuple[str, bool] | None:
    """Classify ``line`` as a CommonMark fence opener.

    Args:
        line: One line of Markdown.

    Returns:
        ``(bar, is_text)`` for an opener — ``bar`` is the backtick or tilde run and ``is_text`` is
        true only for a backtick run immediately followed by ``text`` (what ``report_store.fence``
        writes) — or ``None`` when the line opens no fence.
    """
    m = _FENCE_OPEN.match(line)
    if not m:
        return None
    bar, info = m.group(1), m.group(2)
    if bar[0] == "`" and "`" in info:   # CommonMark: a backtick info string may not hold a backtick
        return None
    return bar, bar[0] == "`" and info == "text"


def _closes(line: str, bar: str) -> bool:
    """Return whether ``line`` closes a fence opened by ``bar`` (CommonMark closing rule).

    Args:
        line: One line of Markdown.
        bar: The opener's backtick or tilde run.

    Returns:
        True for up to 3 spaces of indent, a run of ``bar``'s character at least ``len(bar)`` long,
        then only spaces or tabs.
    """
    char = re.escape(bar[0])
    return re.fullmatch(r" {0,3}" + char + "{" + str(len(bar)) + r",}[ \t]*", line) is not None


#: A CommonMark HTML-block start outside a fence (up to 3 spaces, then ``<``): no exemption then.
_HTML_BLOCK_START = re.compile(r"^ {0,3}<")
#: Characters str.splitlines treats as line breaks but CommonMark does not (only \n, \r, \r\n are).
_EXTRA_LINE_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def fenced_lines(text: str) -> set[int]:
    """Return the 1-based line numbers inside closed ``report_store.fence`` blocks (fence lines excluded).

    Follows CommonMark fenced-code rules: an opener has up to 3 spaces of indent; the fence closes
    at the first later line with up to 3 spaces of indent, a run of the same character at least as
    long as the opener's, and only trailing spaces or tabs; fences do not nest. Other fences
    (``~~~``, backticks with another info string) are tracked so their content cannot open a
    ``text`` fence, but their lines are never returned. An unclosed fence fences nothing (fail closed).
    Lines are numbered with :meth:`str.splitlines`, as :func:`agentteams.scan.scan_content` numbers them.

    Args:
        text: Markdown text.

    Returns:
        Line numbers of body lines of closed ``text`` fences.
    """
    if any(ch in text for ch in _EXTRA_LINE_BREAKS):
        # str.splitlines (and so the scanner's line numbers) breaks on these; CommonMark renderers do not.
        # A line the renderer shows as plain text could open a fence here, so no exemption at all (V1-1).
        return set()
    inside: set[int] = set()
    pending: list[int] = []
    bar, is_text = "", False
    for num, line in enumerate(text.splitlines(), start=1):
        if bar:
            if _closes(line, bar):
                if is_text:
                    inside.update(pending)
                bar, is_text, pending = "", False, []
            else:
                pending.append(num)
            continue
        if _HTML_BLOCK_START.match(line):
            # An HTML block runs to the next blank line and renders what follows it raw, so a fence
            # inside it is not a fence on GitHub (V2-2). Agent text is always fenced; refuse the exemption.
            return set()
        opener = _fence_opener(line)
        if opener:
            bar, is_text = opener
    return inside


def _check_files(files: dict[str, str]) -> tuple[dict[str, str], int]:
    """Validate names, rewrite paths, enforce caps and the content scan.

    Injection-pattern hits **inside** a fence are expected (escalation evidence quotes the injection
    it reports) and do not block; they are counted so the ledger shows them. Every other high
    finding (secrets, personal data) blocks wherever it is, fenced or not.

    Returns:
        ``(rewritten files, fenced injection hits)``.
    """
    from agentteams.scan import scan_content

    if not isinstance(files, dict) or not files:
        raise executor.ExecError("nothing to publish")
    if len(files) > MAX_FILES:
        raise executor.ExecError(f"{len(files)} files exceed the cap of {MAX_FILES}")
    out: dict[str, str] = {}
    for name, text in files.items():
        if not isinstance(name, str) or not _NAME_RE.match(name) or not isinstance(text, str):
            raise executor.ExecError(f"bad file name or content: {str(name)[:80]!r}")
        if "\x00" in text:
            raise executor.ExecError(f"{name}: contains NUL")
        out[name] = rewrite_paths(text)
    total = sum(len(t.encode("utf-8")) for t in out.values())
    if total > MAX_TOTAL_BYTES:
        raise executor.ExecError(f"{total} bytes exceed the cap of {MAX_TOTAL_BYTES}")
    high: list[str] = []
    fenced_injection = 0
    for name, text in out.items():
        fenced = fenced_lines(text)
        for f in scan_content(text, filename=f"vulnscan-publish/{name}"):
            if f.severity != "high":
                continue
            if f.category == "injection" and f.line in fenced:
                fenced_injection += 1
            else:
                high.append(f"{name}:L{f.line} {f.category}")
    if high:
        raise executor.ExecError("content scan found high-severity findings (nothing published): "
                                 + ", ".join(high[:20]))
    return out, fenced_injection


# ---------------------------------------------------------------- preflight

def _gh(*args: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, env=env, timeout=_GH_TIMEOUT,
                              check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _gh_json(*args: str) -> dict[str, Any]:
    code, out, err = _gh(*args)
    if code:
        raise executor.ExecError(f"gh {' '.join(args[:2])} failed: {(err or out).strip()[:200]}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise executor.ExecError(f"gh {' '.join(args[:2])} returned non-JSON output") from exc
    if not isinstance(data, dict):
        raise executor.ExecError(f"gh {' '.join(args[:2])} returned an unexpected shape")
    return data


def _gh_list(path: str) -> list[dict[str, Any]]:
    """Run ``gh api --paginate <path>`` and return the concatenated list of objects.

    ``--paginate`` prints one JSON array per page back to back (``[...][...]``); each is decoded
    with :meth:`json.JSONDecoder.raw_decode` in turn.

    Args:
        path: The REST path (with query string).

    Returns:
        Every item of every page.

    Raises:
        executor.ExecError: ``gh`` failed, printed nothing, printed non-JSON, a page is not an array,
            or an item is not an object.
    """
    code, out, err = _gh("api", "--paginate", path)
    label = f"gh api {path.split('?')[0]}"
    if code:
        raise executor.ExecError(f"{label} failed: {(err or out).strip()[:200]}")
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    pos, pages = 0, 0
    while True:
        while pos < len(out) and out[pos].isspace():
            pos += 1
        if pos >= len(out):
            break
        try:
            page, pos = decoder.raw_decode(out, pos)
        except json.JSONDecodeError as exc:
            raise executor.ExecError(f"{label} returned non-JSON output") from exc
        if not isinstance(page, list) or not all(isinstance(i, dict) for i in page):
            raise executor.ExecError(f"{label} returned an unexpected shape")
        items += page
        pages += 1
    if not pages:
        raise executor.ExecError(f"{label} returned no output")
    return items


def _access_checks(nwo: str, owner_id: int) -> list[str]:
    """Refuse when anyone but the owner can read the repository through a collaborator, invitation or key.

    Each endpoint fails closed: an API error, a non-list reply or a missing ``id`` is a problem.
    GitHub Apps and fine-grained tokens with access are not visible here (documented residual).

    Args:
        nwo: ``owner/name``.
        owner_id: The pinned owner's numeric id.

    Returns:
        Problems; empty means only the owner has access by these routes.
    """
    problems: list[str] = []
    try:
        collaborators = _gh_list(f"repos/{nwo}/collaborators?affiliation=all&per_page=100")
        others = [c for c in collaborators if type(c.get("id")) is not int or c.get("id") != owner_id]
        if others:
            names = ", ".join(str(c.get("login", c.get("id")))[:60] for c in others[:5])
            problems.append(f"the ops repository has collaborators other than the owner: {names}")
    except executor.ExecError as exc:
        problems.append(f"cannot list collaborators: {exc}")
    try:
        if _gh_list(f"repos/{nwo}/invitations?per_page=100"):
            problems.append("the ops repository has pending collaborator invitations")
    except executor.ExecError as exc:
        problems.append(f"cannot list invitations: {exc}")
    try:
        if _gh_list(f"repos/{nwo}/keys?per_page=100"):
            problems.append("the ops repository has deploy keys (read-only keys also expose reports)")
    except executor.ExecError as exc:
        problems.append(f"cannot list deploy keys: {exc}")
    return problems


def _local_checks(repo: Path, remote_url: str) -> list[str]:
    problems: list[str] = []
    origin = executor.git(repo, "config", "--get", "remote.origin.url", check=False).strip()
    if origin != remote_url:
        problems.append(f"remote.origin.url is {origin!r}, not the pinned {remote_url!r}")
    if executor.git(repo, "config", "--get-all", "remote.origin.pushurl", check=False).strip():
        problems.append("remote.origin.pushurl is set; it could redirect the push")
    if executor.git(repo, "config", "--get-regexp", r"^url\..*insteadof$", check=False).strip():
        problems.append("a url.*.insteadOf / pushInsteadOf rewrite is configured; it could redirect the push")
    return problems


def _branch_checks(repo: Path) -> list[str]:
    heads = executor.git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", push=True).split()
    if not heads:
        return []
    sha = heads[0]
    if not _SHA_RE.match(sha):
        return [f"unexpected ls-remote output for {BRANCH}"]
    executor.git(repo, "fetch", "-q", "--no-tags", "origin", f"refs/heads/{BRANCH}", push=True)
    listed = executor.git(repo, "ls-tree", "-r", "--name-only", sha, "--", ".github/workflows").strip()
    return [f"the remote '{BRANCH}' branch contains .github/workflows/ — refusing"] if listed else []


def preflight(grant_record: dict[str, Any]) -> list[str]:
    """Check that publishing would go only to the pinned, private, owner-held, inert repository.

    Runs, in order: hardened ``git config`` reads of ``ops_repo`` (``remote.origin.url`` equals the pin;
    no ``pushurl``/``insteadOf`` redirection); ``gh repo view <remote_url> --json id,isPrivate,nameWithOwner``;
    ``gh api repos/<o>/<r>``; ``gh api user``; ``gh api repos/<o>/<r>/actions/permissions``;
    ``gh api repos/<o>/<r>/pages``; ``gh api --paginate`` of the repository's collaborators (only the
    owner may be listed), pending invitations and deploy keys (both must be empty); and ``git ls-remote``/``fetch``/``ls-tree`` of the ``reports`` branch.

    Args:
        grant_record: A verified publish grant.

    Returns:
        Problems; empty means publishing may proceed.
    """
    repo = Path(str(grant_record["ops_repo"]))
    pinned_owner = grant_record["owner_id"]
    problems: list[str] = []
    try:
        problems += _local_checks(repo, str(grant_record["remote_url"]))
        if problems:
            return problems
        view = _gh_json("repo", "view", str(grant_record["remote_url"]), "--json", "id,isPrivate,nameWithOwner")
        nwo = str(view.get("nameWithOwner", ""))
        if not _NWO_RE.match(nwo) or ".." in nwo:
            return [f"gh repo view returned a malformed nameWithOwner {nwo[:100]!r}"]
        if view.get("id") != grant_record["node_id"]:
            problems.append(f"repository node id {view.get('id')!r} is not the pinned {grant_record['node_id']!r}")
        if view.get("isPrivate") is not True:
            problems.append("the repository is not private")
        api_repo = _gh_json("api", f"repos/{nwo}")
        owner = api_repo.get("owner") if isinstance(api_repo.get("owner"), dict) else {}
        if owner.get("id") != pinned_owner:
            problems.append(f"repository owner id {owner.get('id')!r} is not the pinned owner_id {pinned_owner!r}")
        if api_repo.get("node_id") != grant_record["node_id"] or api_repo.get("private") is not True:
            problems.append("the REST view of the repository disagrees with the pinned node id or privacy")
        user = _gh_json("api", "user")
        if user.get("id") != pinned_owner:
            problems.append(f"the gh user id {user.get('id')!r} is not the pinned owner_id {pinned_owner!r}")
        actions = _gh_json("api", f"repos/{nwo}/actions/permissions")
        if actions.get("enabled") is not False:
            problems.append("GitHub Actions is enabled on the ops repository")
        code, out, err = _gh("api", f"repos/{nwo}/pages")
        if code == 0:
            problems.append("GitHub Pages is enabled on the ops repository")
        elif "404" not in (err + out):
            problems.append(f"cannot determine the Pages status: {(err or out).strip()[:200]}")
        problems += _access_checks(nwo, pinned_owner)
        problems += _branch_checks(repo)
    except executor.ExecError as exc:
        problems.append(f"preflight failed: {exc}")
    return problems


# ---------------------------------------------------------------- publish

def _run_date(run_dir: Path) -> str:
    name = Path(run_dir).name
    return name if _DATE_RE.match(name) else f"{datetime.now(UTC):%Y-%m-%d}"


def _fresh_worktree(repo: Path, wt: Path, base: str) -> None:
    if wt.exists():
        shutil.rmtree(wt)
    executor.git(repo, "worktree", "prune")
    wt.parent.mkdir(parents=True, exist_ok=True)
    executor.git(repo, "worktree", "add", "-q", "--detach", str(wt), base)


def _commit(repo: Path, files: dict[str, str], rel: str, run_id: str) -> str:
    """Commit ``files`` under ``rel`` on top of the remote ``reports`` tip (or as a root commit)."""
    heads = executor.git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", push=True).split()
    parent = heads[0] if heads else ""
    if parent:
        if not _SHA_RE.match(parent):
            raise executor.ExecError(f"unexpected ls-remote output for {BRANCH}")
        executor.git(repo, "fetch", "-q", "--no-tags", "origin", f"refs/heads/{BRANCH}", push=True)
    wt = paths.state_dir() / "publish-wt"
    _fresh_worktree(repo, wt, parent or "HEAD")
    if parent:
        executor.git(wt, "read-tree", parent)
    else:
        executor.git(wt, "read-tree", "--empty")
    dest = wt / rel
    dest.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (dest / name).write_text(text, encoding="utf-8")
    executor.git(wt, "add", "--", *(f"{rel}/{n}" for n in files))
    tree = executor.git(wt, "write-tree").strip()
    argv = ["commit-tree", tree, "-m", f"vulnscan reports for run {run_id}",
            "-m", "Published by the agentteams vulnscan executor."]
    if parent:
        argv[2:2] = ["-p", parent]
    sha = executor.git(wt, *argv).strip()
    executor.git(wt, "update-ref", "--no-deref", "HEAD", sha)
    return sha


def _refuse(g: dict[str, Any], run_id: str, detail: str) -> dict[str, Any]:
    ledger.append("refused", op="publish", grant_id=str(g.get("grant_id", ""))[:40],
                  target=str(g.get("ops_repo", ""))[:300], run_id=run_id[:80], detail=detail[:1000])
    return {"status": "refused", "detail": detail}


def _gate(g: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Raise ExecError unless ``g`` is the active grant, has uses left and no HALT is in force."""
    active, problems = load_active()
    if g.get("grant_id") not in {a["grant_id"] for a in active}:
        raise executor.ExecError("not an active, valid publish grant: "
                                 + ("; ".join(problems) if problems else "unknown or revoked"))
    g = next(a for a in active if a["grant_id"] == g.get("grant_id"))
    if uses(g["grant_id"], rows) >= int(g["max_uses"]):
        raise executor.ExecError(f"publish grant uses exhausted ({g['max_uses']})")
    gov = Path(str(g["governance_dir"]))
    log = gov / "references" / "security-decisions.log.csv"
    if not log.is_file() or log.is_symlink():
        raise executor.ExecError(f"decision log {log} is missing or a symlink, so the C-2 HALT check cannot run")
    from agentteams.cli import decision_log

    try:
        decision_log._assert_no_unretracted_halt(gov, action=PUBLISH_ACTION)
    except RuntimeError as exc:
        raise executor.ExecError(f"HALT in force: {exc}") from exc


def publish(grant_record: dict[str, Any], run_dir: Path, run_id: str, files: dict[str, str]) -> dict[str, Any]:
    """Publish one run's (already fenced) files to the ops repository's ``reports`` branch. Takes the ledger lock.

    Sequence: verify the grant is the active one with uses left and no ``vulnscan-publish`` HALT;
    :func:`_check_files` (scrub local identity, cap at :data:`MAX_FILES` files / :data:`MAX_TOTAL_BYTES`
    bytes, content scan: any high finding refuses except an injection-pattern hit inside a fence),
    refusing **before** a use is reserved; ledger ``publish-begun``; :func:`preflight`; commit
    ``reports/<date>/<run_id>/<name>`` in ``<state>/publish-wt``; :func:`preflight` again; push
    ``HEAD:refs/heads/reports`` without force; ledger ``publish-done`` or ``publish-failed``.

    Args:
        grant_record: The publish grant (re-verified against :func:`load_active`).
        run_dir: The run directory; its name gives the date when it is ``YYYY-MM-DD``.
        run_id: The run id (``executor.new_run_id``).
        files: ``{file name: text}``; names are plain file names, texts are already fenced by the caller.

    Returns:
        ``{"status": "done", "commit", "path"}``; ``{"status": "failed", "detail", "local_commit"}``
        (a failed push keeps the local commit, also under ``refs/vulnscan-publish/<run_id>``); or
        ``{"status": "refused", "detail"}`` when nothing was reserved.
    """
    with ledger.locked():
        try:
            rows = ledger.read()
        except ValueError as exc:
            return {"status": "refused", "detail": f"ledger integrity failure — refusing everything: {exc}"}
        if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            return _refuse(grant_record, str(run_id), "malformed run_id")
        try:
            _gate(grant_record, rows)
        except executor.ExecError as exc:
            return _refuse(grant_record, run_id, str(exc))
        try:   # before a use is reserved: content the scan refuses never costs a publish
            clean, fenced_injection = _check_files(files)
        except executor.ExecError as exc:
            return _refuse(grant_record, run_id, str(exc))
        gid, repo = grant_record["grant_id"], Path(str(grant_record["ops_repo"]))
        rel = f"{BRANCH}/{_run_date(run_dir)}/{run_id}"
        ledger.append("publish-begun", grant_id=gid, op="publish", target=str(repo), run_id=run_id,
                      path=rel, files=sorted(str(n)[:100] for n in files)[:MAX_FILES + 1])
        sha = ""
        try:
            problems = preflight(grant_record)
            if problems:
                raise executor.ExecError("preflight: " + "; ".join(problems))
            sha = _commit(repo, clean, rel, run_id)
            problems = preflight(grant_record)
            if problems:
                raise executor.ExecError("preflight (before push): " + "; ".join(problems))
            executor.git(paths.state_dir() / "publish-wt", "push", "-q", "origin",
                         f"HEAD:refs/heads/{BRANCH}", push=True)
        except (executor.ExecError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
            if sha:
                executor.git(repo, "update-ref", f"refs/vulnscan-publish/{run_id}", sha, check=False)
            ledger.append("publish-failed", grant_id=gid, op="publish", target=str(repo), run_id=run_id,
                          local_commit=sha, detail=str(exc)[:1000])
            return {"status": "failed", "detail": str(exc), "local_commit": sha}
        ledger.append("publish-done", grant_id=gid, op="publish", target=str(repo), run_id=run_id,
                      commit=sha, path=rel, fenced_injection_hits=fenced_injection)
        return {"status": "done", "commit": sha, "path": rel, "fenced_injection_hits": fenced_injection}
