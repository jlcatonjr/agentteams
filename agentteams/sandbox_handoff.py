"""sandbox_handoff.py — operator handoff scripts for work a sandboxed agent cannot do itself.

An agent running inside an OS sandbox (Claude Code's Seatbelt/bubblewrap sandbox, Codex's
``workspace-write`` sandbox, a ``confine-run.sh`` launcher) hits operations the sandbox refuses:
network to a host outside the allowlist (``git push``, ``gh pr create``), writes to ``.git`` gitfiles or
outside the granted roots (``git worktree add``, ``.claude/agents``), Keychain/agent sockets, ``open``,
local port binding. The protocol in ``references/sandbox-handoff.reference.md`` says the agent must not
work around the sandbox and must not stop at "I can't": it writes a **handoff script** the operator runs
in a terminal outside the sandbox, and gives the operator the **complete command** to run it.

This module renders and checks those scripts so every team produces the same, reviewable shape:

* ``tmp/by-week/YYYY-Www/<slug>.handoff.sh`` — a ``bash`` script with a header the operator reads first
  (purpose, why the sandbox blocked it, what it touches, whether it is destructive and under which
  ``@security`` clearance), ``set -euo pipefail``, an absolute ``cd``, tool preflight, every command echoed
  before it runs, an optional confirmation prompt, and post-run verify commands.
* ``tmp/by-week/YYYY-Www/<slug>.handoff.log`` — written by the script as it runs. It lives inside the
  project, so the sandboxed agent can read it back (:func:`handoff_status`) and continue: the result line,
  the failing step, and the script's own SHA-256 (so an edited or regenerated script is reported, not
  trusted).

The script refuses to run when it sees a sandbox marker in its environment, so an agent cannot run its
own handoff (and fail half-way inside the sandbox); ``AGENTTEAMS_HANDOFF_ALLOW_SANDBOX=1`` overrides.

Spec content is data (C-4): commands are written verbatim for the operator to review, never executed
here. A spec that looks like it carries a secret is refused: scripts reference credentials through
environment variables the operator already has (``"$GITHUB_TOKEN"``), never literal values. A
destructive spec must cite its recorded ``@security`` clearance (C-5) and always prompts before running.

CLI
---
    python -m agentteams.sandbox_handoff detect [--json]
    python -m agentteams.sandbox_handoff write SPEC.json [--root DIR] [--overwrite] [--json]
    python -m agentteams.sandbox_handoff status SCRIPT_OR_LOG [--json]

``SPEC.json`` (``-`` reads stdin)::

    {"slug": "push-feature-x",
     "purpose": "Push feat/x and open its PR",
     "blocked_by": "sandbox network allowlist excludes github.com",
     "steps": [{"title": "Push", "run": "git push -u origin feat/x"},
               {"title": "Open PR", "run": "gh pr create --fill --base main"}],
     "verify": [{"title": "PR exists", "run": "gh pr view --json url -q .url"}],
     "requires": ["git", "gh"],
     "touches": ["origin/feat/x (remote branch)", "GitHub PR"],
     "destructive": false, "clearance": "", "confirm": false, "cwd": ""}

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from agentteams.atomicio import write_new_atomic
from agentteams.cli.governance_targets import target_is_governance_root
# scan.py is integrity-pinned, so its pattern list is reused rather than edited or copied.
from agentteams.scan import _CREDENTIAL_PATTERNS

#: First line after the shebang; :func:`handoff_status` keys on it.
MARKER = "# AGENTTEAMS-SANDBOX-HANDOFF v1"

#: Environment variables that identify a sandboxed process, with the sandbox each names.
#: Absence proves nothing: a refused operation (EPERM, a denied host) is the authoritative signal.
SANDBOX_ENV_MARKERS: dict[str, str] = {
    "SANDBOX_RUNTIME": "claude-code",
    "CODEX_SANDBOX": "codex",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_RESULT_RE = re.compile(r"^HANDOFF-RESULT: (ok|failed)(?: rc=(\d+))?(?: step=(\d+))?", re.MULTILINE)
_SHA_RE = re.compile(r"^HANDOFF-SCRIPT-SHA256: ([0-9a-f]{64})", re.MULTILINE)

#: Credential shapes a handoff spec needs on top of ``scan._CREDENTIAL_PATTERNS``: shell assignments and
#: flags carrying a literal (``TOKEN=abc…``, ``--token abc…``), but never a ``$VAR`` reference.
_EXTRA_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*['\"]?(?![$'\"])[^\s'\"]{8,}"),
    re.compile(r"(?i)--(?:password|token|secret|api-key)[= ]+['\"]?(?![$'\"])[^\s'\"]{8,}"),
)

#: Capability grants and signing material outside ``governance_targets.GOVERNANCE_TARGET_STEMS`` (which covers
#: the constitution, rosters, integrity manifest, ledgers and verify keys). With those stems, a handoff naming
#: any of them is refused even though the operator reviews it (C-3, Rule 15(c)): a reviewed-looking script is
#: exactly how a forged grant would arrive. The operator changes these by hand. Case-insensitive substrings.
EXTRA_TRUST_ROOT_TOKENS: tuple[str, ...] = (
    ".claude/settings.json", ".claude/settings.local.json", ".claude/hooks", "settings.hooks.example.json",
    "confined-run.example.sh", "management-authority.json", "agents.md", ".config/agentteams/keys",
    "agentteams_management_signing_key", "agentteams_decision_signing_key", "agentteams_proposal_ledger_key",
)

#: Spec keys :func:`spec_from_dict` accepts; anything else is refused rather than silently ignored.
SPEC_KEYS = frozenset({"slug", "purpose", "blocked_by", "steps", "verify", "requires", "touches", "destructive",
                       "clearance", "confirm", "cwd", "plan"})


class HandoffError(ValueError):
    """A handoff spec that cannot be rendered safely (bad slug, secret, missing clearance, bad syntax)."""


@dataclass(frozen=True)
class SandboxInfo:
    """What :func:`detect_sandbox` could tell from the environment.

    Attributes:
        sandboxed: True when at least one sandbox marker is present.
        kinds: The sandboxes named by the markers found (``claude-code``, ``codex``). ``confine-run.sh``
            scrubs the environment and sets no marker, so a confined process reports none.
        network_disabled: True when the sandbox says outbound network is off entirely.
        markers: The marker variable names found (values are not reported).
    """

    sandboxed: bool
    kinds: tuple[str, ...] = ()
    network_disabled: bool = False
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class HandoffStep:
    """One command block in a handoff script.

    Attributes:
        title: Short human label printed before the command.
        run: The shell command line, written verbatim (the operator reviews it).
    """

    title: str
    run: str


@dataclass(frozen=True)
class HandoffSpec:
    """Everything a handoff script says and does. See the module docstring for the JSON form.

    Attributes:
        slug: Lowercase-hyphenated name; the script is ``<slug>.handoff.sh``.
        purpose: One line: what running the script accomplishes.
        blocked_by: Why the agent could not do it inside the sandbox (the refused operation).
        steps: Commands that do the work, in order.
        verify: Commands run after ``steps`` that show the work took effect.
        requires: Executables that must be on the operator's PATH.
        touches: Everything the script writes or changes outside the sandbox's grant.
        destructive: True when any step deletes, force-pushes, resets or rewrites (C-5).
        clearance: The recorded ``@security`` clearance a destructive script runs under.
        confirm: Prompt before running (default on; always on when ``destructive``).
        cwd: Directory to run in, relative to the project root (default: the root).
        plan: Optional plan slug this handoff belongs to (recorded in the header).
    """

    slug: str
    purpose: str
    blocked_by: str
    steps: tuple[HandoffStep, ...]
    verify: tuple[HandoffStep, ...] = ()
    requires: tuple[str, ...] = ()
    touches: tuple[str, ...] = ()
    destructive: bool = False
    clearance: str = ""
    confirm: bool = True
    cwd: str = ""
    plan: str = ""


@dataclass(frozen=True)
class HandoffResult:
    """What :func:`write_handoff` produced.

    Attributes:
        script: Absolute path of the written script.
        log: Absolute path the script will write its log to.
        command: The complete command the operator runs in an external terminal. It checks the script's
            SHA-256 first, so a script edited after the operator reviewed it refuses to run.
        sha256: The script's SHA-256 (show it to the operator with the command).
        syntax_checked: True when ``bash -n`` validated the script.
    """

    script: Path
    log: Path
    command: str
    sha256: str
    syntax_checked: bool


@dataclass
class HandoffStatus:
    """The outcome of a handoff, read back from its log.

    Attributes:
        state: ``not-run`` (no log), ``incomplete`` (log without a result line: still running or killed),
            ``ok``, ``failed``, or ``stale`` (the log was written by a different version of the script).
        rc: The failing exit status, when ``failed``.
        step: The step number that failed, when ``failed`` (0 = preflight).
        log: The log path consulted.
        notes: Human-readable detail.
    """

    state: str
    rc: int | None = None
    step: int | None = None
    log: str = ""
    notes: list[str] = field(default_factory=list)


def detect_sandbox(env: Mapping[str, str] | None = None) -> SandboxInfo:
    """Report the sandbox markers present in ``env``.

    Args:
        env: Environment to inspect; defaults to ``os.environ``.

    Returns:
        A :class:`SandboxInfo`. ``sandboxed=False`` means no marker was found, not that the process is
        unconfined.
    """
    env = os.environ if env is None else env
    found = tuple(name for name in SANDBOX_ENV_MARKERS if env.get(name))
    kinds = tuple(dict.fromkeys(SANDBOX_ENV_MARKERS[name] for name in found))
    network_off = env.get("CODEX_SANDBOX_NETWORK_DISABLED", "") == "1"
    return SandboxInfo(sandboxed=bool(found), kinds=kinds, network_disabled=network_off, markers=found)


def _steps(raw: Any, key: str) -> tuple[HandoffStep, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise HandoffError(f"'{key}' must be a list of {{title, run}} objects")
    out: list[HandoffStep] = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not str(item.get("run", "")).strip():
            raise HandoffError(f"'{key}' item {i} needs a non-empty 'run'")
        out.append(HandoffStep(title=str(item.get("title") or f"{key} {i}").strip(), run=str(item["run"]).strip()))
    return tuple(out)


def _strings(raw: Any, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise HandoffError(f"'{key}' must be a list of strings")
    return tuple(x.strip() for x in raw if x.strip())


def spec_from_dict(data: Mapping[str, Any]) -> HandoffSpec:
    """Build a validated :class:`HandoffSpec` from its JSON form.

    Args:
        data: The decoded spec object.

    Returns:
        The spec.

    Raises:
        HandoffError: When a field is missing, mistyped, or fails validation (see :func:`validate_spec`).
    """
    if not isinstance(data, Mapping):
        raise HandoffError("spec must be a JSON object")
    unknown = sorted(set(data) - SPEC_KEYS)
    if unknown:
        raise HandoffError(f"unknown spec key(s): {', '.join(unknown)}")
    for key in ("destructive", "confirm"):
        if key in data and not isinstance(data[key], bool):
            raise HandoffError(f"'{key}' must be a JSON boolean (true/false), not {data[key]!r}")
    spec = HandoffSpec(
        slug=str(data.get("slug", "")).strip(),
        purpose=str(data.get("purpose", "")).strip(),
        blocked_by=str(data.get("blocked_by", "")).strip(),
        steps=_steps(data.get("steps"), "steps"),
        verify=_steps(data.get("verify"), "verify"),
        requires=_strings(data.get("requires"), "requires"),
        touches=_strings(data.get("touches"), "touches"),
        destructive=bool(data.get("destructive", False)),
        clearance=str(data.get("clearance", "") or "").strip(),
        confirm=bool(data.get("confirm", True)),
        cwd=str(data.get("cwd", "") or "").strip(),
        plan=str(data.get("plan", "") or "").strip(),
    )
    validate_spec(spec)
    return spec


def find_secrets(text: str) -> list[str]:
    """Return a redacted description of each credential-shaped string in ``text``.

    Args:
        text: Content to scan.

    Returns:
        One entry per match, showing only the first four characters.
    """
    hits: list[str] = []
    named = [*_CREDENTIAL_PATTERNS, *(("credential assignment", p) for p in _EXTRA_SECRET_PATTERNS)]
    for name, pattern in named:
        for m in pattern.finditer(text):
            hits.append(f"{name}: {m.group(0)[:4]}…")
    return hits


def trust_roots_named(spec: HandoffSpec) -> list[str]:
    """Return the trust roots / capability grants a spec's ``touches`` and commands name.

    Args:
        spec: The spec to check.

    Returns:
        The offending ``touches`` entries and command tokens, sorted and de-duplicated.
    """
    candidates = list(spec.touches)
    for step in (*spec.steps, *spec.verify):
        try:
            candidates += shlex.split(step.run, comments=True)
        except ValueError:
            candidates += step.run.split()
    hits = {c for c in candidates
            if target_is_governance_root(c) or any(t in c.lower() for t in EXTRA_TRUST_ROOT_TOKENS)}
    return sorted(hits)


def validate_spec(spec: HandoffSpec) -> None:
    """Check a spec against the handoff contract.

    Args:
        spec: The spec to check.

    Raises:
        HandoffError: On a bad slug, an empty purpose/blocked_by/steps list, a destructive spec without a
            clearance, a ``cwd`` that escapes the root, a malformed ``requires`` name, a single-line field
            containing a newline, a trust-root path or signing-key name, or credential-shaped content.
    """
    if not _SLUG_RE.match(spec.slug):
        raise HandoffError(f"slug {spec.slug!r} must be lowercase letters, digits and hyphens (max 80)")
    if not spec.purpose:
        raise HandoffError("'purpose' is required: say what running the script accomplishes")
    if not spec.blocked_by:
        raise HandoffError("'blocked_by' is required: name the operation the sandbox refused")
    if not spec.steps:
        raise HandoffError("'steps' must contain at least one command")
    if spec.destructive and not spec.clearance:
        raise HandoffError("a destructive handoff must cite its recorded @security clearance in 'clearance' (C-5)")
    if spec.cwd and (spec.cwd.startswith("/") or ".." in Path(spec.cwd).parts):
        raise HandoffError("'cwd' must be a path inside the project root")
    for name in spec.requires:
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", name):
            raise HandoffError(f"'requires' entry {name!r} is not a plain executable name")
    one_line = [spec.purpose, spec.blocked_by, spec.clearance, spec.plan, *spec.touches,
                *(s.title for s in (*spec.steps, *spec.verify))]
    if any("\n" in v or "\r" in v for v in one_line):
        raise HandoffError("header fields and step titles must be single lines")
    blob = "\n".join([*one_line, *(s.run for s in (*spec.steps, *spec.verify))])
    roots = trust_roots_named(spec)
    if roots:
        raise HandoffError("a handoff may not write a trust root or capability grant (C-3, Rule 15(c)); the "
                           "operator changes these by hand: " + ", ".join(roots))
    secrets = find_secrets(blob)
    if secrets:
        raise HandoffError("spec contains credential-shaped content; reference an environment variable the "
                           "operator already has instead: " + "; ".join(secrets))


def iso_week(now: datetime | None = None) -> str:
    """Return the ``YYYY-Www`` ISO week used for ``tmp/by-week/`` folders.

    Args:
        now: Moment to use; defaults to the current local time, matching the operator's ``date +%G-W%V``
            (the header's ``generated`` stamp is UTC because it is a timestamp, not a folder name).

    Returns:
        The ISO week label.
    """
    year, week, _ = (now or datetime.now()).isocalendar()
    return f"{year}-W{week:02d}"


def handoff_paths(root: Path, slug: str, week: str | None = None) -> tuple[Path, Path]:
    """Return ``(script, log)`` paths for ``slug`` under ``root/tmp/by-week/<week>/``.

    Args:
        root: Project root.
        slug: Handoff slug.
        week: ISO week label; defaults to :func:`iso_week`.

    Returns:
        Absolute script and log paths.
    """
    folder = Path(root).resolve() / "tmp" / "by-week" / (week or iso_week())
    return folder / f"{slug}.handoff.sh", folder / f"{slug}.handoff.log"


def _comment(text: str) -> str:
    return text.replace("\n", " ").replace("\r", " ")


def _block(steps: tuple[HandoffStep, ...], start: int, total: int) -> list[str]:
    lines: list[str] = []
    for i, s in enumerate(steps, start):
        lines.append(f"step {i} {total} {shlex.quote(s.title)}")
        lines.append(f"show {shlex.quote(s.run)}")
        lines.append(s.run)
        lines.append("")
    return lines


def render_script(spec: HandoffSpec, *, root: Path, log: Path, script: Path,
                  generated: datetime | None = None) -> str:
    """Render the handoff script text for ``spec``.

    Args:
        spec: A validated spec.
        root: Absolute project root the script ``cd``s into.
        log: Absolute log path the script writes.
        script: Absolute path the script will live at (printed in its own run instructions).
        generated: Timestamp for the header; defaults to now (UTC).

    Returns:
        The script text.
    """
    validate_spec(spec)
    stamp = (generated or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    workdir = (Path(root) / spec.cwd) if spec.cwd else Path(root)
    total = len(spec.steps) + len(spec.verify)
    confirm = spec.confirm or spec.destructive
    destructive = f"YES — clearance: {_comment(spec.clearance)}" if spec.destructive else "no"
    touches = [f"#   - {_comment(t)}" for t in spec.touches] or ["#   - (nothing outside the project declared)"]
    lines = [
        "#!/usr/bin/env bash",
        MARKER,
        "#",
        f"# slug:        {spec.slug}",
        *( [f"# plan:        {_comment(spec.plan)}"] if spec.plan else [] ),
        f"# purpose:     {_comment(spec.purpose)}",
        f"# blocked-by:  {_comment(spec.blocked_by)}",
        f"# destructive: {destructive}",
        "# touches:",
        *touches,
        f"# generated:   {stamp} by a sandboxed agent. READ EVERY COMMAND BELOW BEFORE RUNNING IT.",
        "#",
        "# Run it from a terminal OUTSIDE the agent sandbox:",
        f"#   bash {shlex.quote(str(script))}",
        "# Output is also written to:",
        f"#   {log}",
        "# so the agent can read the result back and continue.",
        "",
        "set -euo pipefail",
        "",
        f"ROOT={shlex.quote(str(root))}",
        f"WORKDIR={shlex.quote(str(workdir))}",
        f"LOG={shlex.quote(str(log))}",
        'STEP=0',
        "",
        "for marker in " + " ".join(SANDBOX_ENV_MARKERS) + "; do",
        '  if [ -n "${!marker:-}" ] && [ "${AGENTTEAMS_HANDOFF_ALLOW_SANDBOX:-}" != 1 ]; then',
        '    echo "This handoff must run in a terminal outside the agent sandbox ($marker is set)." >&2',
        '    echo "Set AGENTTEAMS_HANDOFF_ALLOW_SANDBOX=1 to override." >&2',
        "    exit 64",
        "  fi",
        "done",
        "",
        'mkdir -p "$(dirname "$LOG")"',
        ': > "$LOG"',
        'if command -v shasum >/dev/null 2>&1; then SUM=$(shasum -a 256 "$0" | cut -d" " -f1)',
        'elif command -v sha256sum >/dev/null 2>&1; then SUM=$(sha256sum "$0" | cut -d" " -f1)',
        'else SUM=unknown; fi',
        'printf "HANDOFF-SCRIPT-SHA256: %s\\nHANDOFF-START: %s\\n" "$SUM" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"',
        "",
        "# Everything below runs in one subshell whose output is also appended to $LOG.",
        "(",
        "trap 'rc=$?; if [ $rc -eq 0 ]; then printf \"\\nHANDOFF-RESULT: ok\\n\"; "
        "else printf \"\\nHANDOFF-RESULT: failed rc=%s step=%s\\n\" \"$rc\" \"$STEP\"; fi' EXIT",
        'step() { STEP=$1; printf "\\n==> [%s/%s] %s\\n" "$1" "$2" "$3"; }',
        "show() { printf '$ %s\\n' \"$1\"; }",
        "",
    ]
    if spec.requires:
        lines += [
            "for tool in " + " ".join(shlex.quote(r) for r in spec.requires) + "; do",
            '  command -v "$tool" >/dev/null 2>&1 || { echo "missing required tool: $tool" >&2; exit 127; }',
            "done",
            "",
        ]
    lines += ['cd "$WORKDIR"', ""]
    if confirm:
        lines += [
            "printf 'About to run: %s\\n' " + shlex.quote(_comment(spec.purpose)),
            'if [ "${AGENTTEAMS_HANDOFF_YES:-}" != 1 ]; then',
            '  read -r -p "Proceed? [y/N] " answer < /dev/tty',
            '  case "$answer" in y|Y|yes|YES) ;; *) echo "Aborted by operator."; exit 65 ;; esac',
            "fi",
            "",
        ]
    lines += _block(spec.steps, 1, total)
    if spec.verify:
        lines += ['echo', 'echo "--- verify ---"', ""]
        lines += _block(spec.verify, len(spec.steps) + 1, total)
    lines += ['echo', 'echo "Handoff complete. Tell the agent: done (log: $LOG)."',
              ') 2>&1 | tee -a "$LOG"',
              "# The subshell's status (not tee's) is the script's status.",
              'exit "${PIPESTATUS[0]}"', ""]
    return "\n".join(lines)


def _bash_syntax_ok(path: Path) -> bool | None:
    bash = shutil.which("bash")
    if not bash:
        return None
    proc = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise HandoffError(f"generated script fails `bash -n`: {proc.stderr.strip()}")
    return True


def write_handoff(spec: HandoffSpec, root: Path | str, *, week: str | None = None,
                  overwrite: bool = False) -> HandoffResult:
    """Write ``spec``'s handoff script and return the complete operator command.

    Args:
        spec: The handoff to write.
        root: Project root (resolved to an absolute path).
        week: ISO week folder; defaults to the current week.
        overwrite: Replace an existing script with the same slug.

    Returns:
        A :class:`HandoffResult` with the script path, its log path and the command to run.

    Raises:
        HandoffError: On an invalid spec, an existing script without ``overwrite``, or a script that fails
            ``bash -n`` (the file is removed in that case).
    """
    root_path = Path(root).resolve()
    script, log = handoff_paths(root_path, spec.slug, week)
    if script.exists() and not overwrite:
        raise HandoffError(f"{script} already exists; pass overwrite=True (the operator may have reviewed it)")
    text = render_script(spec, root=root_path, log=log, script=script)
    script.parent.mkdir(parents=True, exist_ok=True)
    write_new_atomic(script.parent, script.name, text.encode("utf-8"), mode=0o755)
    try:
        checked = _bash_syntax_ok(script)
    except HandoffError:
        script.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    return HandoffResult(script=script, log=log, command=run_command(script, digest), sha256=digest,
                         syntax_checked=bool(checked))


def run_command(script: Path, sha256: str) -> str:
    """Return the complete operator command: verify the script's hash, then run it.

    Args:
        script: Absolute script path.
        sha256: The hash the agent computed when it wrote the script.

    Returns:
        A single shell command line, usable from any directory. ``shasum`` ships with macOS and most
        Linux distributions; the command falls back to ``sha256sum``.
    """
    q = shlex.quote(str(script))
    return (f"( s=$( (shasum -a 256 {q} 2>/dev/null || sha256sum {q}) | cut -d' ' -f1 ); "
            f"[ \"$s\" = {sha256} ] || {{ echo 'handoff script changed since it was written; re-read it' >&2; exit 1; }} ) "
            f"&& bash {q}")


def handoff_status(path: Path | str) -> HandoffStatus:
    """Read a handoff's outcome from its log.

    Args:
        path: The ``.handoff.sh`` script or its ``.handoff.log``.

    Returns:
        A :class:`HandoffStatus`. When the script is present, a log written by a different version of it
        is reported as ``stale``.

    Raises:
        HandoffError: When ``path`` is neither a ``.handoff.sh`` nor a ``.handoff.log``.
    """
    p = Path(path)
    if p.name.endswith(".handoff.sh"):
        script, log = p, p.with_name(p.name[: -len(".sh")] + ".log")
    elif p.name.endswith(".handoff.log"):
        log, script = p, p.with_name(p.name[: -len(".log")] + ".sh")
    else:
        raise HandoffError(f"{p.name} is not a .handoff.sh script or .handoff.log")
    if not log.exists():
        return HandoffStatus(state="not-run", log=str(log), notes=["no log yet: the operator has not run it"])
    text = log.read_text(encoding="utf-8", errors="replace")
    status = HandoffStatus(state="incomplete", log=str(log))
    sha = _SHA_RE.search(text)
    if script.exists() and sha:
        current = hashlib.sha256(script.read_bytes()).hexdigest()
        if current != sha.group(1):
            status.state = "stale"
            status.notes.append("the log was written by a different version of the script; re-run it")
            return status
    results = _RESULT_RE.findall(text)
    if not results:
        status.notes.append("no result line: the script is still running or was interrupted")
        return status
    outcome, rc, step = results[-1]
    status.state = outcome
    if outcome == "failed":
        status.rc = int(rc) if rc else None
        status.step = int(step) if step else None
        status.notes.append("read the log tail for the failing command's output")
    return status


def _load_spec(arg: str) -> HandoffSpec:
    raw = sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HandoffError(f"spec is not valid JSON: {exc}") from exc
    return spec_from_dict(data)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. See module docstring for usage.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit status: 0 on success, 1 on a refused spec or a handoff that is not ``ok``, 2 on a
        path that is not a handoff script or log.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agentteams.sandbox_handoff",
                                     description="Write and check operator handoff scripts for sandboxed agents.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_detect = sub.add_parser("detect", help="Report sandbox markers in this environment.")
    p_detect.add_argument("--json", action="store_true")
    p_write = sub.add_parser("write", help="Write <slug>.handoff.sh from a JSON spec ('-' = stdin).")
    p_write.add_argument("spec")
    p_write.add_argument("--root", default=".")
    p_write.add_argument("--week", default=None)
    p_write.add_argument("--overwrite", action="store_true")
    p_write.add_argument("--json", action="store_true")
    p_status = sub.add_parser("status", help="Read a handoff's outcome from its log.")
    p_status.add_argument("path")
    p_status.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "detect":
        info = detect_sandbox()
        if args.json:
            print(json.dumps(info.__dict__, default=list))
        elif info.sandboxed:
            print(f"sandboxed: {', '.join(info.kinds)} (markers: {', '.join(info.markers)})"
                  + ("; network disabled" if info.network_disabled else ""))
        else:
            print("no sandbox marker found (a refused operation is still the authoritative signal)")
        return 0

    if args.cmd == "write":
        try:
            result = write_handoff(_load_spec(args.spec), args.root, week=args.week, overwrite=args.overwrite)
        except (HandoffError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"script": str(result.script), "log": str(result.log), "command": result.command,
                              "sha256": result.sha256, "syntax_checked": result.syntax_checked}))
        else:
            print(f"script: {result.script}")
            print(f"log:    {result.log}")
            print(f"sha256: {result.sha256}")
            print("Run this in a terminal outside the sandbox:")
            print(f"  {result.command}")
        return 0

    try:
        status = handoff_status(args.path)
    except HandoffError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(status.__dict__))
    else:
        detail = f" rc={status.rc} step={status.step}" if status.state == "failed" else ""
        print(f"{status.state}{detail}  ({status.log})")
        for note in status.notes:
            print(f"  - {note}")
    return 0 if status.state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
