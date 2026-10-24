"""An AF_UNIX listener for the egress gateway that admits only one uid (peer credentials from the kernel).

The gateway's default listener is TCP on the container network (``--listen HOST:PORT``): the agent
container is the only peer on that internal network. For host-side use (a gateway next to a CLI on
the same machine) ``--listen unix:/path/to.sock`` binds a Unix-domain socket instead:

- the socket's directory must exist, be a real directory owned by the gateway's uid and writable by
  no one else; the socket is created mode 0600 (a stale socket at the path is replaced, anything
  else there is refused);
- every connection's peer uid is read from the kernel (``SO_PEERCRED`` on Linux, ``LOCAL_PEERCRED``
  on macOS, as in baseAgent's ``serve/llm_gateway.py``) and a uid other than ``--peer-uid`` (default:
  the gateway's own) is closed before a byte is read. A peer whose uid cannot be read is refused too.
"""

from __future__ import annotations

import os
import socket
import socketserver
import stat
import struct
import sys
from collections.abc import Callable
from pathlib import Path

SUN_PATH_MAX = 100                     # macOS sun_path is 104 bytes, Linux 108
# macOS <sys/un.h>: SOL_LOCAL is 0; LOCAL_PEERCRED returns struct xucred {u_int version; uid_t uid; short ngroups;
# gid_t groups[16]} (76 bytes with padding), XUCRED_VERSION 0.
DARWIN_SOL_LOCAL = 0
DARWIN_LOCAL_PEERCRED = getattr(socket, "LOCAL_PEERCRED", 1)
DARWIN_XUCRED_SIZE = 76
DARWIN_XUCRED_VERSION = 0


def peer_uid(conn: socket.socket) -> int:
    """Return the uid of the process at the other end of a connected AF_UNIX socket, from the kernel.

    Raises:
        OSError: Not an AF_UNIX socket, or the platform cannot report it (the caller refuses the peer).
    """
    if conn.family != socket.AF_UNIX:
        raise OSError("peer credentials need an AF_UNIX socket")
    if hasattr(socket, "SO_PEERCRED"):                      # Linux: struct ucred {pid, uid, gid}
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", raw)[1]
    if sys.platform == "darwin":
        raw = conn.getsockopt(DARWIN_SOL_LOCAL, DARWIN_LOCAL_PEERCRED, DARWIN_XUCRED_SIZE)
        version, uid = struct.unpack_from("=II", raw)
        if version != DARWIN_XUCRED_VERSION:
            raise OSError("unexpected xucred version")
        return uid
    raise OSError("peer credentials are not available on this platform")


def peer_allowed(conn: socket.socket, allowed_uid: int) -> tuple[bool, int | None]:
    """Return ``(allowed, uid)``; ``uid`` is None (and the peer refused) when it cannot be read."""
    try:
        uid = peer_uid(conn)
    except OSError:
        return False, None
    return uid == allowed_uid, uid


def socket_path_problem(path: Path) -> str:
    """Return why ``path`` may not hold the gateway's socket, or ``""``. A stale socket there is fine."""
    if len(str(path).encode()) > SUN_PATH_MAX:
        return f"the socket path is longer than {SUN_PATH_MAX} bytes"
    parent = path.parent
    try:
        st = os.lstat(parent)
    except OSError as exc:
        return f"the socket directory cannot be used: {type(exc).__name__}"
    if not stat.S_ISDIR(st.st_mode):
        return "the socket directory is not a directory (or is a symbolic link)"
    if st.st_uid != os.getuid():
        return "the socket directory is not owned by the gateway's uid"
    if st.st_mode & 0o022:
        return "the socket directory is writable by group or others"
    if os.path.lexists(path) and not stat.S_ISSOCK(os.lstat(path).st_mode):
        return "the socket path exists and is not a socket"
    return ""


class UnixGatewayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """A threading HTTP server on an AF_UNIX socket that serves only peers of ``allowed_uid``."""

    daemon_threads = True

    def __init__(self, path: Path, handler: type, *, allowed_uid: int,
                 on_deny: Callable[[int | None], None] | None = None) -> None:
        """Bind ``path`` (mode 0600) after :func:`socket_path_problem` passes.

        Raises:
            ValueError: The path or its directory may not be used.
            OSError: The bind failed.
        """
        problem = socket_path_problem(path)
        if problem:
            raise ValueError(problem)
        if os.path.lexists(path):
            os.unlink(path)                                  # a stale socket from an earlier run
        self.path, self.allowed_uid, self.on_deny = path, allowed_uid, on_deny
        old = os.umask(0o177)                                # the socket is born 0600
        try:
            super().__init__(str(path), handler)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)

    def verify_request(self, request: socket.socket, client_address: object) -> bool:  # type: ignore[override]
        """Admit the connection only when the kernel reports the allowed uid."""
        ok, uid = peer_allowed(request, self.allowed_uid)
        if not ok and self.on_deny is not None:
            self.on_deny(uid)
        return ok

    def server_close(self) -> None:
        """Close the socket and remove its path."""
        super().server_close()
        if os.path.lexists(self.path) and stat.S_ISSOCK(os.lstat(self.path).st_mode):
            os.unlink(self.path)
