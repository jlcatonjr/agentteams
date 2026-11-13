"""test_redteam_verifiers_data.py — F-1 sensitivity tests and negative controls for non-Python
enforcement modules under ``agentteams/data/`` that no existing suite drove directly.

Same pairing discipline as ``tests/test_redteam_verifiers.py``: a **sensitivity test** shows the
module's decision changes when the input it judges changes; a **negative control** shows it does
not change for input it should ignore. Each script runs for real (``bash``/``sh``) against a
``tmp_path`` fixture world whose external tools (``docker``, ``curl``, ``tar`` ...) are fakes on
``PATH``. Nothing here reaches a network, a daemon or a real host.

Covered here:

* ``server_kit/L5-containers.sh`` — the Rule S-10 cooldown HOLD on a freshly built image;
* ``server_kit/L4-python-app.sh`` — a negative control for its major-version refusal;
* ``vulnscan_tools/git-credential-vulnscan`` — releases the token only for https + the pinned host;
* ``vulnscan_tools/Dockerfile`` and ``vulnscan_agent/Dockerfile`` — the per-architecture sha256 gate
  on each downloaded release asset. The ``RUN`` body is taken verbatim from the Dockerfile and run
  under ``/bin/sh`` (what ``RUN`` uses); the only rewrite is ``/tmp/`` -> the test's tmp dir.

Fixture checksums are always well-formed 64-hex values: macOS ``sha256sum -c`` exits 0 on a
*malformed* line (it only warns), unlike GNU coreutils in the Debian build image, so a short
"wrong" checksum would test the platform, not the Dockerfile.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_vulnscan_server_kit_levels import _fake_python
from tests.test_vulnscan_server_kit_levels import _host as _level_host

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "agentteams" / "data"
BASH = os.environ.get("AGENTTEAMS_TEST_BASH") or ("/bin/bash" if Path("/bin/bash").is_file() else
                                                   (shutil.which("bash") or "bash"))
SH = "/bin/sh"


def _tool(bindir: Path, name: str, body: str) -> Path:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# server_kit/L5-containers.sh — the S-10 cooldown hold
# ---------------------------------------------------------------------------

FAKE_DOCKER = r"""#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  compose) exit 0;;
  pull) exit 0;;
  ps) case "$*" in *--filter*) echo "web Up";; *) echo "web nginx:1 c0ffee";; esac;;
  inspect)
    case "$3" in
      *working_dir*) echo "$FAKE_WD";;
      *config_files*) echo "$FAKE_WD/compose.yml";;
      *'"com.docker.compose.project"'*) echo proj;;
      *compose.service*) echo websvc;;
      *.Image*) echo sha256:old;;
    esac;;
  image)
    case "$4" in
      *.Id*) echo sha256:new;;
      *.Created*) if [ "$5" = sha256:old ]; then echo 2020-01-01T00:00:00Z; else echo "$FAKE_CREATED"; fi;;
    esac;;
esac
exit 0
"""


def _l5(tmp_path: Path, created: dt.date, *args: str) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _tool(bindir, "docker", FAKE_DOCKER)
    _tool(bindir, "sleep", "#!/bin/sh\nexit 0\n")
    _tool(bindir, "hostname", "#!/bin/sh\necho host1\n")
    wd = tmp_path / "compose"
    wd.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    log.write_text("", encoding="utf-8")
    env = {"PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin", "LC_ALL": "C", "FAKE_LOG": str(log),
           "FAKE_WD": str(wd), "FAKE_CREATED": f"{created.isoformat()}T00:00:00Z"}
    proc = subprocess.run([BASH, str(DATA / "server_kit" / "L5-containers.sh"), *args], capture_output=True,
                          text=True, env=env, timeout=60, check=False)
    return proc, log.read_text(encoding="utf-8")


def _recreated(log: str) -> bool:
    return "up -d --no-deps websvc" in log


def test_l5_holds_a_freshly_built_image_and_recreates_an_aged_one(tmp_path: Path) -> None:
    """Sensitivity: only the new image's build date changes, and the recreate decision flips."""
    today = dt.datetime.now(dt.UTC).date()
    fresh, fresh_log = _l5(tmp_path, today - dt.timedelta(days=2), "--apply")
    assert fresh.returncode == 0, (fresh.stdout, fresh.stderr)
    assert "HOLD" in fresh.stdout and not _recreated(fresh_log), (fresh.stdout, fresh_log)
    aged, aged_log = _l5(tmp_path, today - dt.timedelta(days=60), "--apply")
    assert "HOLD" not in aged.stdout and _recreated(aged_log), (aged.stdout, aged_log)
    assert "OK     recreated proj/websvc" in aged.stdout


