#!/usr/bin/env bash
# Build the credential-free vulnscan agent image and prove its isolation (walkthrough Step 7).
# Run on the operator's machine, with Docker running.
#
# FRAMEWORK= selects the agent CLI (default goose; the runtime driver in agentteams/vulnscan/agent_drivers.py).
# goose, claude, copilot and codex are available in this release; grok is refused.
#
# What it does:
#   1. goose / codex: downloads the CLI's linux release tarball for this machine's architecture and checks its
#      sha256 against the digest GitHub publishes for that release asset (refuses on a mismatch, or when GitHub
#      publishes none unless --trust-download is given), and, for the version agentteams pins
#      (agent_drivers.<CLI>.image), against the digest recorded there too; codex: when cosign is installed,
#      also verifies the binary's sigstore bundle (identity: openai/codex's rust-release workflow for the tag),
#      and a version other than the pinned one must be 14 days old (Rule S-10) unless --trust-download;
#      copilot: no release checksum exists; the build runs `npm ci --ignore-scripts` from the committed
#      lockfile (agentteams/data/vulnscan_agent/copilot/) and `npm audit signatures`, and checks the installed
#      tree's hash against the one agentteams pins (only the pinned version has a lockfile);
#      claude: downloads the release's manifest.json, verifies its GPG signature against Anthropic's release
#      key (fingerprint checked; needs gpg, else only the pinned version is accepted), takes the linux
#      binary's sha256 from it and compares it with agent_drivers.CLAUDE.image for the pinned version;
#   2. resolves the python base image to an immutable digest (python:3.12-slim@sha256:...);
#   3. runs `agent-image build` with both pins, from the source checkout you name (default: this one);
#   4. runs `agent-selftest` if agent mode is configured (agent.team_dir set), else says what to set.
# It does not sign anything, read any key, or start an agent run.
#
# Usage:   [FRAMEWORK=goose|claude|copilot|codex] bash scripts/vulnscan-agent-image.sh [--cli-version X.Y.Z] [--source DIR] [--trust-download]
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
  --trust-download) TRUST=true;; -h|--help) sed -n '2,28p' "$0"; exit 0;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
case "$FRAMEWORK" in
  goose|claude|copilot|codex) ;;
  grok) echo "FRAMEWORK=$FRAMEWORK: driver not available in this release (goose, claude, copilot, codex)" >&2; exit 2;;
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

case "$(uname -m)" in arm64|aarch64) GARCH=aarch64-unknown-linux-gnu; XARCH=aarch64-unknown-linux-musl; CPLAT=linux-arm64; DARCH=arm64;;
  x86_64|amd64) GARCH=x86_64-unknown-linux-gnu; XARCH=x86_64-unknown-linux-musl; CPLAT=linux-x64; DARCH=amd64;;
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
elif [ "$FRAMEWORK" = copilot ]; then
  # Copilot CLI: no published checksum (plan revision 12). The build runs npm ci from the committed lockfile and
  # npm audit signatures, and checks the installed tree against the hash agentteams pins for this architecture.
  echo "== 1/4 copilot v$GV ($DARCH): committed lockfile"
  LOCKV="$("$PY" -P -c 'from agentteams.vulnscan import agent_drivers_copilot as c; print(c.lockfile_version())')"
  [ "$GV" = "$LOCKV" ] || { echo "the committed lockfile pins copilot v$LOCKV; v$GV needs a new lockfile and tree hash" >&2; exit 1; }
  [ "$PINSHA" != "-" ] || { echo "agentteams pins no installed-tree hash for $DARCH" >&2; exit 1; }
  SHA="$PINSHA"
  echo "installed-tree sha256 $SHA (pinned; npm ci checks every tarball's integrity, the build re-checks the tree)"
else
if [ "$FRAMEWORK" = codex ]; then REPO=openai/codex; TAG="rust-v$GV"; ASSET="codex-$XARCH.tar.gz"
else REPO=block/goose; TAG="v$GV"; ASSET="goose-$GARCH.tar.bz2"; fi
echo "== 1/4 $FRAMEWORK v$GV ($ASSET)"
curl -fsSL -o "$WORK/$ASSET" "https://github.com/$REPO/releases/download/$TAG/$ASSET"
SHA="$(shasum -a 256 "$WORK/$ASSET" | cut -d' ' -f1)"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh api "repos/$REPO/releases/tags/$TAG" > "$WORK/release.json" 2>/dev/null || true
else
  curl -fsSL "https://api.github.com/repos/$REPO/releases/tags/$TAG" > "$WORK/release.json" || true
fi
# the asset's published digest ("" when none) and the release's age in days (-1 when unknown)
read -r PUB AGE < <("$PY" -P -c 'import json, sys
from datetime import datetime, timezone
try:
    r = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    r = {}
a = [x for x in r.get("assets", []) if x.get("name") == sys.argv[2]]
t = r.get("published_at")
age = (datetime.now(timezone.utc) - datetime.fromisoformat(t.replace("Z", "+00:00"))).days if t else -1
print(((a[0].get("digest") or "").removeprefix("sha256:") if a else "") or "-", age)' "$WORK/release.json" "$ASSET")
[ "$PUB" = "-" ] && PUB=""
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
if [ "$FRAMEWORK" = codex ]; then
  if [ "$GV" != "$PINV" ] && [ "$AGE" -lt 14 ] && ! $TRUST; then
    echo "codex v$GV was published $AGE day(s) ago (unknown: -1): Rule S-10 needs 14; use the pinned v$PINV or --trust-download" >&2; exit 1
  fi
  ID="https://github.com/openai/codex/.github/workflows/rust-release.yml@refs/tags/$TAG"
  if command -v cosign >/dev/null; then
    curl -fsSL -o "$WORK/codex.sigstore" "https://github.com/$REPO/releases/download/$TAG/codex-$XARCH.sigstore"
    tar -xzf "$WORK/$ASSET" -C "$WORK" "codex-$XARCH"
    cosign verify-blob --bundle "$WORK/codex.sigstore" --certificate-identity "$ID" \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com "$WORK/codex-$XARCH" >/dev/null 2>&1 \
      || { echo "the codex binary's sigstore bundle does NOT verify for $ID — refusing" >&2; exit 1; }
    echo "sigstore: the codex binary is signed by $ID"
  else
    echo "NOTICE: cosign is not installed, so the codex binary's sigstore signature was NOT verified;" >&2
    echo "NOTICE: only the sha256 pin above was checked. Install cosign and re-run for the signature check." >&2
  fi
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
