#!/usr/bin/env python3
"""gen_vulnscan_screenshots.py — terminal captures and the architecture figure for the vulnscan docs.

vulnscan has no GUI: its "screens" are terminal sessions. This script **runs the real commands** of
``python -m agentteams.vulnscan`` (and ``agentteams`` for the team) against a throwaway demo home and
renders each session to an SVG terminal frame, so the screenshots in
``docs_src/vulnscan-automation-walkthrough.md`` show what the code actually prints.

What is staged, and disclosed in each caption:

- a demo ``HOME`` in a temporary directory (shown as ``~``), with two tiny git repositories under
  ``~/code`` and one host (``web1``, an ``example.com`` name) in ``hosts.conf``;
- a demo operator Ed25519 key generated for the session (needs the ``[signing]`` extra);
- a demo scan result written through :class:`agentteams.vulnscan.report.Report` (the real ``daily``
  needs network access to OSV.dev);
- a goose team generated with the security-intelligence freshness gate stubbed (offline it refuses,
  correctly), and a grant signed through the Python API when signing cannot complete here (no
  pseudo-terminal, or the install-location check refusing a development checkout). Neither is a
  supported way to run vulnscan;
- the agent's requests sent through the real MCP server class (no container, no model): the
  executor, ledger, escalation and review code that processes them is the real code.

Nothing reaches Docker, GitHub or a real host: the demo repositories push to local bare remotes, and
``server-audit`` on ``web1`` is refused before any ssh would run. The one network use is the demo
``stage-fix``, which reads PyPI release data to pick the fix version (without network it fails, and
the capture shows that). Interactive prompts (signing, resolving) are answered through a real
pseudo-terminal when one exists. Without one, grant signing is confirmed the way its prompt asks
off a terminal (re-run with ``--confirm-review-sha256 <digest>``, shown in the capture); resolving
has no such route, so its capture is skipped.

Output is **not** byte-deterministic: ids, digests and timestamps are generated per run. Long
outputs are cut, and every cut is marked in the frame.

Writes ``docs_src/book/figures/vulnscan/*.svg``.

Run: ``python3 scripts/gen_vulnscan_screenshots.py`` (from the repository root).
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures" / "vulnscan"
MAX_COLS = 100
MAX_ROWS = 34

# ---------------------------------------------------------------- terminal frame


def frame_svg(title: str, lines: list[str]) -> str:
    """Render captured terminal lines as a standalone SVG terminal window.

    Args:
        title: Window title (also the accessible ``<title>``).
        lines: Lines to show (already cut and wrapped).

    Returns:
        SVG document text.
    """
    char_w, line_h, pad = 7.8, 17, 14
    cols = max([len(ln) for ln in lines] + [40])
    width = int(cols * char_w + 2 * pad)
    height = int(len(lines) * line_h + 2 * pad + 28)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img">',
           f"<title>{escape(title)}</title>",
           f'<rect width="{width}" height="{height}" rx="8" fill="#1e1f24"/>',
           f'<rect width="{width}" height="26" rx="8" fill="#2c2e35"/>',
           f'<rect y="18" width="{width}" height="8" fill="#2c2e35"/>',
           '<circle cx="16" cy="13" r="5" fill="#ff5f57"/><circle cx="34" cy="13" r="5" fill="#febc2e"/>'
           '<circle cx="52" cy="13" r="5" fill="#28c840"/>',
           f'<text x="{width // 2}" y="17" fill="#a9adb7" font-family="Menlo, Consolas, monospace" '
           f'font-size="12" text-anchor="middle">{escape(title)}</text>']
    y = 26 + pad + 12
    for ln in lines:
        color = "#e6e6e6"
        if ln.startswith("$ "):
            color = "#8fd18f"
        elif ln.startswith("[…"):
            color = "#7f8594"
        elif ln.lower().startswith(("error", "refused")) or "HALT" in ln[:40]:
            color = "#ff9a8a"
        out.append(f'<text x="{pad}" y="{y}" fill="{color}" font-family="Menlo, Consolas, monospace" '
                   f'font-size="13" xml:space="preserve">{escape(ln)}</text>')
        y += line_h
    out.append("</svg>")
    return "\n".join(out) + "\n"


def shape(text: str, *, head: int = MAX_ROWS, tail: int = 0) -> list[str]:
    """Wrap long lines and cut long outputs, marking every cut.

    Args:
        text: Raw captured text.
        head: Lines kept from the start.
        tail: Lines kept from the end (after a cut marker).

    Returns:
        Display lines.
    """
    lines: list[str] = []
    for raw in text.replace("\t", "    ").splitlines():
        while len(raw) > MAX_COLS:
            lines.append(raw[:MAX_COLS])
            raw = "    " + raw[MAX_COLS:]
        lines.append(raw)
    if len(lines) > head + tail:
        cut = len(lines) - head - tail
        lines = lines[:head] + [f"[… {cut} more lines not shown]"] + (lines[-tail:] if tail else [])
    return lines


# ---------------------------------------------------------------- demo environment


class Demo:
    """A throwaway HOME with config, state, kit, repos, a team and an operator key."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("AGENTTEAMS_", "XDG_", "GH_", "GITHUB_"))}
        self.env.update({"HOME": str(self.home), "PYTHONPATH": str(REPO_ROOT), "COLUMNS": str(MAX_COLS),
                         "NO_COLOR": "1", "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8"})

    def shorten(self, text: str) -> str:
        """Show the demo home as ``~``; replace this machine's interpreter and checkout paths with placeholders."""
        subs = {str(self.home): "~", str(self.home.resolve()): "~", sys.executable: "<python>",
                str(Path(sys.executable).resolve()): "<python>", sys.prefix: "<venv>", str(REPO_ROOT): "<agentteams>"}
        for p in sorted(subs, key=len, reverse=True):
            text = text.replace(p, subs[p])
        # launchd domains carry this account's numeric uid (`gui/NNN`): show a placeholder instead.
        return re.sub(r"\bgui/\d+\b", "gui/<uid>", text)

    def run(self, argv: list[str], *, cwd: Path | None = None, stdin: str = "") -> tuple[int, str]:
        """Run a command non-interactively; return (exit code, stdout+stderr)."""
        proc = subprocess.run(argv, cwd=str(cwd or self.home), env=self.env, input=stdin, text=True,
                              capture_output=True, timeout=600, check=False)
        return proc.returncode, self.shorten(proc.stdout + proc.stderr)

    def run_pty(self, argv: list[str], answers: dict[str, str]) -> tuple[int, str]:
        """Run a command on a pseudo-terminal, answering each prompt substring once."""
        master, slave = pty.openpty()
        proc = subprocess.Popen(argv, cwd=str(self.home), env=self.env, stdin=slave, stdout=slave,
                                stderr=slave, close_fds=True)
        os.close(slave)
        buf, pending = b"", dict(answers)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("utf-8", "replace")
                for prompt in list(pending):
                    if prompt in text:
                        os.write(master, (pending.pop(prompt) + "\n").encode())
            elif proc.poll() is not None:
                break
        proc.wait(timeout=60)
        os.close(master)
        return proc.returncode, self.shorten(buf.decode("utf-8", "replace").replace("\r\n", "\n"))


