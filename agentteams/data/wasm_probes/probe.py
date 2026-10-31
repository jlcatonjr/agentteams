"""probe.py — the Python guest deny probe (WASM sandbox plan P04). Runs INSIDE the WASM guest, never on the host.

``agentteams.wasm_guests.probe_language("python", ...)`` passes this file as guest source (``/src/main.py``) and
parses only its ``PROBE <name> DENIED|ALLOWED|ABSENT [detail]`` lines (C-4: the output is data). A probe that reads
something prints what it read, so a canary the host planted shows up in the output when a read succeeds.

Modes (argv[1]): ``deny`` (default) runs every row; ``spin`` loops forever (fuel trap); ``grow`` allocates 200 MiB
(memory limit).
"""

import os
import sys

CANARY_ENV = "AGENTTEAMS_WASM_CANARY"


def emit(name, verdict, detail=""):
    """Print one PROBE line.

    Args:
        name: The probe row.
        verdict: DENIED, ALLOWED or ABSENT.
        detail: A short, single-line detail.

    Returns:
        None.
    """
    text = " ".join(str(detail).split())[:200]
    print(f"PROBE {name} {verdict} {text}".rstrip(), flush=True)


def attempt(name, action):
    """Run *action*; an expected exception means DENIED (AttributeError / ImportError: ABSENT), a value means ALLOWED.

    Args:
        name: The probe row.
        action: A zero-argument callable returning what it obtained.

    Returns:
        None.
    """
    try:
        got = action()
    except (AttributeError, ImportError, NotImplementedError) as exc:
        emit(name, "ABSENT", type(exc).__name__)
    except (OSError, RuntimeError, ValueError, LookupError, TypeError) as exc:
        emit(name, "DENIED", type(exc).__name__)  # anything else crashes the probe, which then fails (closed)
    else:
        emit(name, "ALLOWED", repr(got))


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(256)


def _write(path, mode="w"):
    with open(path, mode, encoding="utf-8") as fh:
        fh.write("probe\n")
    return path


def _runtime_file():
    for name in sorted(os.listdir("/lib")):
        cand = f"/lib/{name}/os.py"
        if name.startswith("python3") and os.path.isfile(cand):
            return cand
    raise FileNotFoundError("/lib/python3.*/os.py")


def _env():
    if os.environ:
        return dict(os.environ)
    raise LookupError("empty environment")


def _system():
    code = os.system("true")
    if code != 0:
        raise OSError(code)
    return code


def _spawn():
    import subprocess
    return subprocess.run(["true"], check=True).returncode


def _connect():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(2)
        sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()
    return "connected"


def _dns():
    import socket
    return socket.getaddrinfo("localhost", 80)[:1]


def _thread():
    import threading
    box = []
    worker = threading.Thread(target=lambda: box.append(1))
    worker.start()
    worker.join()
    return box


def _scratch():
    _write("/scratch/probe-x")
    return _read("/scratch/probe-x")


def deny():
    """Run every probe row once.

    Returns:
        None.
    """
    import time
    attempt("fs-abs-file", lambda: _read("/etc/passwd"))
    attempt("fs-abs-list", lambda: os.listdir("/"))
    attempt("fs-rel", lambda: _read("c/probe-canary.txt"))
    attempt("fs-parent", lambda: _read("../parent-canary.txt"))
    attempt("fs-project", lambda: _read("/project/c/probe-canary.txt"))
    attempt("fs-write-runtime", lambda: _write(_runtime_file(), "a"))
    attempt("fs-write-src", lambda: _write("/src/main.py", "a"))
    attempt("fs-write-cwd", lambda: _write("probe-out.txt"))
    attempt("scratch-write", _scratch)
    attempt("env", _env)
    attempt("env-canary", lambda: os.environ[CANARY_ENV])
    attempt("net-connect", _connect)
    attempt("net-dns", _dns)
    attempt("proc-system", _system)
    attempt("proc-spawn", _spawn)
    attempt("thread", _thread)
    attempt("clock", time.time)
    attempt("random", lambda: os.urandom(8).hex())
    print("PROBE-STDERR", file=sys.stderr, flush=True)
    emit("stdio", "ALLOWED", "stdout+stderr")


def main(argv):
    """Dispatch on the mode in argv[1].

    Args:
        argv: ``sys.argv``.

    Returns:
        The exit code.
    """
    mode = argv[1] if len(argv) > 1 else "deny"
    if mode == "spin":
        while True:
            pass
    if mode == "grow":
        blob = bytearray(200 << 20)
        return 0 if blob else 1
    if mode == "deny":
        deny()
        return 0
    print(f"unknown mode {mode!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
