#!/usr/bin/env bash
# Operator action (self-updating-agents plan, second revision, rule 6): install a systemd USER
# path + service unit that runs `agentteams --sync-agent-docs --apply` for ONE project whenever
# one of its agent dirs changes, so learned blocks written by goose/Copilot/Claude agents reach the
# other frameworks' copies of the same agent. It runs OUTSIDE every agent session (agents inside a
# Claude Code sandbox cannot write .claude/agents, and must not run unsandboxed code).
#
# OPERATOR-ONLY. An agent must never run --apply: a path unit is a persistence mechanism.
#   * dry run by default (prints the unit files); --apply installs; --remove uninstalls
#   * systemd USER units only; refuses root / sudo; never enables linger
#   * fixed command, fixed environment, no agent-supplied arguments:
#       env -i PATH=/usr/bin:/bin HOME=%h <abs python> -I -m agentteams.cli.app \
#           --sync-agent-docs --project <abs project> --apply
#     `-I` (isolated mode) ignores PYTHON* variables, the user site dir and the current directory,
#     so nothing in the project can shadow the agentteams package. .claude/agents targets are never
#     written by the unit (they are staged; the operator runs --apply --include-claude by hand).
#   * refuses when the python interpreter, its prefix or base prefix, the agentteams package, the
#     sync state dir (~/.local/state/agentteams), the systemd user unit dir or the log dir lies
#     inside the project or inside any sandbox.filesystem.allowWrite root of
#     <project>/.claude/settings.json or settings.local.json (an agent could then edit what the
#     unit runs or trusts)
#   * bounded: 5 s debounce, 300 s hard timeout, start/trigger rate limits (an idempotent run
#     writes nothing, so it cannot re-trigger itself), UMask=0077
#   * logs to ~/.cache/agentteams/doc-sync-<project hash>.log (mode 0600)
#
# Usage:  bash scripts/install-agent-doc-sync.sh --project DIR [--python PATH] [--apply|--remove]
set -euo pipefail
MODE=dry; PROJECT=""; PY="$(command -v python3 || true)"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) MODE=apply; shift ;;
    --remove) MODE=remove; shift ;;
    --project) PROJECT="${2:?--project DIR}"; shift 2 ;;
    --python) PY="${2:?--python PATH}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -eq 0 ] || [ -n "${SUDO_USER:-}" ]; then
  echo "refusing: run as your own user, not root/sudo (systemd --user units only)." >&2; exit 2
