#!/usr/bin/env bash
# Build the credential-free vulnscan agent image and prove its isolation (walkthrough Step 7).
# Run on the operator's machine, with Docker running.
#
# FRAMEWORK= selects the agent CLI (default goose; the runtime driver in agentteams/vulnscan/agent_drivers.py).
# goose and claude are available in this release; copilot, codex and grok are refused.
#
# What it does:
#   1. goose: downloads goose's linux release tarball for this machine's architecture and checks its sha256
#      against the digest GitHub publishes for that release asset (refuses on a mismatch, or when GitHub
#      publishes none unless --trust-download is given), and, for the version agentteams pins
#      (agent_drivers.GOOSE.image), against the digest recorded there too;
#      claude: downloads the release's manifest.json, verifies its GPG signature against Anthropic's release
#      key (fingerprint checked; needs gpg, else only the pinned version is accepted), takes the linux
#      binary's sha256 from it and compares it with agent_drivers.CLAUDE.image for the pinned version;
#   2. resolves the python base image to an immutable digest (python:3.12-slim@sha256:...);
#   3. runs `agent-image build` with both pins, from the source checkout you name (default: this one);
#   4. runs `agent-selftest` if agent mode is configured (agent.team_dir set), else says what to set.
# It does not sign anything, read any key, or start an agent run.
#
# Usage:   [FRAMEWORK=goose] bash scripts/vulnscan-agent-image.sh [--cli-version X.Y.Z] [--source DIR] [--trust-download]
#          (--goose-version is an alias of --cli-version; the default is the version agentteams pins)
#          VULNSCAN_PY=/path/to/python bash scripts/vulnscan-agent-image.sh
set -euo pipefail

# gpg_status_ok FPR < gpg --status-fd output: succeed only on a GOODSIG from FPR's key id plus a VALIDSIG naming
# FPR, with no EXPKEYSIG, REVKEYSIG, EXPSIG, BADSIG or ERRSIG line (an expired or revoked key, an expired or bad
# signature, or one gpg could not check, is refused even when another line looks good).
gpg_status_ok() {
  local fpr="$1" status
  status="$(cat)"
  if grep -Eq '^\[GNUPG:\] (EXPKEYSIG|REVKEYSIG|EXPSIG|BADSIG|ERRSIG)( |$)' <<<"$status"; then return 1; fi
  grep -q "^\[GNUPG:\] GOODSIG ${fpr: -16} " <<<"$status" || return 1
  grep -q "^\[GNUPG:\] VALIDSIG $fpr " <<<"$status" || return 1
}
# Sourced (tests): define the function only.
[ "${BASH_SOURCE[0]}" = "$0" ] || return 0

FRAMEWORK="${FRAMEWORK:-goose}"; GV=""; SRC="$(cd "$(dirname "$0")/.." && pwd)"; TRUST=false
while [ $# -gt 0 ]; do case "$1" in
  --cli-version|--goose-version) GV="${2:?$1 needs a value}"; shift;; --source) SRC="${2:?--source needs a value}"; shift;;
  --trust-download) TRUST=true;; -h|--help) sed -n '2,23p' "$0"; exit 0;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
case "$FRAMEWORK" in
  goose|claude) ;;
  copilot|codex|grok) echo "FRAMEWORK=$FRAMEWORK: driver not available in this release (goose, claude)" >&2; exit 2;;
  *) echo "FRAMEWORK must be one of goose, claude, copilot, codex, grok (got '$FRAMEWORK')" >&2; exit 2;;
esac

PY="${VULNSCAN_PY:-}"
if [ -z "$PY" ] && command -v pipx >/dev/null; then
  venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)"
  [ -n "$venvs" ] && [ -x "$venvs/agentteams/bin/python" ] && PY="$venvs/agentteams/bin/python"
fi
PY="${PY:-python3}"
"$PY" -P -c 'import agentteams.vulnscan' 2>/dev/null || { echo "agentteams is not importable with $PY (set VULNSCAN_PY)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop (or the daemon), then re-run." >&2; exit 2; }

case "$(uname -m)" in arm64|aarch64) GARCH=aarch64-unknown-linux-gnu; CPLAT=linux-arm64; DARCH=arm64;;
  x86_64|amd64) GARCH=x86_64-unknown-linux-gnu; CPLAT=linux-x64; DARCH=amd64;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 2;; esac
