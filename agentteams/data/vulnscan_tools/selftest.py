"""The tools container's isolation probe (``tools-selftest``; stdlib only; runs inside the tools image).

Run with the exact flags of a confined operation. Prints one JSON object of named checks; every check must be true.
Inputs (environment): VULNSCAN_EXPECT_UID, VULNSCAN_HOST_HOME, VULNSCAN_RW, VULNSCAN_RO, and for ``--mode proxy``
VULNSCAN_PROXY (proxy host), VULNSCAN_ALLOWED and VULNSCAN_DENIED (``host:port``).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import socket
import sys

CREDENTIAL_PATHS = (".ssh", ".gnupg", ".aws", ".docker", ".kube", ".config/gh", ".config/agentteams/keys",
                    ".netrc", ".git-credentials", ".npmrc", ".pypirc")


def _status(field: str) -> str:
    with open("/proc/self/status", encoding="utf-8") as fh:
        return next((ln.split(":", 1)[1].strip() for ln in fh if ln.startswith(field + ":")), "")


def _write_fails(path: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
    except OSError as exc:
        return exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
    os.unlink(path)
    return False


def _write_works(directory: str) -> bool:
    path = os.path.join(directory, ".vulnscan-selftest")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.unlink(path)
    except OSError:
        return False
    return True


def _connect_fails(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError:
        return True
    return False


def _connect_status(proxy: str, endpoint: str) -> str:
    """Return the proxy's status line for ``CONNECT endpoint`` ("" when the proxy is unreachable)."""
    try:
        with socket.create_connection((proxy, 3128), timeout=10) as s:
            s.sendall(f"CONNECT {endpoint} HTTP/1.1\r\nHost: {endpoint}\r\n\r\n".encode())
            return s.recv(256).decode("latin-1").split("\r\n", 1)[0]
    except OSError:
        return ""


def checks(mode: str) -> dict[str, bool]:
    """Return every isolation check for ``mode`` (``none``: no network; ``proxy``: through the egress proxy)."""
    env = os.environ
    home = env.get("HOME", "")
    host_home = env.get("VULNSCAN_HOST_HOME", "/nonexistent-home")
    out = {
        "uid_is_operator_not_root": os.getuid() == int(env.get("VULNSCAN_EXPECT_UID", "-1")) and os.getuid() != 0,
        "caps_zero": int(_status("CapEff") or "1", 16) == 0,
        "no_new_privs": _status("NoNewPrivs") == "1",
        "rootfs_read_only": _write_fails("/usr/local/.vulnscan-probe") and _write_fails("/etc/.vulnscan-probe"),
        "home_is_empty_tmpfs": home == "/toolhome" and os.listdir(home) == [] and _write_works(home),
        "no_credential_paths": not any(os.path.lexists(os.path.join(p, c)) for p in (host_home, home, "/root")
                                       for c in CREDENTIAL_PATHS),
        "no_token_variables": not any(k.endswith(("_TOKEN", "_API_KEY")) or k in ("SSH_AUTH_SOCK", "GH_TOKEN")
                                      for k in env),
        "rw_mount_writable": _write_works(env.get("VULNSCAN_RW", "/nonexistent")),
        "ro_mount_read_only": _write_fails(os.path.join(env.get("VULNSCAN_RO", "/nonexistent"), ".probe")),
        "tools_present": all(shutil.which(t) for t in ("git", "git-lfs", "ssh", "ssh-keygen", "nc", "node", "npm", "bash")),
        "direct_egress_refused": _connect_fails("1.1.1.1", 443) and _connect_fails("8.8.8.8", 53),
    }
    if mode == "none":
        out["dns_unavailable"] = _connect_fails("registry.npmjs.org", 443)
    else:
        proxy = env.get("VULNSCAN_PROXY", "")
        out["proxy_refuses_unlisted"] = " 403 " in f"{_connect_status(proxy, env.get('VULNSCAN_DENIED', ''))} "
        out["proxy_refuses_ip_literal"] = " 403 " in f"{_connect_status(proxy, '1.1.1.1:443')} "
        out["proxy_allows_listed"] = " 200 " in f"{_connect_status(proxy, env.get('VULNSCAN_ALLOWED', ''))} "
    return out


def main() -> int:
    """Print the checks as JSON; exit 0 only when all pass."""
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("none", "proxy"), default="none")
    result = checks(p.parse_args().mode)
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
