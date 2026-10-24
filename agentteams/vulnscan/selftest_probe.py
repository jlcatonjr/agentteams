"""The isolation self-test in-container probe (run by :func:`agentteams.vulnscan.agent_run.selftest`).

Plain Python executed inside the agent container with the agent's exact flags. It prints one line,
``SELFTEST {json}``, with one boolean per check. The text is part of what a passing self-test vouches for
(:func:`agentteams.vulnscan.agent_run.flags_sha256` hashes it). Split out of ``agent_run.py`` (CH-07). The
runtime-specific checks come from the driver (:mod:`agentteams.vulnscan.agent_drivers`).
"""

from __future__ import annotations

#: The probe source. ``%(gateway)s`` / ``%(port)d``: the gateway host name and port; ``%(key_env)s`` /
#: ``%(dummy)s``: the driver's model-key variable and the dummy value it must hold; ``%(cwd)s``: the
#: working directory, which must be empty; ``%(entries)s``: the
#: driver's own checks (:meth:`agentteams.vulnscan.agent_drivers.Driver.selftest_entries`), which may use the
#: helpers defined above them (``home``, ``can_write``, ``can_mkdir``). ``internet_blocked`` and
#: ``provider_apis_blocked`` try every model provider a driver could reach directly (OpenRouter,
#: Anthropic, OpenAI, xAI), whichever framework is configured.
PROBE_TEMPLATE = r"""
import http.client, json, os, urllib.error, urllib.request
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
    and os.environ.get("%(key_env)s") == "%(dummy)s"
def reach(url):
    try:
        urllib.request.urlopen(url, timeout=6).read(1)
        return True
    except (OSError, http.client.HTTPException):   # network refusals only: any other fault fails the test
        return False
def server_answers(url):   # an HTTP error status (401 without a key, 404) still proves the host is reachable
    try:
        urllib.request.urlopen(url, timeout=6).read(1)
        return True
    except urllib.error.HTTPError:
        return True
    except (OSError, http.client.HTTPException):
        return False
r["internet_blocked"] = not server_answers("https://example.com") and not server_answers("https://openrouter.ai/api/v1/models")
r["provider_apis_blocked"] = not any(server_answers(u) for u in ("https://api.anthropic.com/v1/models", "https://api.openai.com/v1/models",
                                                        "https://api.x.ai/v1/models"))   # every driver's provider
r["gateway_reachable"] = reach("http://%(gateway)s:%(port)d/healthz")
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
r["cwd_empty"] = os.getcwd() == "%(cwd)s" and os.listdir(".") == []   # revision 7: never the team mount
def can_mkdir(d):
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return False
    return can_write(os.path.join(d, ".probe"))
%(entries)s
st = open("/proc/self/status").read()
cap = [l.split()[1] for l in st.splitlines() if l.startswith("CapEff:")][0]
nnp = [l.split()[1] for l in st.splitlines() if l.startswith("NoNewPrivs:")][0]
r["no_capabilities"] = int(cap, 16) == 0
r["no_new_privs"] = nnp == "1"
r["non_root"] = os.getuid() != 0
print("SELFTEST " + json.dumps(r))
"""


def render(gateway: str, port: int, *, key_env: str = "OPENROUTER_API_KEY", dummy: str = "injected-by-gateway",
           entries: list[str] | tuple[str, ...] = (), cwd: str = "/work/cwd") -> str:
    """Return the probe source for one gateway and one runtime driver.

    Args:
        gateway: Gateway container name on the internal network.
        port: Gateway port.
        key_env: The driver's model-key variable (it must hold ``dummy``).
        dummy: The dummy key value.
        entries: The driver's own checks, inserted before the capability checks.
        cwd: The container's working directory, which must be empty (``cwd_empty``).

    Returns:
        Python source.
    """
    return PROBE_TEMPLATE % {"gateway": gateway, "port": port, "key_env": key_env, "dummy": dummy, "cwd": cwd,
                             "entries": "\n".join(entries)}