VS = [sys.executable, "-m", "agentteams.vulnscan"]


def cmd_line(argv: list[str], demo: Demo) -> str:
    """The command as a reader would type it (demo paths shortened)."""
    shown = ["python", "-m", "agentteams.vulnscan"] + argv[len(VS):] if argv[:len(VS)] == VS else argv
    return demo.shorten("$ " + " ".join(a if a and " " not in a else repr(a) for a in shown))


def git(repo: Path, *args: str) -> None:
    """Run git for staging the demo repositories."""
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=demo@example.com", "-c", "user.name=demo", *args],
                   check=True, capture_output=True)


def stage(demo: Demo) -> dict[str, object]:
    """Create the demo repos, team, key, governance log, hosts.conf and a scan result."""
    code = demo.home / "code"
    for name, req in (("shop-api", "PyJWT==2.3.0\nrequests==2.31.0\n"), ("billing", "Django==4.2.1\n")):
        repo = code / name
        repo.mkdir(parents=True)
        (repo / "requirements.txt").write_text(req)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "initial")
        bare = demo.root / "remotes" / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "push", "-q", "origin", "main")
    return {"code": code}


def write_keys(demo: Demo, key_id: str) -> bool:
    """Generate a demo operator key pair (private in keys/, public in verify-keys/)."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        return False
    cfg = demo.home / ".config" / "agentteams"
    keys, verify = cfg / "keys", cfg / "verify-keys"
    for d in (keys, verify):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    priv = Ed25519PrivateKey.generate()
    (keys / f"{key_id}.pem").write_bytes(priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (keys / f"{key_id}.pem").chmod(0o600)
    (verify / f"{key_id}.pub.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    demo.env["AGENTTEAMS_DECISION_ED25519_KEYFILE"] = str(keys / f"{key_id}.pem")
    return True


def _install_demo_grant(demo: Demo, gov: Path) -> None:
    """Sign and install the demo grant through the Python API (used when the real signing is refused)."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "from agentteams.cli.signed_ledger import ed25519_sign\n"
        "from agentteams.vulnscan import grant\n"
        "rec = grant.build(ops=['stage-fix'], repo_roots=[%r], hosts=[], days=1, max_uses=5,\n"
        "                  key_id='op', governance_dir=%r)\n"
        "pem = Path(%r).read_text()\n"
        "signed = {**rec, 'signature': ed25519_sign(pem, grant.signed_values(rec))}\n"
        "grant.verify(signed)\n"
        "print(grant.install(signed))\n"
    ) % (str(REPO_ROOT), str(demo.home / "code" / "shop-api"), str(gov), demo.env["AGENTTEAMS_DECISION_ED25519_KEYFILE"])
    rc, out = demo.run([sys.executable, "-c", code])
    if rc:
        print(f"demo grant install failed:\n{out}", file=sys.stderr)