# The pinned version and its recorded digest for this architecture (one source of truth: the driver).
read -r PINV PINSHA < <("$PY" -P -c 'import sys; from agentteams.vulnscan import agent_drivers as d
i = d.get_driver(sys.argv[1]).image; print(i.version, dict(i.sha256).get(sys.argv[2], "-"))' "$FRAMEWORK" "$DARCH")
GV="${GV:-$PINV}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-image.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT

if [ "$FRAMEWORK" = claude ]; then
  # Claude Code: the release's manifest.json lists each native binary's sha256 and is GPG-signed by
  # Anthropic's release key (https://code.claude.com/docs/en/setup, "Binary integrity and code signing").
  # The binary itself is downloaded and checked inside the Docker build (CLI_SHA256).
  CC_FPR=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
  REL="https://downloads.claude.ai/claude-code-releases/$GV"
  echo "== 1/4 claude v$GV ($CPLAT): signed release manifest"
  curl -fsSL -o "$WORK/manifest.json" "$REL/manifest.json"
  SHA="$("$PY" -P -c 'import json, sys; m = json.load(open(sys.argv[1])); assert m["version"] == sys.argv[3], "manifest version mismatch"
print(m["platforms"][sys.argv[2]]["checksum"])' "$WORK/manifest.json" "$CPLAT" "$GV")"
  if command -v gpg >/dev/null; then
    curl -fsSL -o "$WORK/manifest.json.sig" "$REL/manifest.json.sig"
    curl -fsSL -o "$WORK/claude-code.asc" https://downloads.claude.ai/keys/claude-code.asc
    mkdir -m 700 "$WORK/gnupg"
    gpg --homedir "$WORK/gnupg" --batch --quiet --import "$WORK/claude-code.asc" 2>/dev/null
    gpg --homedir "$WORK/gnupg" --batch --with-colons --fingerprint 2>/dev/null | grep -q "^fpr:::::::::$CC_FPR:" \
      || { echo "the downloaded release key is not $CC_FPR — refusing" >&2; exit 1; }
    gpg --homedir "$WORK/gnupg" --batch --status-fd 1 --verify "$WORK/manifest.json.sig" "$WORK/manifest.json" 2>/dev/null \
      | gpg_status_ok "$CC_FPR" \
      || { echo "manifest.json signature does NOT verify against $CC_FPR (needs GOODSIG and VALIDSIG; no expired, revoked, bad or unchecked signature) — refusing" >&2; exit 1; }
    echo "manifest.json: good signature from $CC_FPR"
  elif [ "$GV" = "$PINV" ] && [ "$PINSHA" != "-" ]; then
    echo "gpg not installed: relying on the digest agentteams pins for v$GV (verified when it was pinned)"
  elif $TRUST; then
    echo "gpg not installed and v$GV is not the pinned version; trusted because --trust-download was given"
  else
    echo "gpg is needed to verify the manifest of v$GV (or use the pinned v$PINV, or --trust-download)" >&2; exit 1
  fi
  if [ "$GV" = "$PINV" ] && [ "$PINSHA" != "-" ] && [ "$SHA" != "$PINSHA" ]; then
    echo "sha256 MISMATCH: the manifest lists $SHA, agentteams pins $PINSHA for v$GV — refusing" >&2; exit 1
  fi
  echo "sha256 $SHA ($CPLAT, from the signed manifest; the build re-checks the binary)"
else
ASSET="goose-$GARCH.tar.bz2"
echo "== 1/4 goose v$GV ($ASSET)"
curl -fsSL -o "$WORK/$ASSET" "https://github.com/block/goose/releases/download/v$GV/$ASSET"
SHA="$(shasum -a 256 "$WORK/$ASSET" | cut -d' ' -f1)"
PUB=""
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  PUB="$(gh api "repos/block/goose/releases/tags/v$GV" --jq ".assets[] | select(.name==\"$ASSET\") | .digest" 2>/dev/null | sed 's/^sha256://')"
else
  PUB="$(curl -fsSL "https://api.github.com/repos/block/goose/releases/tags/v$GV" | "$PY" -P -c \
    "import json,sys; a=[x for x in json.load(sys.stdin)['assets'] if x['name']=='$ASSET']; print((a[0].get('digest') or '').removeprefix('sha256:') if a else '')")"
fi
if [ "$GV" = "$PINV" ] && [ "$PINSHA" != "-" ] && [ "$SHA" != "$PINSHA" ]; then
  echo "sha256 MISMATCH: downloaded $SHA, agentteams pins $PINSHA for v$GV — refusing" >&2; exit 1
fi
if [ -n "$PUB" ]; then
  [ "$PUB" = "$SHA" ] || { echo "sha256 MISMATCH: downloaded $SHA, GitHub publishes $PUB — refusing" >&2; exit 1; }
  echo "sha256 $SHA (matches the digest GitHub publishes for the asset)"
elif $TRUST; then
  echo "sha256 $SHA (GitHub publishes no digest for this asset; trusted because --trust-download was given)"
else
  echo "GitHub publishes no digest for $ASSET; verify $SHA yourself, then re-run with --trust-download" >&2; exit 1
fi
fi

echo "== 2/4 python base image digest"
docker pull -q python:3.12-slim >/dev/null
PYIMG="$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim)"
case "$PYIMG" in python@sha256:*) PYIMG="python:3.12-slim@${PYIMG#python@}";; *) echo "could not resolve a digest: $PYIMG" >&2; exit 1;; esac
echo "$PYIMG"

echo "== 3/4 agent-image build (source: $SRC)"
"$PY" -P -m agentteams.vulnscan agent-image build --source "$SRC" --framework "$FRAMEWORK" \
  --cli-version "$GV" --cli-sha256 "$SHA" --python-image "$PYIMG"

echo "== 4/4 agent-selftest"
if "$PY" -P -c 'from agentteams.vulnscan import daily, agent_run; import sys; a = agent_run.agent_cfg(daily.load_config()); sys.exit(0 if a.get("team_dir") else 1)' 2>/dev/null; then
  "$PY" -P -m agentteams.vulnscan agent-selftest
else
  echo "Skipped: agent mode is not configured (vulnscan.json needs agent.team_dir and agent.key_file; walkthrough Step 6)."
  echo "Then run: $PY -P -m agentteams.vulnscan agent-selftest"
fi
