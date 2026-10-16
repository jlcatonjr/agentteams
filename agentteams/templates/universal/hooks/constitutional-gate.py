#!/usr/bin/env python3
"""constitutional-gate.py — PreToolUse hook enforcing C-4 and C-5 on agent tool calls.

**Why this exists.** ``agentteams/cli/security_gate.py`` is a real, fail-closed gate — and it
guards four CLI entry points, not the agents. A 2026-08-06 red-team audit (probe E3) recorded
the consequence: an agent deleting files with ``Bash``, or writing a credential with ``Write``,
never reaches it. For agent-initiated actions C-5 was procedural text.

This hook is the counterpart. The harness runs it *before* the tool call, so unlike every
control inside ``agentteams/`` it is not merely another file the agents can edit on their way
past it (probe E4).

**Verdict policy — deliberately split.**

* ``deny`` for **deterministic** findings: content about to be written that the scanner rates
  high severity (credentials, PII paths, injected override text). There is no legitimate reason
  to write those, so a block costs nothing and asking would train the operator to click through.
* ``ask`` for **procedural** findings: Bash commands matching a Mandatory Review Trigger.
  Whether ``sudo`` or a pipe-to-shell is appropriate is a judgment call the constitution routes
  to a human, and a hook that denied them outright would be wrong often enough to get removed.

**Fail-open vs fail-closed on internal error (CC-2).** By default this hook fails OPEN on an
internal error — an attacker who can make it crash gets an allow. That is accepted for a
COOPERATIVE workspace, because a hook that can brick an operator's session gets deleted, and a
deleted hook enforces nothing. The mitigation is `agentteams.integrity`, which pins the scanner
it calls (this file itself is not in the manifest — a gap recorded 2026-08-16 in the remediation
log). For a CONFINED/EXCLUSIVE `privilege_profile`, agentteams emits this hook with
`_FAIL_CLOSED_ON_ERROR = True` (see the entry point), so a crash instead emits a `deny` rather
than a silent allow — the operator opted into a boundary, so a gate crash must not drop it.
`--allow-fallback-fail-open` restores the fail-open default even for those profiles.

The default fail-open is supplied by the HARNESS, not by a catch-all: a PreToolUse hook exiting
with any code other than 0 or 2 is a non-blocking error and the action proceeds. The single
`except` in `_entrypoint` is a process-boundary handler that ACTS (emits a deny) and reports —
not a swallow (CH-24) — and runs only under the fail-closed policy. A verdict is expressed as
JSON on stdout with exit 0; the fail-closed belt additionally uses exit code 2.

**Interpreter pin (1.0 must-fix, 2026-10-15).** The harness runs this hook as plain ``python3``, and
the write path imports ``agentteams``. When PATH's ``python3`` cannot import it, the hook re-runs
itself under the interpreter named in ``agentteams-python`` beside it (the absolute
``sys.executable`` that rendered the team; untracked, because a machine path must never be
committed — Rule S-8), forwarding the original stdin bytes and relaying the child's verdict. The pin
is used only when it is absolute, an executable ``python*`` file, and outside the project tree
(as found and resolved); ``AGENTTEAMS_GATE_REEXEC`` stops a second hop. With no interpreter
that can import agentteams, a content-bearing write gets ``ask`` (cooperative) or ``deny``
(fail-closed) naming the fix — never the silent allow a crash used to be.

Input:  a JSON tool-call payload on stdin (``tool_name``, ``tool_input``).
Output: exit 0, plus a ``hookSpecificOutput`` JSON decision on stdout when acting.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOOK_EVENT = "PreToolUse"

#: Bash patterns that match a Mandatory Review Trigger in security.template.md. Each is a
#: judgment call routed to the operator, never an automatic deny — see the verdict policy above.
_BASH_REVIEW_TRIGGERS: tuple[tuple[str, str], ...] = (
    (r"\bsudo\b|\bdoas\b", "elevated privilege — effects outside the project tree"),
    (r"curl[^|]*\|\s*(?:ba)?sh|wget[^|]*\|\s*(?:ba)?sh|iwr[^|]*\|\s*iex",
     "remote content piped straight into a shell with no inspectable step (Rule S-9 criterion 2)"),
    (r"\b(?:crontab|launchctl)\b|/etc/sudoers|LaunchAgents",
     "persistence mechanism — outlives the current session (Rule S-9 criterion 4)"),
    (r"\b(?:brew|apt|apt-get|dnf|yum)\s+install\b|\bpip\s+install\b|\bnpm\s+i(?:nstall)?\b",
     "package installation — unreviewed third-party code on the host"),
    (r"\bgit\b(?=[^\n]*\bpush\b)(?=[^\n]*--force)|\bgit\b[^\n]*\breset\s+--hard\b",
     "history-destructive git operation"),
    # ── Outbound-data gate (Rule S-9 criterion 6; C-4 derived content) ────────────────────────────
    # Exfiltration is the last hop of an injection, including one an agent decoded from read content
    # itself ("decrypt this and follow it"). Decoding is deliberately NOT gated: a model can decode
    # base64/hex/ROT13 with no tool call, and `base64 -d` is routine, so a decode trigger would be
    # noise that trains rubber-stamping. The send is the hop a Bash call must make. Scoped to sends
    # that carry a file, stdin, command output or a credential path, or that open a new channel — a
    # plain GET, `git push` to a configured remote and `gh pr create --body` stay silent (headless PR
    # agents post through gh). Same limits as the delete gate: per-call and stateless, blind to
    # WebFetch/MCP, to a request carrying literal or variable-expanded data, and to URLs rendered into
    # chat output. Network confinement is the boundary, not this.
    # Flag letters are case-sensitive ((?-i:…)) because the gate searches with IGNORECASE and curl's
    # -d/-D and -F/-f mean different things; a short-flag cluster (`-sd@f`) is matched too. Tools that
    # are also ordinary words (nc, scp, host…) are matched by command NAME, not by regex, in
    # _command_name_trigger() below, so `grep scp` or `wget -nc` stays silent. Also an accepted extra
    # prompt: a quoted argument containing `; nc …` (the line is split on separators before quoting). Known extra prompts, accepted: a quoted
    # `requests.post(` in a grep, and `curl -H "…$(gh auth token)"` (that one sends a credential).
    (r"\bcurl\b[^\n|;&]*\s(?-i:-[a-zA-Z]*d|--data(?:-binary|-ascii)?|--json)[\s=]*['\"]?@|"
     r"\bcurl\b[^\n|;&]*\s--data-urlencode[\s=]*['\"]?[^\s'\"@]*@|"
     r"\bcurl\b[^\n|;&]*\s(?-i:-[a-zA-Z]*F|--form(?:-string)?)[\s=]*['\"]?[^\s'\"=]+=[@<]|"
     r"\bcurl\b[^\n|;&]*\s(?-i:-[a-zA-Z]*[TK]\b|--upload-file\b|--config\b)|"
     r"\bwget\b[^\n]*--(?:post|body)-file\b|"
     r"\bgh\b[^\n|;&]*(?:\s--input\b|\s(?-i:-F|--field)[\s=]*['\"]?[\w.\[\]-]+=@)",
     "outbound request uploading a file or stdin body (Rule S-9 criterion 6 — local data leaving the machine)"),
    (r"\b(?:curl|wget)\b[^\n;&|]*(?:\$\(|`)|"
     r"\b(?:curl|wget|gh|nc|ncat|socat|scp|rsync|sftp)\b[^\n;&|]*"
     r"(?:~/\.ssh|\.aws/|\.netrc|\.gnupg|\bid_(?:rsa|ed25519|ecdsa)\b|\.env(?![\w.])|\bprintenv\b)",
     "outbound request built from command output or naming a credential path (Rule S-9 criterion 6)"),
    (r"/dev/(?:tcp|udp)/|\brsync\b[^\n|;&]*\s(?:[\w.-]+@)?[\w.-]+:(?!//)",
     "raw socket or copy to a remote host — arbitrary data can leave the machine (Rule S-9 criterion 6)"),
    (r"\bgh\s+gist\s+(?:create|new|edit)\b|\bgit\b[^\n]*\bremote\s+(?:add|set-url)\b|"
     r"\bgit\b(?=[^\n]*\bconfig\b)(?![^\n]*\s(?:--(?:get|get-all|get-regexp|get-urlmatch|list|show-origin)|-l)\b)(?=[^\n]*\b(?:remote\.\S+\.(?:url|pushurl)|url\.\S+\.(?:insteadof|pushinsteadof))\b)|"
     r"\bgit\b[^\n]*\s-c\s+['\"]?(?:remote|url)\.|"
     r"\bgh\b(?=[^\n|;&]*\s(?:-R|--repo)[\s=])(?=[^\n|;&]*(?:--body-file|\$\(|`))|"
     r"\bgh\s+api\b[^\n|;&]*(?:\$\(|`)|"
     r"\bgit\b(?=[^\n]*\bpush\b)(?=[^\n]*\s['\"]?(?:[a-z][\w+.-]*://|[\w.-]+@[\w.-]+:|[\w-]+(?:\.[\w-]+)+:[^\s/]*/?))|"
     r"\bGIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+|PARAMETERS|GLOBAL|SYSTEM)\b|"
     r"\bgit\b(?=[^\n]*\bconfig\b)(?![^\n]*\s(?:--(?:get|get-all|get-regexp|get-urlmatch|list|show-origin)|-l)\b)(?=[^\n]*\binclude(?:if\b[^\n]*)?\.path\b)|"
     r"^(?=[^\n]*(?:\.git[\\/](?:config|modules[\\/]\S+[\\/]config|worktrees[\\/]\S+[\\/]config)|\.gitconfig))"
     r"(?=[^\n]*(?:\b(?:cp|mv|ln|install|dd|rsync)\b|\bsed\b[^\n]*\s-i|\bperl\b[^\n]*\s-p?i))|"
     r"(?:>>?|\btee\b(?:\s+-\S+)*)\s*['\"]?[^\s;&|'\"]*"
     r"(?:\.git[\\/](?:config|worktrees[\\/][^\s/\\]+[\\/]config)|\.gitconfig|\.config[\\/]git[\\/]config)\b",
     "new publication channel (gist, a new or repointed git remote or git config file, a push to a URL, "
     "or a gh body file/command output sent to a named repo) — Rule S-9 criterion 6"),
    (r"\b(?:requests|httpx)\.(?:post|put|patch)\(|\burlopen\([^)\n]*(?:data=|,)|"
     r"\bsocket\.(?:socket|create_connection)\(|\bfetch\([^)\n]*method:\s*['\"](?:POST|PUT|PATCH)",
     "interpreter-mediated outbound send (Rule S-9 criterion 6)"),
    # ── Delete-authorization gate (operator-directed; C-5: clearance precedes destruction) ──────────
    # BEST-EFFORT, COOPERATIVE speed bump — NOT a boundary. It routes a NAMED SUBSET of Bash delete
    # idioms to the operator ("ask") when the harness honors PreToolUse AND a human answers. It does
    # NOT cover: Write/Edit content-shrink, MCP / non-Bash tool deletes, interpreter-mediated deletion
    # not matched below, shell aliases or variable/quote obfuscation, or non-Claude consumers that do
    # not implement PreToolUse. Under headless / auto-approve it may be bypassed. Its own patterns can
    # be stripped by editing this file — an edit that agentteams.integrity.verify() detects (the hook
    # is pinned in ENFORCEMENT_MODULES), not this hook self-checking. A green delete-gate test suite
    # means "these spellings are gated", never "deletion is prevented". Authoritative limits:
    # security.template.md.
    (r"\bgh\b[^\n]*\s\w+\s+delete\b|\bgh\s+api\b[^\n]*(?:-X\s*DELETE|--method\s+DELETE)",
     "GitHub resource deletion via gh (repo/release/etc.) — irreversible"),
    # A merge is outside every PR agent's remit and outside the github-write MCP allowlist; the same token
    # through gh would otherwise get around both (MCP catalogue, @security implementation review cond. 6).
    # `gh [flags] pr [flags] merge` only (flags may sit on either side of `pr`: `gh pr -R o/r merge`), so a
    # "merge" in a PR body or a later `git merge` doesn't prompt. The REST merge endpoint and the GraphQL
    # merge mutations prompt whatever sends them (gh api, curl, wget, an interpreter), not only gh: matching
    # the client let `curl -X PUT …/pulls/N/merge` through (mathAgents @technical-validator, 2026-10-08).
    # Each flag token must parse exactly one way: a `-R`/`--repo` value may not start with `-`, and a
    # flag is `-{1,2}` + a word char. Overlapping alternatives here backtracked exponentially — 14
    # repeats of ` --repo -R` took ~50 s, and a hook the harness times out lets the command run. (No
    # atomic groups: the hook may run under a system python3 older than 3.11.)
    (r"\bgh(?:\s+(?:-R|--repo)\s+[^\s-]\S*|\s+-{1,2}\w[\w-]*(?:=\S+)?)*\s+pr"
     r"(?:\s+(?:-R|--repo)\s+[^\s-]\S*|\s+-{1,2}\w[\w-]*(?:=\S+)?)*\s+merge\b|"
     r"\bpulls/[^\s/]+/merge\b|"
     r"\b(?:mergePullRequest|enablePullRequestAutoMerge)\b",
     "pull-request merge — human review decides; merges go through @git-operations' reviewed path"),
    (r"\bgit\b(?=[^\n]*\bpush\b)(?=[^\n]*(?:--delete|--mirror|--prune|\s:\S))",
     "remote branch/tag deletion or mirror/prune push — irreversible remote loss"),
    (r"\bgit\b[^\n]*\b(?:branch|tag)\s+(?:-[a-zA-Z]*[dD]\b|--delete\b)|"
     r"\bgit\b[^\n]*\bupdate-ref\s+-d\b|\bgit\b[^\n]*\bworktree\s+remove\b",
     "git branch/tag/ref/worktree deletion (any -C/--git-dir prefix)"),
    # argparse accepts unique prefixes (`--branch-c`, `--appl`), and the CLI is reachable as
    # `agentteams`, `python -m agentteams.cli.app` or `build_team.py`; the Python API too.
    (r"\b(?:agentteams|build_team)\b(?=[^\n]*--branch-[cp])(?=[^\n]*--appl)|"
     r"\bbranch_cleanup\b[^\n]*\b(?:run_cleanup|run_post_merge|execute_items)\b",
     "branch deletion through agentteams (its git deletes run inside the tool, unseen here)"),
    (r"\brm\s+\S|\b(?:unlink|srm|wipe|rmdir|shred|truncate)\b|\bfind\b[^\n]*\s-delete\b|"
     r"\bdd\b[^\n]*\bof=|>\|\s*\S|(?:^|[;&|])\s*:\s*>\s*\S|\bmv\b[^\n]*\s/dev/null\b|\bcp\s+/dev/null\b",
     "irreversible filesystem deletion or truncation/overwrite"),
    (r"\bkubectl\b[^\n]*\bdelete\b|\bhelm\s+uninstall\b|\bterraform\s+(?:destroy|state\s+rm)\b|"
     r"\bterraform\s+apply\b[^\n]*-destroy|"
     r"\bdocker\s+(?:container\s+rm|image\s+rm|rm|rmi|volume\s+(?:rm|prune)|system\s+prune)\b|"
     r"\bdocker\s+compose\s+down\b[^\n]*(?:-v|--volumes)|"
     r"\baws\s+\S+\s+delete-|\baws\s+s3\s+r[bm]\b|\bgcloud\b[^\n]*\sdelete\b|\baz\b[^\n]*\sdelete\b|"
     r"\bdropdb\b",
     "infrastructure resource deletion"),
    (r"\b(?:psql|mysql|mariadb|sqlite3?|mongo|redis-cli|clickhouse[-a-z]*)\b[^\n]*"
     r"(?:\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b)",
     "database deletion via a client invocation"),
    (r"\b(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir)|rimraf)\b|\.unlink\(|\bfs\.rm(?:Sync|dirSync)?\(",
     "interpreter-mediated deletion (python/node/etc.)"),
)

_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit"})

#: Bounds on what the regex triggers are asked to scan. Patterns with unbounded gaps are superlinear on
#: pathological input (a line of `git push git push …` took 89 s on one pattern), and a hook the
#: harness times out lets the command run. Every `[^\n]*` gap stops at a newline, so evaluating per
#: line changes no verdict; a line or command beyond the caps is routed to the operator instead of
#: being scanned slowly. Worst case at the caps is a few seconds on python3 3.9.
_MAX_LINE = 8192
_MAX_COMMAND = 32768
_GIT_CONFIG_PATH = re.compile(
    r"(?:^|[\\/])(?:\.git[\\/](?:config(?:\.worktree)?|(?:worktrees|modules)[\\/].+[\\/]config(?:\.worktree)?)"
    r"|\.gitconfig|\.config[\\/]git[\\/]config)$",
    re.IGNORECASE,
)


def _mask_quoted_separators(command: str) -> str:
    """Replace ``&``, ``|`` and ``;`` inside quotes with ``_`` so they no longer split a command.

    One linear pass tracking single/double quotes and backslash escapes. Unquoted separators —
    the ones the shell acts on — are untouched, so pipe and sequence patterns still see them.
    """
    out, quote, escaped = [], "", False
    for ch in command:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = ""
            elif ch in "&|;":
                ch = "_"
        elif ch in "'\"":
            quote = ch
        out.append(ch)
    return "".join(out)


def _bash_trigger(command: str) -> str | None:
    """The first Mandatory Review Trigger a Bash command matches, else None — in bounded time."""
    # A trailing pipe or `&&`/`||` continues the pipeline on the next line; join it so per-line
    # evaluation still sees `curl … |` and `sh` together.
    command = re.sub(r"(\||&&)[ \t]*\r?\n", r"\1 ", command)
    lines = command.splitlines() or [""]
    if len(command) > _MAX_COMMAND or any(len(line) > _MAX_LINE for line in lines):
        return (f"command too long to check reliably (over {_MAX_COMMAND} characters, or a line over "
                f"{_MAX_LINE}) — the gate routes it to you rather than scanning it slowly")
    command = _mask_quoted_separators(command)
    lines = command.splitlines() or [""]
    why = _command_name_trigger(command)
    if why:
        return why
    for pattern, reason in _BASH_REVIEW_TRIGGERS:
        if any(re.search(pattern, line, re.IGNORECASE) for line in lines):
            return reason
    return None


# ── Command-name triggers (tools whose names are also ordinary words) ─────────────────────────────
# A regex cannot say "in command position" both precisely and in linear time — the attempt here was
# quadratic (4.6 s on a 10k-char command, enough for padding to outlast the harness timeout). This
# tokenizer is linear: split on shell separators, drop quotes, then skip VAR=value prefixes, wrappers
# (with the flags that take a value) and a leading backslash or path, and read the real command name.
# Not covered, by design of a speed-bump: a name produced at run time (`$(which nc)`, aliases, `$X`).
_SEGMENT_SPLIT = re.compile(r"[\n;&|(){}`]|\$\(")
_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-D", "-r", "-t", "--user", "--group", "--chdir"},
    "doas": {"-u", "-C"}, "env": {"-u", "-C", "--unset", "--chdir"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"}, "nice": {"-n", "--adjustment"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--arg-file", "--max-args", "--max-procs",
              "--max-lines", "--delimiter", "--max-chars", "--eof"},
    "stdbuf": {"-i", "-o", "-e"}, "watch": {"-n", "--interval", "-d"}, "ionice": {"-c", "-n"},
    "flock": {"-w", "--timeout"}, "chrt": set(), "setsid": set(), "eval": set(), "unbuffer": set(),
    "caffeinate": set(), "coproc": set(), "command": set(), "exec": {"-a"}, "nohup": set(), "time": set(), "busybox": set(),
    "then": set(), "do": set(), "else": set(), "if": set(), "while": set(), "until": set(), "!": set(),
    "sh": set(), "bash": set(), "zsh": set(), "dash": set(),
}
#: Wrappers whose first positional argument is not the command (a duration, lock file, priority).
_WRAPPER_POSITIONALS = {"timeout": 1, "flock": 1, "chrt": 1}
_RAW_SOCKET_TOOLS = frozenset({"ncat", "socat", "telnet", "sftp", "scp", "ssh"})
_DNS_TOOLS = frozenset({"dig", "nslookup", "drill", "host"})


def _command_names(command: str) -> list[tuple[str, str]]:
    """Return ``(name, rest_of_segment)`` for each simple command, wrappers peeled off."""
    found = []
    for segment in _SEGMENT_SPLIT.split(command):
        # shlex honours quotes and drops a real comment (not a `#` inside quotes). A quoted argument
        # is then re-split so `bash -c 'nc …'` exposes `nc`; quote debris such as `FOO= #` is skipped
        # as punctuation. Unbalanced quotes (common once the line is split on `;`/`|`) fall back to
        # a plain split with the quotes dropped.
        try:
            words = shlex.split(segment, comments=True)
        except ValueError:
            words = [segment.replace("'", " ").replace('"', " ")]
        tokens = [w for word in words for w in word.split()]
        i, value_flags, positionals = 0, set(), 0
        while i < len(tokens):
            token = tokens[i].lstrip("\\")
            base = token.rsplit("/", 1)[-1]
            if token in value_flags:
                i += 2
                continue
            if re.fullmatch(r"\d*[<>]+&?", token):      # bare redirection: its target is the next token
                i += 2
                continue
            if (token.startswith("-") or re.fullmatch(r"\W+|\d*[<>]+&?\S+|\w+=\S*|\d+(?:\.\d+)?[smhd]?", token)):
                if positionals and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", token):
                    positionals -= 1   # `timeout 5`: the duration fills the wrapper's positional
                i += 1
                continue
            if base in _WRAPPERS:
                value_flags, positionals = _WRAPPERS[base], _WRAPPER_POSITIONALS.get(base, 0)
                i += 1
                continue
            if positionals:
                positionals -= 1
                i += 1
                continue
            found.append((base, " ".join(tokens[i + 1:])))
            break
    return found


def _command_name_trigger(command: str) -> str | None:
    """The trigger reason for a word-like network tool run as a command, else None."""
    for name, rest in _command_names(command):
        if name in _RAW_SOCKET_TOOLS or (name == "nc" and not re.search(r"(?:^|\s)-[a-zA-Z]*z", rest)):
            return "raw socket or copy to a remote host — arbitrary data can leave the machine (Rule S-9 criterion 6)"
    if re.search(r"\b(?:dig|nslookup|drill|host)\b[^\n|;&]*(?:\$\(|`)", command, re.IGNORECASE) and any(
            name in _DNS_TOOLS for name, _ in _command_names(command)):
        return "DNS lookup built from command output — a covert exfiltration channel (Rule S-9 criterion 6)"
    return None


def _decide(decision: str, reason: str) -> None:
    """Emit a hook decision and exit 0. Exit 0 + JSON is the structured-decision contract."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    raise SystemExit(0)