def write_scan(demo: Demo, run_date: str) -> Path:
    """Write a demo scan result for ``run_date`` through the real Report class."""
    sys.path.insert(0, str(REPO_ROOT))
    from agentteams.vulnscan.report import Finding, Report

    code = demo.home / "code"
    rep = Report(target="daily", checked=3)   # the three pins in the two demo repos
    rep.findings += [
        Finding(kind="package", target=str(code / "shop-api"), name="PyJWT", version="2.3.0", ecosystem="PyPI",
                severity=4, fix="2.4.0", ids=["GHSA-ffqj-6fqr-9h24"], detail="demo finding"),
        Finding(kind="package", target=str(code / "billing"), name="Django", version="4.2.1", ecosystem="PyPI",
                severity=3, fix="4.2.2", ids=["DEMO-0001"], detail="demo finding"),
    ]
    run = demo.home / ".local" / "state" / "agentteams" / "vulnscan" / run_date
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(rep.to_dict(), indent=2))
    (run / "summary.md").write_text(rep.markdown(new_keys=set()))
    hb = demo.home / ".local" / "state" / "agentteams" / "vulnscan" / "heartbeat.json"
    hb.write_text(json.dumps({"last_run": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "exit": 1,
                              "findings": len(rep.findings), "new": 0, "unscanned": 0,
                              "summary": str(run / "summary.md")}, indent=2))
    return run


# ---------------------------------------------------------------- captures


_DIGEST_RE = re.compile(r"--confirm-review-sha256 ([0-9a-f]{64})")


