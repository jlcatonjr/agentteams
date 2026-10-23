"""The isolation self-test in-container probe (run by :func:`agentteams.vulnscan.agent_run.selftest`).

Plain Python executed inside the agent container with the agent's exact flags. It prints one line,
``SELFTEST {json}``, with one boolean per check. The text is part of what a passing self-test vouches for
(:func:`agentteams.vulnscan.agent_run.flags_sha256` hashes it). Split out of ``agent_run.py`` (CH-07).
"""

from __future__ import annotations

#: The probe source; ``%s`` / ``%d`` are the gateway host name and port.
PROBE_TEMPLATE = r"""
import http.client, json, os, urllib.request
r = {}
def no_access(p):
    try:
        os.listdir(p) if os.path.isdir(p) else open(p).read(1)
        return False
    except OSError:
        return True
home = os.path.expanduser("~")
r["no_credential_paths"] = all(no_access(os.path.join(home, x)) for x in
    (".ssh", ".config/gh", ".git-credentials", ".netrc", ".docker", ".npmrc", ".pypirc", ".aws", ".gnupg",
     ".config/agentteams/keys"))
r["no_token_env"] = not any(k in os.environ for k in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "AWS_ACCESS_KEY_ID")) \
    and os.environ.get("OPENROUTER_API_KEY") == "injected-by-gateway"
def reach(url):
    try:
        urllib.request.urlopen(url, timeout=6).read(1)
        return True
    except (OSError, http.client.HTTPException):   # network refusals only: any other fault fails the test
        return False
r["internet_blocked"] = not reach("https://example.com") and not reach("https://openrouter.ai/api/v1/models")
r["gateway_reachable"] = reach("http://%s:%d/healthz")
def can_write(p):
    try:
        with open(p, "w") as f: f.write("x")
        os.remove(p)
        return True
    except OSError:
        return False
r["readonly_rootfs"] = not can_write("/usr/local/probe")
r["readonly_run_dir"] = not can_write("/work/run/probe")
r["readonly_results"] = not can_write("/work/spool/results/probe")
r["spool_writable"] = can_write("/work/spool/requests/.probe")
def can_mkdir(d):
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return False
    return can_write(os.path.join(d, ".probe"))
# goose writes logs, sessions and config under its XDG dirs; a dir it cannot create stops it before any model call.
r["goose_dirs_writable"] = all(can_mkdir(os.path.join(os.environ.get(var) or home + dflt, "goose", sub))
                               for var, dflt, sub in (("XDG_STATE_HOME", "/.local/state", "logs"),
                                                      ("XDG_DATA_HOME", "/.local/share", "sessions"),
                                                      ("XDG_CONFIG_HOME", "/.config", "")))
st = open("/proc/self/status").read()
cap = [l.split()[1] for l in st.splitlines() if l.startswith("CapEff:")][0]
nnp = [l.split()[1] for l in st.splitlines() if l.startswith("NoNewPrivs:")][0]
r["no_capabilities"] = int(cap, 16) == 0
r["no_new_privs"] = nnp == "1"
r["non_root"] = os.getuid() != 0
print("SELFTEST " + json.dumps(r))
"""


def render(gateway: str, port: int) -> str:
    """Return the probe source for one gateway.

    Args:
        gateway: Gateway container name on the internal network.
        port: Gateway port.

    Returns:
        Python source.
    """
    return PROBE_TEMPLATE % (gateway, port)
