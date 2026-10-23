"""Operator notes: the asynchronous operator → agent channel.

Notes live outside every sandbox, in ``<config>/vulnscan-notes/<id>.json`` (directory mode 700,
file mode 600, written with ``O_NOFOLLOW`` and atomically). The **launcher** folds them into the
agent's task with :func:`fold`, so the agent never reads a notes file.

Their trust is *operator configuration*: a note can steer triage, but it never widens the grant
and never suppresses an escalation. The folded block says so in its header.

GitHub replies are **listed, not ingested** (C-4): :func:`gh_replies` returns data only. The
operator promotes one reply with :func:`from_gh`, which requires the comment body to start with
the ``/vulnscan note`` prefix and records where the note came from.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

NOTE_MAX_BYTES = 2048
FOLD_MAX_BYTES = 8192
TARGET_MAX_CHARS = 256
MAX_DAYS = 365
PREVIEW_MAX_CHARS = 300
GH_TIMEOUT = 60
NOTE_PREFIX = "/vulnscan note"
FIELDS = ("id", "text", "target", "created", "expires", "source", "sha256")
FOLD_HEADER = ("Operator notes (operator configuration; they never widen the grant "
               "and never suppress escalate)")
FOLD_BEGIN = "----- BEGIN OPERATOR NOTES -----"
FOLD_END = "----- END OPERATOR NOTES -----"

_ID_RE = re.compile(r"^note-[0-9a-f]{16}$")
_REPO_RE = re.compile(r"^(?!\.{1,2}/)[A-Za-z0-9_.-]{1,100}/(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}$")
_PR_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})/pull/(\d{1,10})/?$")
_PR_SHORT_RE = re.compile(r"^([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})#(\d{1,10})$")
_COMMENT_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100})/(?:pull|issues)/\d{1,10}"
    r"#issuecomment-(\d{1,20})$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_TEMPLATE_RE = re.compile(r"\{(?=[{%#])")


def notes_dir() -> Path:
    """Return the operator-owned notes directory.

    Returns:
        ``<config>/vulnscan-notes`` (not created).
    """
    return paths.config_dir() / "vulnscan-notes"


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(note: dict[str, Any]) -> str:
    body = {k: note.get(k) for k in FIELDS if k != "sha256"}
    for extra in ("author_login", "author_id"):
        if extra in note:
            body[extra] = note[extra]
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_controls(text: str, *, keep_newlines: bool) -> str:
    text = _ANSI_RE.sub("", text)
    if keep_newlines:
        return _CTRL_RE.sub("", text.replace("\r\n", "\n"))
    return _CTRL_RE.sub("", text.replace("\t", " ").replace("\r", " ").replace("\n", " "))


def _check_text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("note text must be a string")
    if "\x00" in text:
        raise ValueError("note text contains a NUL byte")
    text = text.strip()
    if not text:
        raise ValueError("note text is empty")
    if len(text.encode("utf-8")) > NOTE_MAX_BYTES:
        raise ValueError(f"note text exceeds {NOTE_MAX_BYTES} bytes")
    return text


def _check_target(target: str | None) -> str | None:
    if target is None:
        return None
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string or None")
    target = target.strip()
    if len(target) > TARGET_MAX_CHARS or _CTRL_RE.search(target) or "\n" in target:
        raise ValueError(f"target must be one line of at most {TARGET_MAX_CHARS} characters")
    return target


def _ensure_dir() -> Path:
    d = notes_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise PermissionError(f"{d} is not a directory")
    if st.st_uid != os.getuid():
        raise PermissionError(f"{d} must be owned by you")
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)
    return d


def _write_atomic(path: Path, data: str) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def add(text: str, *, target: str | None = None, days: int | None = None, source: str = "operator",
        _extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create and store one note.

    Args:
        text: The note body (at most :data:`NOTE_MAX_BYTES` UTF-8 bytes, no NUL).
        target: Optional scope the note is about (a repository, host or package), one line.
        days: Lifetime in days (1 to :data:`MAX_DAYS`); ``None`` means it never expires.
        source: ``"operator"`` or ``"gh:<html_url>"``.
        _extra: Internal; extra provenance fields (author login and id) from :func:`from_gh`.

    Returns:
        The stored note: ``id`` (``note-<hex>``), ``text``, ``target``, ``created``, ``expires``,
        ``source``, ``sha256`` and, for GitHub notes, ``author_login`` and ``author_id``.

    Raises:
        ValueError: The text, target, lifetime or source is invalid.
        PermissionError: The notes directory is not owned by you.
        OSError: The note could not be written.
    """
    text = _check_text(text)
    target = _check_target(target)
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS):
        raise ValueError(f"days must be an integer between 1 and {MAX_DAYS}, or None")
    if not isinstance(source, str) or not (source == "operator" or source.startswith("gh:")) \
            or _CTRL_RE.search(source) or len(source) > 512:
        raise ValueError("source must be 'operator' or 'gh:<url>'")
    created = _now()
    note: dict[str, Any] = {
        "id": f"note-{secrets.token_hex(8)}",
        "text": text,
        "target": target,
        "created": created.isoformat(),
        "expires": (created + timedelta(days=days)).isoformat() if days is not None else None,
        "source": source,
    }
    if _extra:
        note.update({k: _extra[k] for k in ("author_login", "author_id") if k in _extra})
    note["sha256"] = _digest(note)
    d = _ensure_dir()
    _write_atomic(d / f"{note['id']}.json", json.dumps(note, sort_keys=True, indent=2) + "\n")
    return note