def run_digest_confirmed(demo: Demo, argv: list[str]) -> list[tuple[list[str], int, str]]:
    """Confirm a signing prompt with no terminal, the way the real prompt asks.

    Without a terminal the signer prints the review, refuses, and names the review digest to pass
    back; this runs that, then re-runs with ``--confirm-review-sha256 <digest>``.

    Args:
        demo: The demo environment.
        argv: The signing command.

    Returns:
        One ``(argv, exit code, output)`` per run (one when no digest was offered).
    """
    rc, out = demo.run(argv)
    runs = [(argv, rc, out)]
    match = _DIGEST_RE.search(out)
    if rc and match:
        again = [*argv, "--confirm-review-sha256", match.group(1)]
        rc2, out2 = demo.run(again)
        runs.append((again, rc2, out2))
    return runs


def capture(demo: Demo, name: str, title: str, steps: list[tuple[list[str], dict]], *, head: int = MAX_ROWS,
            tail: int = 0) -> list[int]:
    """Run each (argv, options) step, frame the combined transcript, write ``<name>.svg``.

    A step with ``"pty"`` answers its prompts on a pseudo-terminal. Where none can be allocated, a
    step that also has ``"digest"`` confirms by review digest (:func:`run_digest_confirmed`); any
    other such capture is skipped (the previous SVG is kept) rather than faked.
    """
    text, codes = "", []
    for argv, opts in steps:
        runs: list[tuple[list[str], int, str]]
        if opts.get("pty"):
            try:
                runs = [(argv, *demo.run_pty(argv, opts["pty"]))]
            except OSError as exc:   # no pseudo-terminal here (some sandboxes refuse openpty)
                if not opts.get("digest"):
                    print(f"skipped {name}.svg: no pseudo-terminal ({exc}) and no non-interactive "
                          "confirmation for this prompt; run this script in a real terminal")
                    return []
                print(f"{name}.svg: no pseudo-terminal ({exc}); confirming by review digest instead")
                runs = run_digest_confirmed(demo, argv)
        else:
            runs = [(argv, *demo.run(argv, cwd=opts.get("cwd")))]
        for i, (run_argv, rc, out) in enumerate(runs):
            codes.append(rc)
            shown = opts.get("show", cmd_line(argv, demo))
            if i:   # the digest re-run: the same command plus the confirmation flag
                shown += " \\\n    " + " ".join(run_argv[len(argv):])
            body = "\n".join(shape(out, head=opts.get("head", head), tail=opts.get("tail", tail)))
            text += f"{shown}\n{body}\n" + (f"[exit {rc}]\n" if opts.get("exit") else "")
    lines = text.rstrip("\n").splitlines()
    (OUT_DIR / f"{name}.svg").write_text(frame_svg(title, lines), encoding="utf-8")
    print(f"wrote {name}.svg ({len(lines)} lines; exit codes {codes})")
    return codes


