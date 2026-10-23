#!/usr/bin/env bash
# Build the credential-free vulnscan agent image and prove its isolation (walkthrough Step 7).
# Run on the operator's machine, with Docker running.
#
# What it does:
#   1. downloads goose's linux release tarball for this machine's architecture and checks its sha256
#      against the digest GitHub publishes for that release asset (refuses on a mismatch, or when GitHub
#      publishes none unless --trust-download is given);
#   2. resolves the python base image to an immutable digest (python:3.12-slim@sha256:...);
#   3. runs `agent-image build` with both pins, from the source checkout you name (default: this one);
#   4. runs `agent-selftest` if agent mode is configured (agent.team_dir set), else says what to set.
# It does not sign anything, read any key, or start an agent run.
#
# Usage:   bash scripts/vulnscan-agent-image.sh [--goose-version 1.37.0] [--source DIR] [--trust-download]
#          VULNSCAN_PY=/path/to/python bash scripts/vulnscan-agent-image.sh
set -euo pipefail

GV="1.37.0"; SRC="$(cd "$(dirname "$0")/.." && pwd)"; TRUST=false
while [ $# -gt 0 ]; do case "$1" in
  --goose-version) GV="${2:?--goose-version needs a value}"; shift;; --source) SRC="${2:?--source needs a value}"; shift;;
  --trust-download) TRUST=true;; -h|--help) sed -n '2,16p' "$0"; exit 0;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

PY="${VULNSCAN_PY:-}"
if [ -z "$PY" ] && command -v pipx >/dev/null; then
  venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)"
  [ -n "$venvs" ] && [ -x "$venvs/agentteams/bin/python" ] && PY="$venvs/agentteams/bin/python"
fi
PY="${PY:-python3}"
"$PY" -c 'import agentteams.vulnscan' 2>/dev/null || { echo "agentteams is not importable with $PY (set VULNSCAN_PY)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop (or the daemon), then re-run." >&2; exit 2; }

case "$(uname -m)" in arm64|aarch64) GARCH=aarch64-unknown-linux-gnu;; x86_64|amd64) GARCH=x86_64-unknown-linux-gnu;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 2;; esac
ASSET="goose-$GARCH.tar.bz2"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-image.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT

echo "== 1/4 goose v$GV ($ASSET)"
curl -fsSL -o "$WORK/$ASSET" "https://github.com/block/goose/releases/download/v$GV/$ASSET"
SHA="$(shasum -a 256 "$WORK/$ASSET" | cut -d' ' -f1)"
PUB=""
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  PUB="$(gh api "repos/block/goose/releases/tags/v$GV" --jq ".assets[] | select(.name==\"$ASSET\") | .digest" 2>/dev/null | sed 's/^sha256://')"
else
  PUB="$(curl -fsSL "https://api.github.com/repos/block/goose/releases/tags/v$GV" | "$PY" -c \
    "import json,sys; a=[x for x in json.load(sys.stdin)['assets'] if x['name']=='$ASSET']; print((a[0].get('digest') or '').removeprefix('sha256:') if a else '')")"
fi
if [ -n "$PUB" ]; then
  [ "$PUB" = "$SHA" ] || { echo "sha256 MISMATCH: downloaded $SHA, GitHub publishes $PUB — refusing" >&2; exit 1; }
  echo "sha256 $SHA (matches the digest GitHub publishes for the asset)"
elif $TRUST; then
  echo "sha256 $SHA (GitHub publishes no digest for this asset; trusted because --trust-download was given)"
else
  echo "GitHub publishes no digest for $ASSET; verify $SHA yourself, then re-run with --trust-download" >&2; exit 1
fi

echo "== 2/4 python base image digest"
docker pull -q python:3.12-slim >/dev/null
PYIMG="$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim)"
case "$PYIMG" in python@sha256:*) PYIMG="python:3.12-slim@${PYIMG#python@}";; *) echo "could not resolve a digest: $PYIMG" >&2; exit 1;; esac
echo "$PYIMG"

echo "== 3/4 agent-image build (source: $SRC)"
"$PY" -P -m agentteams.vulnscan agent-image build --source "$SRC" \
  --goose-version "$GV" --goose-sha256 "$SHA" --python-image "$PYIMG"

echo "== 4/4 agent-selftest"
if "$PY" -c 'from agentteams.vulnscan import daily, agent_run; import sys; a = agent_run.agent_cfg(daily.load_config()); sys.exit(0 if a.get("team_dir") else 1)' 2>/dev/null; then
  "$PY" -P -m agentteams.vulnscan agent-selftest
else
  echo "Skipped: agent mode is not configured (vulnscan.json needs agent.team_dir and agent.key_file; walkthrough Step 6)."
  echo "Then run: $PY -P -m agentteams.vulnscan agent-selftest"
fi