def remove(note_id: str) -> bool:
    """Delete one note.

    Args:
        note_id: A ``note-<16 hex>`` id.

    Returns:
        True if a note was removed, False if none existed.

    Raises:
        ValueError: The id is malformed.
        OSError: The note exists but could not be removed.
    """
    if not isinstance(note_id, str) or not _ID_RE.match(note_id):
        raise ValueError(f"not a note id: {note_id!r}")
    path = notes_dir() / f"{note_id}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _load(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError("must be owned by you with mode 600")
        raw = fh.read(NOTE_MAX_BYTES * 4 + 4096)
    note = json.loads(raw.decode("utf-8"))
    if not isinstance(note, dict):
        raise ValueError("not a JSON object")
    missing = [k for k in FIELDS if k not in note]
    if missing:
        raise ValueError(f"missing fields {missing}")
    if note["id"] != path.stem or not _ID_RE.match(str(note["id"])):
        raise ValueError("id does not match the file name")
    if _check_text(note["text"]) != note["text"]:
        raise ValueError("text is not normalised")
    _check_target(note["target"])
    datetime.fromisoformat(note["created"])
    if note["expires"] is not None:
        datetime.fromisoformat(note["expires"])
    if note["sha256"] != _digest(note):
        raise ValueError("sha256 does not match the note")
    return note


