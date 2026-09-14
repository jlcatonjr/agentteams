#!/usr/bin/env bash
# ============================================================================================
# provision-operator-signing-key.sh - OPERATOR-ONLY, run in your INTERACTIVE shell.
#
# Generates the Ed25519 keypair for signing constraint-relaxing security decisions:
#   * the PRIVATE key is written OUTSIDE the repository (mode 600) and never printed;
#   * the PUBLIC key is written into references/authorized-verify-keys/<key-id>.pub.pem
#     (the tracked trust anchor agents verify against).
#
# NEVER run this inside an agent/sandbox session, and NEVER commit or export the private key.
# The whole security model is that no agent context ever holds the private key; only the operator,
# in an interactive shell, reads it via `agentteams --sign-decision`.
#
# Usage:  references/authorized-verify-keys/provision-operator-signing-key.sh <key-id>
#         KEY_DIR=/custom/dir  ...  (default: ~/.config/agentteams)
# ============================================================================================
set -euo pipefail

KEY_ID="${1:-}"
case "$KEY_ID" in
  ''|*[!A-Za-z0-9._-]*) echo "usage: $0 <key-id>  (key-id = letters/digits/._- only)" >&2; exit 2 ;;
esac

command -v openssl >/dev/null 2>&1 || { echo "openssl not found (required)" >&2; exit 2; }

# Resolve the repo root from this script's location so the public key lands in the right store,
# and so we can refuse to place the PRIVATE key anywhere inside the repo tree.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STORE="$REPO_ROOT/references/authorized-verify-keys"

KEY_DIR="${KEY_DIR:-$HOME/.config/agentteams}"
KEY_DIR="$(mkdir -p "$KEY_DIR" && cd "$KEY_DIR" && pwd)"
case "$KEY_DIR/" in
  "$REPO_ROOT"/*) echo "refusing: KEY_DIR '$KEY_DIR' is inside the repo; the private key must live OUTSIDE it" >&2; exit 2 ;;
esac

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
