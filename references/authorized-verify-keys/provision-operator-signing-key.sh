#!/usr/bin/env bash
# ============================================================================================
# provision-operator-signing-key.sh - OPERATOR-ONLY, run in your INTERACTIVE shell.
#
# Generates the Ed25519 keypair for signing constraint-relaxing security decisions:
#   * the PRIVATE key is written OUTSIDE the repository, into ~/.config/agentteams/keys/ (dir mode
#     700, file mode 600), and never printed. Every sandbox agentteams emits read-denies that
#     directory (F-1), so keep the key there;
#   * the PUBLIC key is written into <team dir>/references/authorized-verify-keys/<key-id>.pub.pem
#     for each --team-dir: the store the security gate reads (decision_log._VERIFY_KEY_STORE_REL,
#     relative to the team's agents dir, e.g. .claude/agents). The repository-root
#     references/authorized-verify-keys/ holds only this helper and its README, never a key.
#
# NEVER run this inside an agent/sandbox session, and NEVER commit or export the private key.
# The whole security model is that no agent context ever holds the private key; only the operator,
# in an interactive shell, reads it via `agentteams --sign-decision`.
#
# Usage:  provision-operator-signing-key.sh --team-dir DIR [--team-dir DIR]... <key-id>
#         provision-operator-signing-key.sh --migrate
#   --team-dir DIR             an agentteams team dir (it holds references/agent-privilege.json or
#                              references/build-log.json), e.g. .claude/agents. Repeatable. REQUIRED
#                              for a key-id: without it the helper refuses and lists the team dirs
#                              it finds (F-2: the gate never reads a repository-root store).
#   --migrate                  move legacy ~/.config/agentteams/*.pem keys into keys/ (mv -n: never
#                              overwrites, never deletes) and exit.
#   --allow-unprotected-keydir accept a KEY_DIR other than ~/.config/agentteams/keys. The sandboxes
#                              do NOT deny reads of any other directory, so a key there is readable
#                              by a sandboxed agent. Refused without this flag.
#   KEY_DIR=/custom/dir  (default: ~/.config/agentteams/keys)
# ============================================================================================
set -euo pipefail

CANONICAL_KEY_DIR="$HOME/.config/agentteams/keys"
LEGACY_DIR="$HOME/.config/agentteams"

KEY_ID=""; MIGRATE=0; ALLOW_UNPROTECTED=0; TEAM_DIRS=()
USAGE="usage: $0 [--allow-unprotected-keydir] --team-dir DIR [--team-dir DIR]... <key-id> | --migrate"
while [ $# -gt 0 ]; do
  arg="$1"; shift
  case "$arg" in
    --migrate) MIGRATE=1 ;;
    --allow-unprotected-keydir) ALLOW_UNPROTECTED=1 ;;
    --team-dir) [ $# -gt 0 ] && [ -n "$1" ] || { echo "--team-dir needs a directory" >&2; exit 2; }
                TEAM_DIRS+=("$1"); shift ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) [ -z "$KEY_ID" ] || { echo "$USAGE" >&2; exit 2; }
       KEY_ID="$arg" ;;
  esac
done

# Create (or check) the canonical key dir: mode 700, a real directory, never a symlink. Seatbelt
# matches resolved paths, so a symlinked key dir would escape the sandbox read-deny.
ensure_keys_dir(){
  local d="$1"
  [ -L "$d" ] && { echo "refusing: key dir '$d' is a symlink; the sandbox read-deny would not cover its target" >&2; exit 2; }
  if [ ! -d "$d" ]; then
    (umask 077 && mkdir -p "$d")
    chmod 700 "$d"
  fi
  local mode
  mode="$(stat -c '%a' "$d" 2>/dev/null || stat -f '%Lp' "$d")"
  case "$mode" in
    700) : ;;
    *) echo "refusing: key dir '$d' is mode $mode; it must be 700 (not group/world accessible). Fix: chmod 700 '$d'" >&2; exit 2 ;;
  esac
}