def test_l5_cooldown_flags_do_not_change_an_aged_images_recreate(tmp_path: Path) -> None:
    """Negative control: past the cooldown, --allow-fresh or a shorter cooldown changes nothing."""
    aged = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=60)
    outcomes = []
    for extra in ([], ["--allow-fresh"], ["--cooldown-days", "7"]):
        proc, log = _l5(tmp_path, aged, "--apply", *extra)
        assert proc.returncode == 0, (extra, proc.stdout, proc.stderr)
        outcomes.append(("HOLD" in proc.stdout, _recreated(log)))
    assert outcomes == [(False, True)] * 3


# ---------------------------------------------------------------------------
# server_kit/L4-python-app.sh — negative control for the major-version refusal
# ---------------------------------------------------------------------------

def test_l4_level_plans_a_same_major_pin_without_major_allowed(tmp_path: Path) -> None:
    """Negative control: a pin inside the installed major is planned with no --major-allowed.

    The sensitivity side is tests/test_vulnscan_server_kit_levels.py::
    test_l4_level_refuses_an_unallowed_major_jump (idna 2.10 -> 3.10 refused).
    """
    host = _level_host(tmp_path, {})
    app = tmp_path / "app"
    app.mkdir()
    py = _fake_python(tmp_path, {"idna": "3.7"})
    res = subprocess.run([BASH, str(DATA / "server_kit" / "L4-python-app.sh"), "--level", "--unit", "a.service",
                          "--dir", str(app), "--python", str(py), "--as", "svc", "--pin", "idna==3.10"],
                         capture_output=True, text=True, env=host, timeout=60, check=False)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "crosses a major version" not in res.stderr
    assert "PLAN\tpins\ta.service\tidna\t3.7\t3.10" in res.stdout, res.stdout


# ---------------------------------------------------------------------------
# The vulnscan git helper — releases the value only for https + VULNSCAN_GIT_HOST
# ---------------------------------------------------------------------------

_TOKEN = "tok-" + "z9" * 6   # synthetic placeholder value


def _helper(stdin: str, *, op: str = "get", host: str = "github.com") -> str:
    proc = subprocess.run([SH, str(DATA / "vulnscan_tools" / "git-credential-vulnscan"), op], input=stdin,
                          capture_output=True, text=True, timeout=30, check=False,
                          env={"PATH": "/usr/bin:/bin", "VULNSCAN_GIT_HOST": host, "VULNSCAN_GIT_TOKEN": _TOKEN})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _released(out: str) -> bool:
    lines = out.splitlines()
    return len(lines) == 2 and lines[0] == "username=x-access-token" and lines[1].endswith("=" + _TOKEN)


def test_git_credential_helper_withholds_the_token_off_host_or_off_https() -> None:
    """Sensitivity: the value is released for https://github.com and withheld when host, protocol or op change."""
    assert _released(_helper("protocol=https\nhost=github.com\n\n"))
    assert _helper("protocol=https\nhost=evil.example\n\n") == ""
    assert _helper("protocol=http\nhost=github.com\n\n") == ""
    assert _helper("protocol=https\nhost=github.com.evil.example\n\n") == ""
    assert _helper("protocol=https\nhost=github.com\n\n", op="store") == ""
    assert _helper("protocol=https\nhost=github.com\n\n", host="") == ""


def test_git_credential_helper_ignores_unrelated_request_fields() -> None:
    """Negative control: path/username fields and field order do not change the answer."""
    want = _helper("protocol=https\nhost=github.com\n\n")
    assert _released(want)
    assert _helper("host=github.com\nusername=someone\npath=org/repo.git\nprotocol=https\n\n") == want
    # a field after the blank-line terminator is not part of the request
    assert _helper("protocol=https\nhost=github.com\n\nhost=evil.example\n") == want


# ---------------------------------------------------------------------------
# Dockerfiles — the sha256 gate on downloaded release assets
# ---------------------------------------------------------------------------

def _run_bodies(dockerfile: Path) -> list[str]:
    """Return each RUN instruction's shell body, continuation lines joined (as Docker does)."""
    bodies: list[str] = []
    current: list[str] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if not current and (not line.strip() or line.lstrip().startswith("#")):
            continue
        stripped = line.rstrip()
        cont = stripped.endswith("\\")
        current.append(stripped[:-1] if cont else stripped)
        if not cont:
            instr = " ".join(current).strip()
            current = []
            if instr.startswith("RUN "):
                bodies.append(instr[4:])
    return bodies


def _body_with(dockerfile: Path, marker: str) -> str:
    (body,) = [b for b in _run_bodies(dockerfile) if marker in b and "sha256sum -c" in b]
    return body


_PASSTHROUGH = ("tar", "mkdir", "ln", "rm", "install", "node", "npm", "apt-get", "goose", "claude", "codex")


