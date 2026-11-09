"""The in-sandbox survivor check for bwrap runs (#262 item 2).

bwrap's PID 1 exits with the command and the namespace teardown kills what is left, so a check from outside the
sandbox races the teardown. The shim here counts (and kills) the leftovers from inside, before teardown, and reports
on a socket the command cannot reopen. Split out of :mod:`agentteams.confinement` (CH-07) and integrity-pinned with it.
"""

from __future__ import annotations

import os
import re
import select
import socket
import time

from agentteams._sandbox_profiles import ConfinementError

#: #262 item 2. bwrap's PID 1 exits the moment the command exits and the namespace teardown kills anything left, so a
#: post-exit ``killpg`` from outside races the teardown and usually finds nothing. This shim runs inside the sandbox:
#: it runs the command with the report fd closed, then walks ``/proc`` with shell builtins only (nothing is forked):
#: every process other than itself and its ancestors (bwrap's init, and any intermediate bwrap) that has a live thread
#: is counted and SIGKILLed, and the walk repeats until one finds nothing, so a fork-and-exit chain cannot slip
#: between walks. It writes ``survivors=N`` to the report fd and exits with the command's status. The fd is the child
#: end of a ``socketpair`` placed at a single-digit number (dash cannot address fds above 9): a socket cannot be
#: reopened through ``/proc/<pid>/fd`` (ENXIO), so the command cannot write a report. What remains: a process allowed
#: to ptrace the shim or bwrap's init (``kernel.yama.ptrace_scope=0``) could take the fd with ``pidfd_getfd``.
_SURVIVOR_SHIM = r"""fd=$1; shift; eval "\"\$@\" $fd>&-"; rc=$?
anc=" $$ "; p=$$
while [ "$p" -gt 1 ] 2>/dev/null; do
  read -r st < "/proc/$p/stat" || break
  st=${st##*) }; set -- $st; p=$2; anc="$anc$p "
done
total=0; walks=0; n=1
while [ "$n" -gt 0 ] && [ "$walks" -lt 50 ]; do
  walks=$((walks+1)); n=0
  for d in /proc/[0-9]*; do
    q=${d#/proc/}
    case "$anc" in *" $q "*) continue ;; esac
    live=0
    for t in "$d"/task/[0-9]*; do
      read -r st < "$t/stat" 2>/dev/null || continue
      st=${st##*) }; set -- $st
      if [ "$1" != Z ]; then live=1; break; fi
    done
    [ "$live" = 1 ] || continue
    kill -9 "$q" 2>/dev/null
    n=$((n+1))
  done
  total=$((total+n))
done
[ "$n" -eq 0 ] || total=$((total+1))
eval "echo survivors=$total >&$fd"; exit "$rc"
"""


def survivor_channel() -> tuple[int, int]:
    """Open the survivor report channel: ``(read_fd, child_fd)``.

    A ``socketpair``, because a socket cannot be reopened through ``/proc/<pid>/fd`` (a pipe can). The child end is
    moved to a free fd from 3 to 9 when there is one, so the shim can run under ``/bin/sh``; otherwise it keeps its
    number and :func:`survivor_shim_argv` uses ``/bin/bash``. Pass ``child_fd`` to :func:`wrap` as ``survivor_fd``
    and to the child with ``pass_fds``; close it in the parent once the child has started.

    Returns:
        The parent's read fd and the child's write fd.

    Raises:
        OSError: The socket pair could not be created.
    """
    parent, child = socket.socketpair()
    read_fd, write_fd = parent.detach(), child.detach()
    low = next((n for n in range(3, 10) if not _is_open(n)), None)
    if low is None:
        return read_fd, write_fd  # survivor_shim_argv runs it under bash, which addresses any fd
    os.dup2(write_fd, low)
    os.close(write_fd)
    return read_fd, low


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def survivor_shim_argv(fd: int, argv: list[str]) -> list[str]:
    """Prefix *argv* with the in-sandbox survivor shim reporting on *fd*.

    ``/bin/sh`` (dash on Debian and Ubuntu) only addresses fds 0-9 in redirections, so a higher fd runs the same
    POSIX script under ``/bin/bash``. ``/`` is bound read-only into the sandbox, so the host's shell is the one used.

    Args:
        fd: The child fd from :func:`survivor_channel`.
        argv: The command.

    Returns:
        The shimmed argv.

    Raises:
        ConfinementError: *fd* is above 9 and ``/bin/bash`` does not exist.
    """
    shell = "/bin/sh"
    if fd > 9:
        if not os.path.exists("/bin/bash"):
            raise ConfinementError(f"the survivor report fd {fd} needs /bin/bash, which is missing")
        shell = "/bin/bash"
    return [shell, "-c", _SURVIVOR_SHIM, "agentteams-survivor-check", str(fd), *argv]


def read_survivor_report(fd: int, *, timeout: float = 5.0) -> str | None:
    """Read the in-sandbox survivor report from *fd* (then close it); why the run fails, or None when it is clean.

    Only the exact bytes ``survivors=<digits>\n`` are a report: anything else (a second line, a sign, extra bytes, no
    newline, nothing at all) fails closed.

    Args:
        fd: The read end from :func:`survivor_channel`.
        timeout: Seconds to wait for the writer to finish.

    Returns:
        None when the shim counted no survivors; otherwise the reason the run fails, naming which path fired (the
        shim counted N, or no well-formed report arrived: fail closed).

    Raises:
        OSError: Reading *fd* failed (it is closed either way).
    """
    data = b""
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(data) <= 64:
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(fd, 128)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    m = re.fullmatch(rb"survivors=([0-9]+)\n", data)
    if m is None:
        return f"no well-formed survivor report from inside the sandbox (got {data[:40]!r})"
    count = int(m.group(1))
    return f"the in-sandbox check counted and killed {count}" if count else None