legacy_keys(){
  local f
  for f in "$LEGACY_DIR"/*.pem; do [ -f "$f" ] && [ ! -L "$f" ] && printf '%s\n' "$f"; done
  return 0
}

if [ "$MIGRATE" -eq 1 ]; then
  [ -z "$KEY_ID" ] || { echo "--migrate takes no key-id" >&2; exit 2; }
  [ -L "$LEGACY_DIR" ] && { echo "refusing: '$LEGACY_DIR' is a symlink" >&2; exit 2; }
  ensure_keys_dir "$CANONICAL_KEY_DIR"
  moved=0; skipped=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    dest="$CANONICAL_KEY_DIR/$(basename "$f")"
    if [ -e "$dest" ]; then
      echo "SKIPPED (destination exists, nothing overwritten): $f -> $dest" >&2
      skipped=$((skipped + 1)); continue
    fi
    mv -n "$f" "$dest"
    # mv -n exits 0 even when it declines to overwrite: detect the skip explicitly.
    if [ -e "$f" ] || [ ! -e "$dest" ]; then
      echo "SKIPPED (mv -n did not move it; nothing deleted): $f" >&2
      skipped=$((skipped + 1)); continue
    fi
    chmod 600 "$dest"
    echo "moved: $f -> $dest"
    echo "  new: export AGENTTEAMS_DECISION_ED25519_KEYFILE=\"$dest\""
    moved=$((moved + 1))
  done < <(legacy_keys)
  echo "migrate: $moved moved, $skipped skipped. No key was deleted."
  if [ "$moved" -gt 0 ]; then
    echo "WARNING: any AGENTTEAMS_DECISION_ED25519_KEYFILE exported with the OLD path (shell rc files," >&2
    echo "  CI secrets, direnv, password managers) now points at a missing file. Update each one." >&2
    for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.config/fish/config.fish"; do
      [ -f "$rc" ] && grep -n "AGENTTEAMS_DECISION_ED25519_KEYFILE" "$rc" 2>/dev/null | sed "s|^|  $rc:|" >&2 || true
    done
  fi
  [ "$skipped" -eq 0 ] || exit 1
  exit 0
fi

case "$KEY_ID" in
  ''|*[!A-Za-z0-9._-]*) echo "$USAGE  (key-id = letters/digits/._- only)" >&2; exit 2 ;;
esac

# The project root (git top-level, else this script's ../..): the private key must live OUTSIDE it.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"

is_team(){ [ -f "$1/references/agent-privilege.json" ] || [ -f "$1/references/build-log.json" ]; }
if [ "${#TEAM_DIRS[@]}" -eq 0 ]; then
  # F-2: the gate reads <team dir>/references/authorized-verify-keys, never this script's own
  # directory, so there is no safe default. Refuse and name the candidates.
  echo "refusing: no --team-dir given. The security gate reads the verify key from" >&2
  echo "  <team dir>/references/authorized-verify-keys/, so name the team(s) explicitly." >&2
  found=0
  for d in "$REPO_ROOT"/*/agents "$REPO_ROOT"/.*/agents "$REPO_ROOT"/*/recipes "$REPO_ROOT"/.*/recipes; do
    [ -d "$d" ] && is_team "$d" && { echo "  team dir found: --team-dir ${d#"$REPO_ROOT"/}" >&2; found=1; }
  done
  [ "$found" -eq 1 ] || echo "  (no agentteams team dir found under $REPO_ROOT)" >&2
  exit 2
fi
STORES=()
for d in "${TEAM_DIRS[@]}"; do
  [ -d "$d" ] && [ ! -L "$d" ] || { echo "refusing: --team-dir '$d' is not a directory (or is a symlink)" >&2; exit 2; }
  is_team "$d" || { echo "refusing: --team-dir '$d' is not an agentteams team dir (no references/agent-privilege.json or references/build-log.json)" >&2; exit 2; }
  STORES+=( "$(cd "$d" && pwd)/references/authorized-verify-keys" )
done

command -v openssl >/dev/null 2>&1 || { echo "openssl not found (required)" >&2; exit 2; }

KEY_DIR="${KEY_DIR:-$CANONICAL_KEY_DIR}"
case "$KEY_DIR/" in
  "$REPO_ROOT"/*) echo "refusing: KEY_DIR '$KEY_DIR' is inside the repo; the private key must live OUTSIDE it" >&2; exit 2 ;;
esac
if [ "${KEY_DIR%/}" = "$CANONICAL_KEY_DIR" ]; then
  ensure_keys_dir "$CANONICAL_KEY_DIR"
  KEY_DIR="$CANONICAL_KEY_DIR"
else
  if [ "$ALLOW_UNPROTECTED" -ne 1 ]; then
    echo "refusing: KEY_DIR '$KEY_DIR' is not $CANONICAL_KEY_DIR. Only that directory is read-denied" >&2
    echo "  in the sandboxes agentteams emits; a key elsewhere is readable by a sandboxed agent." >&2
    echo "  Re-run with --allow-unprotected-keydir to accept that." >&2
    exit 2
  fi
  [ -L "$KEY_DIR" ] && { echo "refusing: KEY_DIR '$KEY_DIR' is a symlink" >&2; exit 2; }
  (umask 077 && mkdir -p "$KEY_DIR")
  KEY_DIR="$(cd "$KEY_DIR" && pwd)"
  case "$KEY_DIR/" in
    "$REPO_ROOT"/*) echo "refusing: KEY_DIR '$KEY_DIR' is inside the repo; the private key must live OUTSIDE it" >&2; exit 2 ;;
  esac
  echo "WARNING!! KEY_DIR '$KEY_DIR' is NOT sandbox-denied: a sandboxed agent that can read it can" >&2
  echo "  mint relaxing authorizations with this key. Prefer $CANONICAL_KEY_DIR." >&2
fi

# Legacy keys are left in place (never moved without --migrate), but named loudly.
LEGACY="$(legacy_keys)"
if [ -n "$LEGACY" ]; then
  echo "WARNING: private key file(s) in the pre-F-1 location $LEGACY_DIR:" >&2
  printf '%s\n' "$LEGACY" | sed 's/^/  /' >&2
  echo "  Move them into $CANONICAL_KEY_DIR with: $0 --migrate" >&2
fi

PRIV="$KEY_DIR/decision-signing-$KEY_ID.pem"

[ -e "$PRIV" ] && { echo "refusing: private key already exists at $PRIV (rotate with a new key-id)" >&2; exit 2; }
for STORE in "${STORES[@]}"; do
  [ -e "$STORE/$KEY_ID.pub.pem" ] && { echo "refusing: public key already exists at $STORE/$KEY_ID.pub.pem (rotate with a new key-id)" >&2; exit 2; }
done

umask 077
openssl genpkey -algorithm ed25519 -out "$PRIV"
chmod 600 "$PRIV"

echo "OK. Provisioned Ed25519 signing key '$KEY_ID'."
echo "  private (KEEP SECRET, do NOT commit): $PRIV"
for STORE in "${STORES[@]}"; do
  mkdir -p "$STORE"
  openssl pkey -in "$PRIV" -pubout -out "$STORE/$KEY_ID.pub.pem"
  chmod 644 "$STORE/$KEY_ID.pub.pem"
  echo "  public (the store the gate reads)   : $STORE/$KEY_ID.pub.pem"
done
echo
echo "To sign a relaxing decision, in your interactive shell only:"
echo "  export AGENTTEAMS_DECISION_ED25519_KEYFILE=\"$PRIV\""
echo "  agentteams --sign-decision path/to/decision-spec.json --output <the same team dir>"
echo
echo "Commit each .pub.pem if its team dir is tracked (a .claude/ team dir is often gitignored, so"
echo "the key then stays local). NEVER commit or export $PRIV into an agent session."