def _gate_world(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """Fake bin dir: curl writes a fixed payload to its -o target; install/tar/... only log."""
    sha = shutil.which("sha256sum")
    if not sha:
        pytest.skip("sha256sum is not installed on this platform (the Dockerfile gate is built on it)")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    payload = b"release-asset-bytes\n"
    (tmp_path / "payload").write_bytes(payload)
    _tool(bindir, "curl", '#!/bin/sh\necho "curl $*" >> "$LOG"\nwhile [ $# -gt 0 ]; do\n'
                          '  if [ "$1" = -o ]; then cp "$PAYLOAD" "$2"; fi; shift\ndone\n')
    for name in _PASSTHROUGH:
        _tool(bindir, name, f'#!/bin/sh\necho "{name} $*" >> "$LOG"\n')
    _tool(bindir, "find", '#!/bin/sh\necho /fake/goose\n')
    for real in ("cp", "head", "cut", "sed"):
        target = shutil.which(real, path="/usr/bin:/bin")
        if target:
            (bindir / real).symlink_to(target)
    (bindir / "sha256sum").symlink_to(sha)
    return bindir, log, payload


def _run_gate(tmp_path: Path, body: str, bindir: Path, log: Path, **env: str) -> subprocess.CompletedProcess[str]:
    script = body.replace("/tmp/", f"{tmp_path}/")
    log.write_text("", encoding="utf-8")
    return subprocess.run([SH, "-c", script], capture_output=True, text=True, timeout=60, check=False,
                          env={"PATH": str(bindir), "LOG": str(log), "PAYLOAD": str(tmp_path / "payload"), **env})


_WRONG = "ab" * 32   # well-formed 64-hex, never the payload's digest

# case -> (Dockerfile, marker in the RUN body, version build args, per-arch sha arg names or None,
#          the step that runs only after the gate passes)
_GATES = {
    "tools-node": (DATA / "vulnscan_tools" / "Dockerfile", "node.tar.xz",
                   {"NODE_VERSION": "24.0.0"}, ("NODE_SHA256_AMD64", "NODE_SHA256_ARM64"), "tar "),
    "agent-goose": (DATA / "vulnscan_agent" / "Dockerfile", "goose.tar.bz2", {"CLI_VERSION": "1.0.0"}, None, "tar "),
    "agent-claude": (DATA / "vulnscan_agent" / "Dockerfile", "/tmp/claude ", {"CLI_VERSION": "1.0.0"}, None,
                     "install "),
    "agent-codex": (DATA / "vulnscan_agent" / "Dockerfile", "codex.tar.gz", {"CLI_VERSION": "1.0.0"}, None, "tar "),
}


def _gate_env(case: str, good: str, *, arch: str, right_arch_ok: bool) -> dict[str, str]:
    _df, _marker, base, per_arch, _after = _GATES[case]
    env = {**base, "TARGETARCH": arch}
    if per_arch:
        amd, arm = per_arch
        chosen, other = (amd, arm) if arch == "amd64" else (arm, amd)
        env[chosen] = good if right_arch_ok else _WRONG
        env[other] = _WRONG if right_arch_ok else good
    else:
        env["CLI_SHA256"] = good if right_arch_ok else _WRONG
    return env


@pytest.mark.parametrize("case", sorted(_GATES))
def test_dockerfile_sha256_gate_stops_a_mismatched_asset(tmp_path: Path, case: str) -> None:
    """Sensitivity: the same RUN body installs the asset when the pinned sha256 matches and stops before
    unpacking or installing it when it does not (for tools-node: when only the OTHER arch's pin matches)."""
    dockerfile, marker, _base, _per_arch, after = _GATES[case]
    bindir, log, payload = _gate_world(tmp_path)
    good = hashlib.sha256(payload).hexdigest()
    body = _body_with(dockerfile, marker)
    ok = _run_gate(tmp_path, body, bindir, log, **_gate_env(case, good, arch="amd64", right_arch_ok=True))
    assert ok.returncode == 0, (ok.stdout, ok.stderr, log.read_text())
    assert after in log.read_text()
    for arch in ("amd64", "arm64"):
        bad = _run_gate(tmp_path, body, bindir, log, **_gate_env(case, good, arch=arch, right_arch_ok=False))
        assert bad.returncode != 0, (arch, bad.stdout, bad.stderr)
        calls = log.read_text()
        assert "curl " in calls and after not in calls, (arch, calls)


@pytest.mark.parametrize("case", sorted(_GATES))
def test_dockerfile_sha256_gate_ignores_the_unused_architecture(tmp_path: Path, case: str) -> None:
    """Negative control: with the selected arch's pin correct, the other arch's (garbage) pin and the
    version string do not change the outcome."""
    dockerfile, marker, base, _per_arch, after = _GATES[case]
    bindir, log, payload = _gate_world(tmp_path)
    good = hashlib.sha256(payload).hexdigest()
    body = _body_with(dockerfile, marker)
    for arch in ("amd64", "arm64"):
        for suffix in ("", "-rc1"):
            env = {**_gate_env(case, good, arch=arch, right_arch_ok=True),
                   **{k: v + suffix for k, v in base.items()}}
            res = _run_gate(tmp_path, body, bindir, log, **env)
            assert res.returncode == 0, (arch, suffix, res.stdout, res.stderr)
            assert after in log.read_text()