def _written_content(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Return ``(content, target_path)`` for a write-shaped tool call."""
    if tool_name == "Write":
        return tool_input.get("content", ""), tool_input.get("file_path", "")
    if tool_name in {"Edit", "MultiEdit"}:
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n".join(str(e.get("new_string", "")) for e in edits), tool_input.get("file_path", "")
        return str(tool_input.get("new_string", "")), tool_input.get("file_path", "")
    if tool_name == "NotebookEdit":
        return str(tool_input.get("new_source", "")), tool_input.get("notebook_path", "")
    return "", ""


#: The untracked file beside this hook naming the interpreter that rendered the team (one line).
_PIN_NAME = "agentteams-python"
#: Set on the re-executed child so it never re-executes again (loop guard).
_REEXEC_ENV = "AGENTTEAMS_GATE_REEXEC"
#: Variables that could redirect what the pinned interpreter imports or runs at startup.
_SCRUBBED_ENV = ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")
#: Below Claude Code's 60 s hook timeout; a timed-out child is treated as no interpreter.
_REEXEC_TIMEOUT = 30


def _project_roots() -> list[Path]:
    """The project tree(s) this hook guards: ``<project>/.claude/hooks/<this>`` and ``$CLAUDE_PROJECT_DIR``."""
    parents = Path(__file__).resolve().parents
    roots = [parents[2]] if len(parents) > 2 else []
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        roots.append(Path(env_root).resolve())
    return [r for r in roots if r != Path(r.anchor)]


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _pinned_interpreter() -> tuple[str | None, str]:
    """Return ``(interpreter, "")`` for a usable pin, else ``(None, why)``.

    Usable: an absolute path to an executable ``python*`` file that lies outside the project tree,
    judged both as written and resolved, with no ``pyvenv.cfg`` inside the project above it (an
    agent-writable virtualenv could swap what the interpreter loads).
    """
    pin_file = Path(__file__).resolve().parent / _PIN_NAME
    try:
        lines = pin_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None, f"no readable interpreter pin at {pin_file}"
    value = lines[0].strip() if lines else ""
    if not value or not os.path.isabs(value):
        return None, f"the interpreter pin {pin_file} is not an absolute path"
    if not os.path.basename(value).lower().startswith("python"):
        return None, f"the interpreter pin {pin_file} does not name a python interpreter"
    if not os.path.isfile(value) or not os.access(value, os.X_OK):
        return None, f"the interpreter pin {pin_file} does not name an existing executable"
    roots = _project_roots()
    found, resolved = Path(os.path.abspath(value)), Path(os.path.realpath(value))
    venv_inside = any((p / "pyvenv.cfg").is_file() and _inside(p, roots) for p in found.parents)
    if _inside(found, roots) or _inside(resolved, roots) or venv_inside:
        return None, f"the interpreter pin {pin_file} points inside the project tree, which agents can write"
    return value, ""


def _reexec_under_pin(raw: bytes) -> str:
    """Re-run this hook under the pinned interpreter with the original stdin bytes; relay its verdict.

    Exits with the child's exit code when the child produced a verdict (exit 0 or 2). Otherwise
    returns why no pinned interpreter could run the gate, so the caller fails visibly.
    """
    if os.environ.get(_REEXEC_ENV) == "1":
        return "already re-executed once under the pinned interpreter, which cannot import agentteams either"
    pin, why = _pinned_interpreter()
    if pin is None:
        return why
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
    env[_REEXEC_ENV] = "1"
    try:
        proc = subprocess.run([pin, str(Path(__file__).resolve())], input=raw, capture_output=True,
                              env=env, timeout=_REEXEC_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the pinned interpreter could not run the gate ({type(exc).__name__})"
    sys.stderr.buffer.write(proc.stderr)
    sys.stderr.flush()
    if proc.returncode not in (0, 2):
        return f"the pinned interpreter failed running the gate (exit {proc.returncode})"
    sys.stdout.buffer.write(proc.stdout)
    sys.stdout.flush()
    raise SystemExit(proc.returncode)


def _import_integrity(raw: bytes):
    """Import ``agentteams.integrity``, re-executing under the pin when ``agentteams`` is missing.

    Never returns without the module: with no interpreter that can import agentteams it emits
    ``deny`` (fail-closed) or ``ask`` (cooperative) — a crash here used to be a silent allow.
    """
    try:
        from agentteams import integrity
        return integrity
    except ModuleNotFoundError as exc:
        if exc.name != "agentteams":
            raise
    why = _reexec_under_pin(raw)
    # agentteams' own checkout: the interpreter puts this script's directory on sys.path, not the
    # repo root, so derive the root from this file's location (parents[2]) rather than trusting cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from agentteams import integrity
        return integrity
    except ModuleNotFoundError as exc:
        if exc.name != "agentteams":
            raise
    message = (
        f"the constitutional gate found no Python interpreter that can import agentteams ({why}), so "
        "this write's content was NOT checked. Fix: run `agentteams --check-wiring` in this project, or "
        "re-render the team from an environment where agentteams is installed so "
        f"{Path(__file__).resolve().parent / _PIN_NAME} names that interpreter."
    )
    print(f"agentteams constitutional gate: {message}", file=sys.stderr)
    _decide("deny" if _FAIL_CLOSED_ON_ERROR else "ask", message)
    raise SystemExit(2)  # unreachable: _decide exits


def main() -> int:
    raw = sys.stdin.buffer.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "Bash":
        # A backslash-newline is a line continuation to the shell; join it so no pattern is split by one.
        command = re.sub(r"\\\r?\n", " ", str(tool_input.get("command", "")))
        why = _bash_trigger(command)
        if why:
            _decide(
                "ask",
                f"Mandatory Review Trigger: {why}. C-5 requires a recorded clearance BEFORE "
                f"execution, not after. Approve only if this is the operation you intended.",
            )
        return 0

    if tool_name in _WRITE_TOOLS:
        content, target = _written_content(tool_name, tool_input)
        # Writing a git config file can add or repoint a remote with no `git remote` command for the
        # Bash gate to see; a later plain `git push` then sends to it (Rule S-9 criterion 6). Asked
        # only AFTER the content scan, so a credential in it still gets the deterministic deny.
        git_config = bool(_GIT_CONFIG_PATH.search(str(target)))
        if not content:
            if git_config:
                _ask_git_config()
            return 0

        # Verify the scanner before trusting its verdict. Without this the hook faithfully asks
        # a possibly-tampered module whether the write is safe, which is the E4 failure wearing
        # a hook. `ask` rather than `deny`: a stale manifest after a legitimate edit to scan.py
        # is the common case and must not brick the session — and `allow` would be the failure
        # this exists to catch.
        #
        # This does NOT escape E4. An attacker who can edit scan.py can edit the manifest and
        # this file. It raises the cost from one edit to three and makes each visible in git.
        # Without agentteams these imports crash, and a crash is a silent allow (any exit other
        # than 0/2 is a non-blocking error), so _import_integrity re-runs under the pinned
        # interpreter, falls back to this checkout's root, or fails visibly.
        integrity = _import_integrity(raw)

        integrity_findings = integrity.verify(Path.cwd())
        tampered = [f for f in integrity_findings if f.rel_path.endswith("scan.py")]
        # PR-D (row 31): a MISSING manifest where one is expected (this repo ships it, or the
        # scanner runs from inside the project) leaves the scanner unverifiable. Under the
        # confined fail-closed policy that is `ask` (not `deny`: a deleted manifest is also what
        # a botched regen looks like); the cooperative hook keeps its historical behaviour.
        if _FAIL_CLOSED_ON_ERROR:
            tampered += [f for f in integrity_findings if f.reason == "manifest-missing"]
        if tampered:
            _decide(
                "ask",
                "the content scanner does not match the integrity manifest ("
                + "; ".join(f.describe() for f in tampered)
                + "). Its verdict on this write cannot be trusted. Regenerate the manifest if "
                "you changed scan.py deliberately, or investigate.",
            )

        from agentteams.scan import scan_content, verdict_for_findings
        findings = scan_content(content, filename=target or "<hook>")
        high = [f for f in findings if f.severity == "high"]
        if high:
            detail = "; ".join(f"L{f.line} [{f.category}] {f.message}" for f in high[:3])
            _decide(
                "deny",
                f"agentteams.scan returned HALT ({verdict_for_findings(findings)}) for this "
                f"write: {detail}. Rule S-1/S-5/S-8 apply to any committed file. Remove the "
                f"flagged content rather than re-issuing the write.",
            )
        if git_config:
            _ask_git_config()
    return 0


def _ask_git_config() -> None:
    """Route a write to a git config file to the operator (Rule S-9 criterion 6)."""
    _decide("ask", "Mandatory Review Trigger: a write to a git config file can add or repoint a remote "
                   "(Rule S-9 criterion 6). Approve only if this is the change you intended.")


#: CC-2: agentteams flips this to True at emit time for a confined/exclusive
#: privilege_profile (unless --allow-fallback-fail-open was passed at generation). When
#: True, an UNEXPECTED crash in the gate blocks the tool call instead of letting the harness
#: fail open (a crash = silent allow, which disables the gate exactly where it matters). The
#: entry-point handler below is a process-BOUNDARY reporting handler — it acts (emits a
#: deny) and reports; it is not a swallow (CH-24), and it is the only broad catch here.
_FAIL_CLOSED_ON_ERROR = False


def _entrypoint() -> int:
    """Run the gate, applying the fail-open/fail-closed policy on an unexpected crash.

    Default (``_FAIL_CLOSED_ON_ERROR`` False): an unexpected error propagates and the
    harness treats the non-zero exit as an allow — the historical fail-open behavior a
    PreToolUse hook is designed around, so a buggy gate never bricks a session.

    Confined/exclusive (``_FAIL_CLOSED_ON_ERROR`` True): an unexpected error instead emits a
    ``deny`` decision, so a gate crash cannot become a silent allow in a workspace that
    explicitly opted into a boundary.

    Returns:
        The exit code from :func:`main` on the normal path.
    """
    try:
        return main()
    except SystemExit:
        raise
    except Exception as exc:  # process-boundary handler: acts (deny) + reports, never swallows
        if not _FAIL_CLOSED_ON_ERROR:
            raise
        _decide(
            "deny",
            f"the constitutional gate crashed ({type(exc).__name__}: {exc}); failing closed "
            "because this workspace uses a confined/exclusive privilege_profile — a gate "
            "crash must not become a silent allow. Fix the gate, or regenerate with "
            "--allow-fallback-fail-open to restore the harness fail-open default.",
        )
        raise SystemExit(2)  # belt: if _decide's exit-0 deny contract ever changes, still block


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