fi
[ -n "$PROJECT" ] || { echo "refusing: --project DIR is required" >&2; exit 2; }
[ -d "$PROJECT" ] || { echo "refusing: not a directory: $PROJECT" >&2; exit 2; }
[ -n "$PY" ] && [ -x "$PY" ] || { echo "refusing: python interpreter not found/executable: ${PY:-<none>}" >&2; exit 2; }
case "$PY" in /*) ;; *) echo "refusing: --python must be an absolute path: $PY" >&2; exit 2 ;; esac
PROJECT="$(cd "$PROJECT" && pwd -P)"
# Every value embedded in a unit file: no whitespace/newline, quotes, shell metacharacters or `%`
# (a systemd specifier), so nothing can inject a directive or expand.
for v in "$PROJECT" "$PY"; do
  case "$v" in *[[:space:]\'\"\$\`\\%\;\&\|\<\>\*\?\[\]\(\)\{\}\#\!\~]*|"") echo "refusing: unsafe path for a unit file: $v" >&2; exit 2 ;; esac
done

# Containment + naming facts, computed by the SAME interpreter, in isolated mode, that the unit will
# run. Prints: <hash> TAB <escaped unit instance> TAB <agentteams package dir> TAB <has flag 0|1>,
# or exits 3 with the reason on stderr.
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# The check's source is read into a variable first: a heredoc nested inside a process substitution
# is mis-parsed by bash 3.2 (macOS) when its body contains a backtick.
PYSRC=""
IFS= read -r -d '' PYSRC <<'PYEOF' || true
import hashlib, json, os, sys
project, py, unit_dir, home = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
real = os.path.realpath(project)

def inside(path, root):
    """True when path (as given, or resolved) lies at/under root (as given, or resolved)."""
    for p in {os.path.abspath(path), os.path.realpath(path)}:
        for r in {os.path.abspath(root), os.path.realpath(root)}:
            if p == r or p.startswith(r.rstrip(os.sep) + os.sep):
                return True
    return False

roots = [("the project", real)]
for name in ("settings.json", "settings.local.json"):
    try:
        with open(os.path.join(real, ".claude", name), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        continue
    except (OSError, ValueError) as exc:
        sys.exit(f"refusing: cannot read {real}/.claude/{name}: {exc}")
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    fs = sandbox.get("filesystem") if isinstance(sandbox, dict) else None
    allow = fs.get("allowWrite") if isinstance(fs, dict) else None
    for entry in allow if isinstance(allow, list) else []:
        if isinstance(entry, str) and entry:
            path = os.path.expanduser(entry)
            roots.append((f"allowWrite root {entry!r} ({name})",
                          path if os.path.isabs(path) else os.path.join(real, path)))
def refuse_inside(what, target):
    for label, root in roots:
        if inside(target, root):
            sys.exit(f"refusing: the {what} ({target}) lies inside {label} ({root}); an agent could "
                     "edit what the unit runs or trusts")

# The interpreter and its prefixes are checked before anything is imported from them, so a refusal names the
# real problem (an interpreter inside the project) instead of an import failure.
# The unit runs with HOME=%h and no XDG_* variables, so its state dir and log dir are fixed.
for what, target in (("python interpreter", py), ("python prefix", sys.prefix),
                     ("python base prefix", sys.base_prefix),
                     ("sync state dir", os.path.join(home, ".local", "state", "agentteams")),
                     ("systemd user unit dir", unit_dir),
                     ("log dir", os.path.join(home, ".cache", "agentteams"))):
    refuse_inside(what, target)
try:
    import agentteams
except ImportError:
    sys.exit(f"refusing: {py} -I cannot import agentteams")
pkg = os.path.dirname(os.path.realpath(agentteams.__file__))
refuse_inside("agentteams package", pkg)
for c in pkg:
    if c.isspace() or c in "'\"$`\\%;&|<>":
        sys.exit(f"refusing: unsafe agentteams package path: {pkg}")

def escape(path):  # systemd-escape --path
    parts = [p for p in path.split("/") if p]
    out = []
    for i, ch in enumerate("/".join(parts)):
        if ch == "/":
            out.append("-")
        elif ch.isascii() and (ch.isalnum() or ch in ":_") or (ch == "." and i > 0):
            out.append(ch)
        else:
            out.extend(f"\\x{b:02x}" for b in ch.encode("utf-8"))
    return "".join(out) or "-"

has_flag = 0
try:
    from agentteams.cli.parser import _build_parser
    has_flag = int(any("--sync-agent-docs" in a.option_strings for a in _build_parser()._actions))
except (ImportError, AttributeError, SystemExit):
    has_flag = 0
print(hashlib.sha256(real.encode()).hexdigest()[:16], escape(real), pkg, has_flag, sep="\t")
PYEOF
CHECK_OUT="$("$PY" -I -c "$PYSRC" "$PROJECT" "$PY" "$UNIT_DIR" "$HOME")" || exit 2
IFS=$'\t' read -r HASH ESCAPED PKG HASFLAG <<< "$CHECK_OUT"
[ -n "${HASH:-}" ] || { echo "refusing: containment check produced no result" >&2; exit 2; }

DIRS=()
for d in .github/agents .goose/recipes .claude/agents; do
  if [ -L "$PROJECT/${d%%/*}" ] || [ -L "$PROJECT/$d" ]; then
    echo "refusing: $PROJECT/$d (or its parent) is a symlink" >&2; exit 2
  fi
  [ -d "$PROJECT/$d" ] && DIRS+=("$PROJECT/$d")
done
[ "${#DIRS[@]}" -gt 0 ] || { echo "refusing: no agent dirs (.github/agents, .goose/recipes, .claude/agents) in $PROJECT" >&2; exit 2; }

NAME="agentteams-doc-sync@$ESCAPED"
LOG="%h/.cache/agentteams/doc-sync-$HASH.log"
WATCH=""
for d in "${DIRS[@]}"; do WATCH="${WATCH}PathChanged=$d
"; done

PATH_UNIT="[Unit]
Description=agentteams learned-block doc sync trigger ($PROJECT)

[Path]
${WATCH}TriggerLimitIntervalSec=60
TriggerLimitBurst=10

[Install]
WantedBy=default.target
"
SERVICE="[Unit]
Description=agentteams learned-block doc sync ($PROJECT)
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Type=oneshot
WorkingDirectory=$PROJECT
TimeoutStartSec=300
UMask=0077
NoNewPrivileges=yes
ExecStartPre=/bin/sleep 5
ExecStart=/bin/sh -c 'mkdir -p %h/.cache/agentteams && touch $LOG && chmod 600 $LOG && exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=%h $PY -I -m agentteams.cli.app --sync-agent-docs --project $PROJECT --apply >> $LOG 2>&1'
"

case "$MODE" in
  dry)
    echo "DRY RUN. agentteams package: $PKG"
    [ "$HASFLAG" = 1 ] || echo "WARNING: the agentteams importable by $PY -I has no --sync-agent-docs; --apply will refuse until it is upgraded."
    echo "Would write $UNIT_DIR/$NAME.path:"; echo "$PATH_UNIT"
    echo "and $UNIT_DIR/$NAME.service:"; echo "$SERVICE"
    echo "Claude targets are only staged by the unit; review them with:"
    echo "  $PY -I -m agentteams.cli.app --sync-agent-docs --project $PROJECT --apply --include-claude"
    echo "Re-run with --apply to install (your user only; no linger), or --remove to uninstall." ;;
  apply)
    [ "$HASFLAG" = 1 ] || { echo "refusing: $PY -I imports an agentteams without --sync-agent-docs ($PKG)" >&2; exit 2; }
    command -v systemctl >/dev/null || { echo "systemctl not found" >&2; exit 2; }
    mkdir -p "$UNIT_DIR"
    umask 077
    printf '%s' "$PATH_UNIT" > "$UNIT_DIR/$NAME.path"
    printf '%s' "$SERVICE" > "$UNIT_DIR/$NAME.service"
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME.path"
    echo "Installed. Check: systemctl --user status '$NAME.path' ; log: ~/.cache/agentteams/doc-sync-$HASH.log" ;;
  remove)
    systemctl --user disable --now "$NAME.path" 2>/dev/null || true
    rm -f "$UNIT_DIR/$NAME.path" "$UNIT_DIR/$NAME.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed." ;;
esac
