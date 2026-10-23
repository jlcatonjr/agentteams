"""Storage for the remediation agent's end-of-run report.

The agent cannot write files. It submits its report through the spool (internal op ``report``) and
the out-of-sandbox executor hands it to :func:`save`. The review shows it (:func:`render_for_review`)
and a publish grant may publish it.

The report is **untrusted agent narration**. It is stored verbatim but inert: the body sits inside a
backtick fence longer than any backtick run it contains (:func:`fence`), so no Markdown in it renders
— no links, images, HTML or @mentions.

Layout: ``<run_dir>/agent-report/report.md`` (directory mode 700, file mode 600). The file is a short
header that this module writes, a blank line, then the fenced body. :func:`save` writes the header with
the request id and ``run state: pending``. :func:`finalize` rewrites it with the run's final state at
the end of the run, and only when that run submitted it. Before each agent run an earlier run's report is
renamed to ``<run id>.report.md`` beside it (:func:`archive`, via :mod:`.run_records`), so ``report.md`` is
always the latest run's own. Every write is atomic (temp file plus rename) and goes through a directory
descriptor opened with ``O_NOFOLLOW``, so a symlink planted at the directory or the file is refused,
not followed. The header records the body's SHA-256, and :func:`load` refuses a file whose body no
longer matches it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

REPORT_MAX_BYTES = 200_000
REPORT_DIR = "agent-report"
REPORT_FILE = "report.md"
PENDING_STATE = "pending"
REVIEW_HEADING = "## Agent report (untrusted, verbatim)"
NO_REPORT_LINE = "_No report was submitted for this run._"
NO_RUN_REPORT_LINE = ("_The latest agent run ({run_id}) submitted no report. The stored report is an earlier "
                      "run's and is not shown._")

_TITLE = "Agent report — stored by the vulnscan executor; the fenced body below is untrusted agent text."
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STATE_RE = re.compile(r"^[a-z][a-z-]{0,31}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_HEADER_KEYS = ("run state", "request id", "submitted", "finalized", "body bytes", "body sha256")
_MAX_FILE_BYTES = 4 * REPORT_MAX_BYTES + 4096   # fence + header slack; a larger file is not ours
_BACKTICK_RUN = re.compile(r"`+")


def fence(text: str) -> str:
    """Wrap text in a backtick code fence that nothing inside it can close.

    The fence is one backtick longer than the longest backtick run in ``text`` (minimum 3), so
    the whole text renders as a literal code block.

    Args:
        text: Arbitrary text.

    Returns:
        ``<fence>text\\n<body>\\n<fence>\\n`` (the body gains a trailing newline if it lacks one).
    """
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN.finditer(text)), default=0)
    bar = "`" * max(3, longest + 1)
    body = text if text.endswith("\n") or not text else text + "\n"
    return f"{bar}text\n{body}{bar}\n"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@contextlib.contextmanager
def _report_dir(run_dir: Path, *, create: bool) -> Iterator[int | None]:
    """Yield a descriptor for ``<run_dir>/agent-report`` opened without following symlinks.

    Args:
        run_dir: The run directory (must exist).
        create: Create the report directory (mode 700) when it is missing.

    Yields:
        The directory descriptor, or None when it is missing and ``create`` is false.

    Raises:
        ValueError: The report directory is a symlink, not a directory, not owned by you, or
            group/world accessible.
        FileNotFoundError: ``run_dir`` does not exist.
    """
    run_fd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir(REPORT_DIR, 0o700, dir_fd=run_fd)
        try:
            fd = os.open(REPORT_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=run_fd)
        except FileNotFoundError:
            if create:
                raise
            fd = None
        except OSError as exc:   # ELOOP (symlink) or ENOTDIR
            raise ValueError(f"{run_dir / REPORT_DIR} is not a plain directory (symlink?): {exc}") from exc
    finally:
        os.close(run_fd)
    if fd is None:
        yield None
        return
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise ValueError(f"{run_dir / REPORT_DIR} must be owned by you with mode 700")
        yield fd
    finally:
        os.close(fd)


def _read_file(dfd: int) -> str | None:
    try:
        fd = os.open(REPORT_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"{REPORT_FILE} could not be opened safely (symlink?): {exc}") from exc
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"{REPORT_FILE} is not a regular file")
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise ValueError(f"{REPORT_FILE} must be owned by you with mode 600")
        data = fh.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError(f"{REPORT_FILE} is larger than any report this module writes")
    return data.decode("utf-8")


def _write_file(dfd: int, content: str) -> None:
    tmp = f".{REPORT_FILE}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
    replaced = False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, REPORT_FILE, src_dir_fd=dfd, dst_dir_fd=dfd)
        replaced = True
    finally:
        if not replaced:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp, dir_fd=dfd)


def _header(fields: dict[str, str]) -> str:
    lines = [_TITLE, ""] + [f"- {k}: {fields[k]}" for k in _HEADER_KEYS]
    return "\n".join(lines) + "\n"


def _parse(content: str) -> tuple[dict[str, str], str]:
    """Split a stored report into its header fields and fenced body, verifying both.

    Args:
        content: The whole stored file.

    Returns:
        (header fields, fenced body).

    Raises:
        ValueError: The header is malformed or the body does not match its recorded hash.
    """
    head, sep, body = content.partition("\n\n- ")
    if not sep or head != _TITLE:
        raise ValueError("the stored report header is malformed")
    meta_text, sep, fenced = ("- " + body).partition("\n\n")
    if not sep:
        raise ValueError("the stored report header is malformed")
    fields: dict[str, str] = {}
    for line in meta_text.split("\n"):
        key, colon, value = line[2:].partition(": ")
        if not line.startswith("- ") or not colon or key not in _HEADER_KEYS or key in fields:
            raise ValueError("the stored report header is malformed")
        fields[key] = value
    if tuple(fields) != _HEADER_KEYS:
        raise ValueError("the stored report header is malformed")
    checks = (
        _STATE_RE.match(fields["run state"]), _REQUEST_ID_RE.match(fields["request id"]),
        _TS_RE.match(fields["submitted"]), fields["finalized"] == "-" or _TS_RE.match(fields["finalized"]),
        fields["body bytes"].isdigit(), _SHA_RE.match(fields["body sha256"]),
    )
    if not all(checks):
        raise ValueError("the stored report header is malformed")
    raw = fenced.encode("utf-8")
    if len(raw) != int(fields["body bytes"]) or hashlib.sha256(raw).hexdigest() != fields["body sha256"]:
        raise ValueError("the stored report body does not match its recorded hash — it was altered")
    return fields, fenced


def save(run_dir: Path, markdown: str, *, request_id: str) -> Path:
    """Store the agent's report for a run, fenced and inert. A later save replaces it.

    Args:
        run_dir: The run directory (must exist).
        markdown: The agent's report text (untrusted).
        request_id: The spool request id that carried it (or an operator-run id).

    Returns:
        The path of the stored report.

    Raises:
        ValueError: ``markdown`` is over :data:`REPORT_MAX_BYTES` UTF-8 bytes, contains NUL or is
            not encodable; ``request_id`` is malformed; or the report directory or file is unsafe
            (a symlink, foreign-owned or group/world accessible).
        FileNotFoundError: ``run_dir`` does not exist.
    """
    if not isinstance(markdown, str):
        raise ValueError("the report must be a string")
    if "\x00" in markdown:
        raise ValueError("the report contains a NUL character")
    size = len(markdown.encode("utf-8"))
    if size > REPORT_MAX_BYTES:
        raise ValueError(f"the report is {size} bytes; the limit is {REPORT_MAX_BYTES}")
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        raise ValueError(f"malformed request id {str(request_id)[:80]!r}")
    fenced = fence(markdown)
    raw = fenced.encode("utf-8")
    fields = {"run state": PENDING_STATE, "request id": request_id, "submitted": _now(), "finalized": "-",
              "body bytes": str(len(raw)), "body sha256": hashlib.sha256(raw).hexdigest()}
    with _report_dir(Path(run_dir), create=True) as dfd:
        if dfd is None:   # unreachable: create=True always yields a descriptor
            raise FileNotFoundError(run_dir / REPORT_DIR)
        _write_file(dfd, _header(fields) + "\n" + fenced)
    return Path(run_dir) / REPORT_DIR / REPORT_FILE


def finalize(run_dir: Path, run_state: str, *, since: str | None = None) -> dict[str, str] | None:
    """Record the run's final state in the stored report's header (end of run).

    Does nothing when no report was submitted, or (with ``since``) when the stored report was
    submitted before the run started: it is an earlier run's, never this run's.

    Args:
        run_dir: The run directory.
        run_state: The final state, one of ``agent_run.RUN_STATES``.
        since: The run's start (ISO 8601); a report submitted before it is left alone.

    Returns:
        The finalized report's header fields, or None when there is no report of this run.

    Raises:
        ValueError: ``run_state`` is not a known run state, or the stored report is unsafe,
            malformed or altered.
        FileNotFoundError: ``run_dir`` does not exist.
    """
    from agentteams.vulnscan.agent_run import RUN_STATES   # lazy: agent_run imports executor, which imports us

    if run_state not in RUN_STATES:
        raise ValueError(f"unknown run state {str(run_state)[:40]!r}; expected one of {RUN_STATES}")
    with _report_dir(Path(run_dir), create=False) as dfd:
        if dfd is None:
            return None
        content = _read_file(dfd)
        if content is None:
            return None
        fields, fenced = _parse(content)
        if since is not None and _before(fields["submitted"], since):
            return None
        fields.update({"run state": run_state, "finalized": _now()})
        _write_file(dfd, _header(fields) + "\n" + fenced)
    return fields


def _before(submitted: str, since: str) -> bool:
    """Return whether a header ``submitted`` time is before ``since`` (compared to the second)."""
    try:
        return datetime.fromisoformat(submitted) < datetime.fromisoformat(since).replace(microsecond=0)
    except ValueError:   # an unparseable start binds nothing to this run
        return True


def archive(run_dir: Path, name: str) -> str | None:
    """Rename the stored report to ``<name>.report.md`` beside it (an earlier run's report, kept).

    Args:
        run_dir: The run directory.
        name: The archived run's name (a run id).

    Returns:
        The archived file name, or None when there was no report.

    Raises:
        ValueError: ``name`` is malformed, or the report directory is unsafe.
        FileExistsError: The archive name is taken.
    """
    if not _REQUEST_ID_RE.match(name) or name.startswith("."):
        raise ValueError(f"malformed archive name {name[:80]!r}")
    target = f"{name}.report.md"
    with _report_dir(Path(run_dir), create=False) as dfd:
        if dfd is None:
            return None
        with contextlib.suppress(FileNotFoundError):
            os.stat(target, dir_fd=dfd, follow_symlinks=False)
            raise FileExistsError(target)
        try:
            os.rename(REPORT_FILE, target, src_dir_fd=dfd, dst_dir_fd=dfd)
        except FileNotFoundError:
            return None
    return target


def load(run_dir: Path) -> str | None:
    """Return the stored report: the header, a blank line, then the fenced body.

    Args:
        run_dir: The run directory.

    Returns:
        The report text, or None when no report was submitted (or ``run_dir`` is missing).

    Raises:
        ValueError: The stored report is unsafe (symlink, foreign-owned, loose mode), malformed, or
            its body no longer matches the recorded hash.
    """
    if not Path(run_dir).is_dir():
        return None
    with _report_dir(Path(run_dir), create=False) as dfd:
        if dfd is None:
            return None
        content = _read_file(dfd)
    if content is None:
        return None
    _parse(content)
    return content


def _latest_run(run_dir: Path) -> dict[str, str]:
    """Return ``run_id`` and ``started`` from ``<run_dir>/agent/status.json`` (the latest agent run), or {}."""
    try:
        data = json.loads((Path(run_dir) / "agent" / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    rid, started = data.get("run_id"), data.get("started")
    return {"run_id": rid if isinstance(rid, str) and _REQUEST_ID_RE.match(rid) else "?",
            "started": started} if isinstance(started, str) else {}


def render_for_review(run_dir: Path) -> str:
    """Return the review section for the agent's report, bound to the latest agent run.

    A stored report submitted before the latest run (``<run_dir>/agent/status.json``) started is an
    earlier run's: it is not shown as this run's, and the section says the run submitted none.

    Args:
        run_dir: The run directory.

    Returns:
        :data:`REVIEW_HEADING`, a blank line, then :func:`load`'s text, or a "no report" line.

    Raises:
        ValueError: As :func:`load`.
    """
    text = load(run_dir)
    latest = _latest_run(run_dir)
    if text is not None and latest:
        fields, _ = _parse(text)
        if _before(fields["submitted"], latest["started"]):
            return f"{REVIEW_HEADING}\n\n{NO_RUN_REPORT_LINE.format(run_id=latest['run_id'])}\n"
    body = text if text is not None else NO_REPORT_LINE + "\n"
    return f"{REVIEW_HEADING}\n\n{body}"