def architecture() -> None:
    """The trust-boundary figure: three zones, laid out to read at page width (960 px)."""
    zones = [  # (x, title, subtitle, fill)
        (12, "Your account", "credentials, grants, ledger key", "#eef6ee"),
        (340, "Agent container", "no credentials; internal network only", "#e8eefb"),
        (668, "Outside", "reached only along the arrows", "#fff8e8"),
    ]
    boxes = [  # (x, y, w, h, lines, stroke)
        (28, 70, 280, 40, ["daily job (launchd / systemd / cron)"], "#3f8f4f"),
        (28, 130, 280, 40, ["deterministic scan: OSV, runtimes, servers"], "#3f8f4f"),
        (28, 190, 280, 40, ["run dir: summary.md / .json"], "#777777"),
        (28, 300, 280, 56, ["executor", "acts only under your signed grant"], "#1b3fa0"),
        (28, 376, 280, 56, ["checked before every action:", "grant, @security HALT, escalations"], "#aa3333"),
        (28, 452, 280, 40, ["MAC-chained ledger"], "#aa3333"),
        (28, 512, 280, 40, ["you: review, halts, resolve, say"], "#3f8f4f"),
        (356, 70, 280, 56, ["goose orchestrator", "+ vuln-remediation agent"], "#1b3fa0"),
        (356, 150, 280, 40, ["vulnscan MCP server (its only tools)"], "#1b3fa0"),
        (356, 220, 280, 56, ["request spool", "(writable; so are the goose sessions)"], "#777777"),
        (356, 470, 280, 56, ["egress gateway sidecar", "one model, spend cap, holds the API key"], "#1b3fa0"),
        (684, 290, 260, 56, ["GitHub: new vulnscan/* branch", "and pull request (never merged)"], "#ccaa44"),
        (684, 360, 260, 40, ["servers: read-only audit, dry run"], "#ccaa44"),
        (684, 415, 260, 50, ["private ops repo: reports", "(only with a publish grant)"], "#ccaa44"),
        (684, 485, 260, 46, ["model API", "(OpenRouter or your route proxy)"], "#ccaa44"),
    ]
    arrows = [  # polylines; the last segment carries the arrowhead
        [(168, 110), (168, 130)], [(168, 170), (168, 190)],          # job -> scan -> run dir
        [(308, 210), (356, 104)],                                    # run dir -> agent (read-only mount)
        [(496, 126), (496, 150)], [(496, 190), (496, 220)],          # agent -> MCP -> spool
        [(356, 262), (308, 316)],                                    # spool -> executor
        [(168, 356), (168, 376)], [(168, 432), (168, 452)], [(168, 492), (168, 512)],
        [(308, 312), (684, 318)], [(308, 332), (684, 380)], [(308, 348), (684, 440)],   # executor -> outside
        [(636, 98), (652, 98), (652, 498), (636, 498)],              # agent -> gateway
        [(636, 508), (684, 508)],                                    # gateway -> model API
    ]
    width, height = 960, 580
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           'role="img" font-family="Helvetica, Arial, sans-serif">',
           "<title>vulnscan agent mode: trust boundaries</title>",
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#444"/></marker></defs>',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for x, title, sub, fill in zones:
        out.append(f'<rect x="{x}" y="8" width="312" height="{height - 16}" rx="10" fill="{fill}" stroke="#bbbbbb"/>')
        out.append(f'<text x="{x + 16}" y="32" font-size="16" font-weight="bold" fill="#222">{escape(title)}</text>')
        out.append(f'<text x="{x + 16}" y="52" font-size="12" fill="#555">{escape(sub)}</text>')
    for x, y, w, h, lines, stroke in boxes:
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#ffffff" stroke="{stroke}" '
                   'stroke-width="1.6"/>')
        top = y + h / 2 - (len(lines) - 1) * 8 + 4
        for i, ln in enumerate(lines):
            out.append(f'<text x="{x + w / 2:g}" y="{top + i * 16:g}" font-size="13" text-anchor="middle" '
                       f'fill="#222">{escape(ln)}</text>')
    for pts in arrows:
        path = " ".join(f"{x},{y}" for x, y in pts)
        out.append(f'<polyline points="{path}" fill="none" stroke="#444" stroke-width="1.4" '
                   'marker-end="url(#arrow)"/>')
    out.append("</svg>")
    (OUT_DIR / "vulnscan-architecture.svg").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote vulnscan-architecture.svg")