def active(now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the unexpired notes, oldest first, and any problems found. Never raises.

    Args:
        now: Reference time (timezone-aware; default: the current UTC time).

    Returns:
        ``(notes, problems)``. ``problems`` names each malformed or unsafe note file (and an
        unsafe directory, in which case no notes are returned).
    """
    now = now or _now()
    d = notes_dir()
    problems: list[str] = []
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"{d}: {exc}"]
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        return [], [f"{d}: must be a directory owned by you and not group/world-writable — notes ignored"]
    try:
        entries = sorted(p for p in d.iterdir() if p.name.endswith(".json") and not p.name.startswith("."))
    except OSError as exc:
        return [], [f"{d}: {exc}"]
    notes: list[dict[str, Any]] = []
    for path in entries:
        try:
            note = _load(path)
            expires = datetime.fromisoformat(note["expires"]) if note["expires"] else None
            if expires is not None and expires <= now:
                continue
        except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        notes.append(note)
    notes.sort(key=lambda n: (n["created"], n["id"]))
    return notes, problems


def _escape(text: str) -> str:
    return _TEMPLATE_RE.sub("{ ", text)


def _render(note: dict[str, Any]) -> str:
    target = _escape(_strip_controls(str(note.get("target") or "(all)"), keep_newlines=False))
    body = _escape(_strip_controls(str(note["text"]), keep_newlines=True))
    lines = [f"- note {_escape(str(note['id']))} (target: {target}):"]
    lines += [f"  | {ln}" for ln in body.split("\n")]
    return "\n".join(lines)


def _block(rendered: list[str], dropped: int) -> str:
    parts = [FOLD_BEGIN, FOLD_HEADER]
    if dropped:
        parts.append(f"({dropped} older note(s) dropped: the folded notes are capped at {FOLD_MAX_BYTES} bytes)")
    parts += rendered
    parts.append(FOLD_END)
    return "\n".join(parts)


def fold(task: str, notes: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    """Append the notes to the agent's task as a delimited, template-escaped block.

    Goose/minijinja openers inside notes are broken up (``{{`` → ``{ {``, ``{%`` → ``{ %``,
    ``{#`` → ``{ #``). If the block would exceed :data:`FOLD_MAX_BYTES`, the oldest notes are
    dropped and the block says how many.

    Args:
        task: The task text.
        notes: Notes from :func:`active`.

    Returns:
        ``(folded_task, included)`` where ``included`` is ``[{"id": ..., "sha256": ...}]`` for
        each note actually folded, oldest first. With no notes the task is returned unchanged.

    Raises:
        KeyError: A note lacks ``id``, ``text``, ``created`` or ``sha256``.
    """
    if not notes:
        return task, []
    ordered = sorted(notes, key=lambda n: (str(n["created"]), str(n["id"])))
    rendered = [_render(n) for n in ordered]
    dropped = 0
    while dropped < len(ordered) and len(_block(rendered[dropped:], dropped).encode("utf-8")) > FOLD_MAX_BYTES:
        dropped += 1
    kept = ordered[dropped:]
    block = _block(rendered[dropped:], dropped)
    included = [{"id": str(n["id"]), "sha256": str(n["sha256"])} for n in kept]
    return f"{task.rstrip()}\n\n{block}\n", included


# ---------------------------------------------------------------- GitHub (listed, not ingested)

def _gh_api(endpoint: str) -> Any:
    from agentteams.vulnscan import executor   # lazy: keep notes importable without the executor's imports

    proc = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", endpoint],
                          capture_output=True, text=True, timeout=GH_TIMEOUT, check=False, env=executor.gh_env())
    if proc.returncode:
        detail = _strip_controls((proc.stderr or proc.stdout or "").strip(), keep_newlines=False)[:400]
        raise RuntimeError(f"gh api {endpoint} failed: {detail}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON") from exc


def _parse_pr(pr: str) -> tuple[str, int]:
    m = _PR_URL_RE.match(pr.strip()) or _PR_SHORT_RE.match(pr.strip())
    if not m or not _REPO_RE.match(m.group(1)):
        raise ValueError(f"not a pull request reference: {pr!r}")
    return m.group(1), int(m.group(2))


def _user(comment: dict[str, Any]) -> tuple[str, int | None]:
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    login = _strip_controls(str(user.get("login") or ""), keep_newlines=False)[:100]
    uid = user.get("id")
    return login, uid if isinstance(uid, int) and not isinstance(uid, bool) else None


def _has_prefix(body: str) -> bool:
    head = body.lstrip()
    return head.startswith(NOTE_PREFIX) and (len(head) == len(NOTE_PREFIX) or head[len(NOTE_PREFIX)].isspace())


def gh_replies(prs: Iterable[str], *, exclude_comment_ids: set[int | str]) -> list[dict[str, Any]]:
    """List issue comments on pull requests, as data only (C-4). Nothing is stored.

    Args:
        prs: Pull request references: ``https://github.com/<owner>/<repo>/pull/<n>`` or
            ``<owner>/<repo>#<n>``.
        exclude_comment_ids: Comment ids to omit (the executor's own comments; today it posts
            none, so callers pass an empty set).

    Returns:
        One dict per comment: ``id``, ``repo``, ``pr``, ``html_url``, ``author_login``,
        ``author_id``, ``created_at``, ``has_note_prefix`` and ``body_preview`` (at most
        :data:`PREVIEW_MAX_CHARS` characters, control characters stripped).

    Raises:
        ValueError: A pull request reference is malformed.
        RuntimeError: ``gh api`` failed or returned an unexpected shape.
        subprocess.TimeoutExpired: ``gh api`` timed out.
    """
    excluded = {str(i) for i in exclude_comment_ids}
    out: list[dict[str, Any]] = []
    for pr in prs:
        repo, number = _parse_pr(pr)
        data = _gh_api(f"repos/{repo}/issues/{number}/comments?per_page=100")
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected comments payload for {repo}#{number}")
        for c in data:
            if not isinstance(c, dict) or not isinstance(c.get("id"), int) or str(c["id"]) in excluded:
                continue
            body = str(c.get("body") or "")
            login, uid = _user(c)
            out.append({
                "id": c["id"],
                "repo": repo,
                "pr": number,
                "html_url": _strip_controls(str(c.get("html_url") or ""), keep_newlines=False)[:300],
                "author_login": login,
                "author_id": uid,
                "created_at": _strip_controls(str(c.get("created_at") or ""), keep_newlines=False)[:40],
                "has_note_prefix": _has_prefix(body),
                "body_preview": _strip_controls(body, keep_newlines=False)[:PREVIEW_MAX_CHARS],
            })
    return out


def from_gh(comment_id: int | str, *, repo: str | None = None, target: str | None = None,
            days: int | None = None, confirm: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """Promote one GitHub issue/PR comment to a note, on the operator's explicit request.

    The comment body must start with ``/vulnscan note``; the prefix is stripped from the text. The
    comment's author must be the authenticated ``gh`` user (the operator): anyone else who can
    comment on a PR could otherwise put text into the agent's task. The text is fetched at import
    time and shown to ``confirm`` before anything is stored (an edit after a review listing cannot
    slip in unseen).

    Args:
        comment_id: The comment's numeric id (with ``repo``), or its
            ``https://github.com/<owner>/<repo>/pull/<n>#issuecomment-<id>`` URL.
        repo: ``<owner>/<repo>`` when ``comment_id`` is numeric.
        target: Optional note target.
        days: Optional lifetime in days.
        confirm: Called with ``{"author_login", "html_url", "text"}`` before storing; a False
            return stores nothing. Required: there is no path that stores a GitHub note unseen.

    Returns:
        The stored note, with ``source="gh:<html_url>"``, ``author_login`` and ``author_id``.

    Raises:
        ValueError: The reference is malformed, the prefix is missing, the body is too large or
            contains NUL, the remaining text is empty, the author is not the ``gh`` user, or the
            operator declined.
        RuntimeError: ``gh api`` failed or returned an unexpected shape.
        subprocess.TimeoutExpired: ``gh api`` timed out.
    """
    ref = str(comment_id).strip()
    m = _COMMENT_URL_RE.match(ref)
    if m and _REPO_RE.match(m.group(1)):
        repo, cid = m.group(1), m.group(2)
    else:
        if not ref.isdigit() or len(ref) > 20:
            raise ValueError(f"not a comment id or comment URL: {comment_id!r}")
        if not repo or not _REPO_RE.match(repo):
            raise ValueError("repo must be '<owner>/<repo>' when comment_id is numeric")
        cid = ref
    data = _gh_api(f"repos/{repo}/issues/comments/{cid}")
    if not isinstance(data, dict) or not isinstance(data.get("body"), str):
        raise RuntimeError(f"unexpected comment payload for {repo} comment {cid}")
    body: str = data["body"]
    if len(body.encode("utf-8")) > NOTE_MAX_BYTES:
        raise ValueError(f"comment body exceeds {NOTE_MAX_BYTES} bytes")
    if not _has_prefix(body):
        raise ValueError(f"comment does not start with the required {NOTE_PREFIX!r} prefix")
    text = body.lstrip()[len(NOTE_PREFIX):]
    html_url = str(data.get("html_url") or "")
    if not html_url.startswith("https://github.com/") or _CTRL_RE.search(html_url) or any(c.isspace() for c in html_url):
        raise RuntimeError("comment payload has no valid html_url")
    login, uid = _user(data)
    me = _gh_api("user")
    me_id = me.get("id") if isinstance(me, dict) else None
    if uid is None or isinstance(me_id, bool) or not isinstance(me_id, int) or uid != me_id:   # True == 1
        raise ValueError(f"the comment's author ({login or 'unknown'}) is not the authenticated gh user; "
                         "only your own comments become notes")
    if not confirm({"author_login": login, "html_url": html_url,
                                            "text": _strip_controls(text, keep_newlines=True)}):
        raise ValueError("not confirmed; nothing stored")
    return add(text, target=target, days=days, source=f"gh:{html_url}",
               _extra={"author_login": login, "author_id": uid})
