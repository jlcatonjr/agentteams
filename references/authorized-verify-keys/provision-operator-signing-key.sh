#!/usr/bin/env bash
# ============================================================================================
# provision-operator-signing-key.sh - OPERATOR-ONLY, run in your INTERACTIVE shell.
#
# Generates the Ed25519 keypair for signing constraint-relaxing security decisions:
#   * the PRIVATE key is written OUTSIDE the repository, into ~/.config/agentteams/keys/ (dir mode
#     700, file mode 600), and never printed. Every sandbox agentteams emits read-denies that
#     directory (F-1), so keep the key there;
#   * the PUBLIC key is written into references/authorized-verify-keys/<key-id>.pub.pem
#     (the tracked trust anchor agents verify against).
#
# NEVER run this inside an agent/sandbox session, and NEVER commit or export the private key.
# The whole security model is that no agent context ever holds the private key; only the operator,
# in an interactive shell, reads it via `agentteams --sign-decision`.
#
# Usage:  references/authorized-verify-keys/provision-operator-signing-key.sh <key-id>
#         references/authorized-verify-keys/provision-operator-signing-key.sh --migrate
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

KEY_ID=""; MIGRATE=0; ALLOW_UNPROTECTED=0
for arg in "$@"; do
  case "$arg" in
    --migrate) MIGRATE=1 ;;
    --allow-unprotected-keydir) ALLOW_UNPROTECTED=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) [ -z "$KEY_ID" ] || { echo "usage: $0 [--allow-unprotected-keydir] <key-id> | --migrate" >&2; exit 2; }
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
  ''|*[!A-Za-z0-9._-]*) echo "usage: $0 [--allow-unprotected-keydir] <key-id> | --migrate  (key-id = letters/digits/._- only)" >&2; exit 2 ;;
esac

command -v openssl >/dev/null 2>&1 || { echo "openssl not found (required)" >&2; exit 2; }

# Resolve the repo root from this script's location so the public key lands in the right store,
# and so we can refuse to place the PRIVATE key anywhere inside the repo tree.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STORE="$REPO_ROOT/references/authorized-verify-keys"

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
PUB="$STORE/$KEY_ID.pub.pem"

[ -e "$PRIV" ] && { echo "refusing: private key already exists at $PRIV (rotate with a new key-id)" >&2; exit 2; }
[ -e "$PUB" ]  && { echo "refusing: public key already exists at $PUB (rotate with a new key-id)" >&2; exit 2; }

umask 077
openssl genpkey -algorithm ed25519 -out "$PRIV"
chmod 600 "$PRIV"
mkdir -p "$STORE"
openssl pkey -in "$PRIV" -pubout -out "$PUB"
chmod 644 "$PUB"

echo "OK. Provisioned Ed25519 signing key '$KEY_ID'."
echo "  private (KEEP SECRET, do NOT commit): $PRIV"
echo "  public  (commit this)               : references/authorized-verify-keys/$KEY_ID.pub.pem"
echo
echo "To sign a relaxing decision, in your interactive shell only:"
echo "  export AGENTTEAMS_DECISION_ED25519_KEYFILE=\"$PRIV\""
echo "  agentteams --sign-decision path/to/decision-spec.json"
echo
echo "Then commit references/authorized-verify-keys/$KEY_ID.pub.pem. NEVER commit or export $PRIV into an agent session."