def main() -> int:
    """Stage the demo, run every capture, write the figures."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    architecture()
    root = Path(tempfile.mkdtemp(prefix="vulnscan-demo-"))
    try:
        return _captures(Demo(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _captures(demo: Demo) -> int:
    stage(demo)
    have_keys = write_keys(demo, "op")
    run_date = time.strftime("%Y-%m-%d")

    capture(demo, "01-help", "python -m agentteams.vulnscan --help", [(VS + ["--help"], {})], head=40)
    capture(demo, "02-init", "Step 1 — write the config", [
        (VS + ["init", "--repo-root", str(demo.home / "code")], {}),
        (["cat", str(demo.home / ".config" / "agentteams" / "vulnscan.json")],
         {"show": "$ cat ~/.config/agentteams/vulnscan.json"})], head=40)
    capture(demo, "03-server-kit", "Step 2 — export the server kit", [(VS + ["server-kit"], {})])
    kit = demo.home / ".config" / "agentteams" / "server-kit"
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com | demo host\n")
    cfg_file = demo.home / ".config" / "agentteams" / "vulnscan.json"   # Step 2: add the host to "servers"
    cfg = json.loads(cfg_file.read_text())
    cfg["servers"] = ["web1"]
    cfg_file.write_text(json.dumps(cfg, indent=2) + "\n")
    capture(demo, "04-schedule", "Step 3 — install the daily job (dry run)", [
        (VS + ["schedule", "install"], {"head": 18, "tail": 6})])

    # The first daily run (demo findings; the real `daily` needs OSV.dev).
    run = write_scan(demo, run_date)
    capture(demo, "04b-summary", "Step 3 — the first run's summary (demo findings)", [
        (["cat", str(run / "summary.md")], {"show": "$ cat ~/.local/state/agentteams/vulnscan/<date>/summary.md",
                                            "head": 30})])

    # Team: a goose team with the vuln-remediation agent.
    team = demo.home / "ops-team"
    team.mkdir()
    brief = json.loads((REPO_ROOT / "examples" / "software-project" / "brief.json").read_text())
    brief["capabilities"] = sorted(set(brief.get("capabilities", [])) | {"vulnerability_remediation"})
    (team / "brief.json").write_text(json.dumps(brief, indent=2))
    # Offline, the security-intelligence freshness gate (correctly) refuses to render. The demo stubs that
    # one gate, as the test suite does; a real run fetches live intel. Disclosed in the walkthrough caption.
    gen = ("import sys; sys.path.insert(0, %r)\n"
           "from agentteams.cli import security_gate\n"
           "security_gate._assert_security_intelligence_fresh = lambda *a, **k: None\n"
           "import build_team\n"
           "sys.exit(build_team.main(['--description', 'brief.json', '--framework', 'goose', '--project', '.',\n"
           "                          '--yes', '--no-scan', '--security-offline']))\n") % str(REPO_ROOT)
    rc, out = demo.run([sys.executable, "-c", gen], cwd=team)
    if rc:
        print(f"team generation failed (exit {rc}):\n{out[-2000:]}", file=sys.stderr)
    recipes = team / ".goose" / "recipes"
    capture(demo, "05-team", "Step 4 — the generated goose team", [
        (["ls", str(recipes)], {"show": "$ ls ~/ops-team/.goose/recipes", "head": 40}),
        (["grep", "-n", "vuln_remediation", str(recipes / "orchestrator.yaml")],
         {"show": "$ grep -n vuln_remediation ~/ops-team/.goose/recipes/orchestrator.yaml"})])
    gov = next((p.parent.parent for p in sorted(team.rglob("security-decisions.log.csv"))), None)

    if have_keys and gov is not None:
        rel = demo.shorten(str(gov))
        sign = VS + ["grant", "sign", "--key-id", "op", "--days", "1", "--max-uses", "5",
                     "--op", "stage-fix", "--repo-root", str(demo.home / "code" / "shop-api"),
                     "--governance-dir", str(gov)]
        shown = ("$ python -m agentteams.vulnscan grant sign --key-id op --days 1 --max-uses 5 \\\n"
                 "    --op stage-fix --repo-root ~/code/shop-api --governance-dir " + rel)
        signed = capture(demo, "06-grant-sign", "Step 8 — sign a standing grant (operator terminal)", [
            (sign, {"pty": {"[y/N]": "y"}, "digest": True, "show": shown, "exit": True})], head=40)
        if not signed or signed[-1] != 0:   # signing refused (e.g. a dev checkout): install an equivalent demo grant
            _install_demo_grant(demo, gov)
        capture(demo, "06b-grant-list", "The installed grant", [(VS + ["grant", "list"], {})])

    # A run: the agent's requests through the real MCP server, served by the executor.
    spool = demo.home / ".local" / "state" / "agentteams" / "vulnscan" / "spool"
    (spool / "requests").mkdir(parents=True, exist_ok=True)
    (spool / "results").mkdir(parents=True, exist_ok=True)
    agent = (
        "import sys; sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "from agentteams.vulnscan.mcp import Server\n"
        "s = Server(Path(%r), Path(%r), Path(%r))\n"
        "print(s.call('request', {'op': 'stage-fix', 'target': %r, 'rationale': 'GHSA-ffqj-6fqr-9h24 critical'}))\n"
        "print(s.call('request', {'op': 'server-audit', 'target': 'web1', 'rationale': 'stale host audit'}))\n"
        "print(s.call('escalate', {'target': 'web1', 'kind': 'other', 'evidence': 'unexpected cron entry in audit'}))\n"
        "print(s.call('submit_report', {'markdown': '# Daily vulnerability remediation\\n\\nRequested stage-fix for shop-api.'}))\n"
    ) % (str(REPO_ROOT), str(run), str(spool / "requests"), str(spool / "results"), str(demo.home / "code" / "shop-api"))
    demo.run([sys.executable, "-c", agent])
    # What agent-run's request loop does every 15 s (a manual `serve-requests` has no run, so it would
    # refuse the report): the real executor.serve_requests with this run's directory and id.
    loop = (
        "import json, sys; sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "from agentteams.vulnscan import daily, executor\n"
        "run = Path(%r)\n"
        "out = executor.serve_requests(daily.load_config(), run_dir=run, run_id=executor.new_run_id(run))\n"
        "print(json.dumps(out))\n"
    ) % (str(REPO_ROOT), str(run))
    rc, out = demo.run([sys.executable, "-c", loop])
    served = json.JSONDecoder().raw_decode(out[out.index("["):])[0] if "[" in out else []   # stderr may follow
    keep = ("op", "target", "status", "detail", "escalation", "scope", "branch", "stored")
    text = json.dumps([{k: r[k] for k in keep if r.get(k) not in (None, "")} for r in served], indent=1)
    lines = shape("# the run loop: executor.serve_requests(cfg, run_dir=<today's run>, run_id=<run id>)\n"
                  "# (output reduced to the fields below)\n" + demo.shorten(text), head=40)
    (OUT_DIR / "07-serve-requests.svg").write_text(frame_svg("What the executor did with the agent's requests", lines))
    print(f"wrote 07-serve-requests.svg (exit {rc})")

    capture(demo, "08-halts", "Something blocks: vulnscan halts", [(VS + ["halts"], {"exit": True})], head=30)
    esc_id = ""
    for line in demo.run(VS + ["halts"])[1].splitlines():
        if line.startswith("- ve-"):
            esc_id = line[2:21]
            break
    if esc_id:
        capture(demo, "09-resolve", "Resolve an `other` escalation", [
            (VS + ["resolve", esc_id, "--evidence", "operator's own cron job (checked)"],
             {"pty": {"[y/N]": "y"}, "exit": True})])
    capture(demo, "10-say", "Leave a note for the next run", [
        (VS + ["say", "skip billing this week: it is being migrated", "--days", "7"], {}),
        (VS + ["say", "--list"], {})])
    capture(demo, "11-review", "The after-action review", [(VS + ["review"], {"head": 44, "tail": 0})])
    capture(demo, "12-publish-none", "Publishing without a publish grant", [
        (VS + ["publish-grant", "list"], {}), (VS + ["publish"], {"exit": True})])
    return 0


if __name__ == "__main__":
    sys.exit(main())
