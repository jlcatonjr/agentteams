#!/usr/bin/env bash
# Build the vulnscan TOOLS image (git, OpenSSH, netcat, Node.js + npm, the egress proxy) and prove its isolation.
# Run on the operator's machine, with Docker running. Executor ops and apply-worktree staging refuse to run until
# `tools-selftest` has passed for the image this builds (agentteams/vulnscan/subproc_confine.py, fail closed).
#
# What it does:
#   1. Node.js: downloads the pinned release's SHASUMS256.txt (agentteams/vulnscan/tools_image.py NODE_VERSION) and
#      checks that its linux-x64 and linux-arm64 tarball lines equal the sha256 values agentteams pins; with gpg
#      installed it also verifies SHASUMS256.txt.sig against the pinned releaser key(s) NODE_RELEASE_SIGNERS
#      (key fetched from github.com/nodejs/release-keys, fingerprint checked). The tarball itself is checked
#      inside the Docker build;
#   2. resolves the python base image to an immutable digest (python:3.12-slim@sha256:...);
#   3. runs `tools-image build` with both pins;
#   4. runs `tools-selftest` (read-only rootfs, no home or key dirs, uid, capabilities, egress refused to a
#      non-allowlisted host and allowed to the listed one).
# It reads no key and contacts only nodejs.org, raw.githubusercontent.com and the Docker registry.
#
# Usage:   bash scripts/vulnscan-tools-image.sh
#          VULNSCAN_PY=/path/to/python bash scripts/vulnscan-tools-image.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=vulnscan-agent-image.sh
. "$HERE/vulnscan-agent-image.sh"   # sourced: defines gpg_status_ok only

PY="${VULNSCAN_PY:-}"
if [ -z "$PY" ] && command -v pipx >/dev/null; then
  venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)"
  [ -n "$venvs" ] && [ -x "$venvs/agentteams/bin/python" ] && PY="$venvs/agentteams/bin/python"
fi
PY="${PY:-python3}"
"$PY" -P -c 'import agentteams.vulnscan.tools_image' 2>/dev/null \
  || { echo "agentteams (with tools_image) is not importable with $PY (set VULNSCAN_PY)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop (or the daemon), then re-run." >&2; exit 2; }

read -r NV SHA_X64 SHA_ARM SIGNERS < <("$PY" -P -c 'from agentteams.vulnscan import tools_image as t
print(t.NODE_VERSION, t.NODE_SHA256["amd64"], t.NODE_SHA256["arm64"], ",".join(t.NODE_RELEASE_SIGNERS))')
WORK="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-tools.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT

echo "== 1/4 Node.js v$NV: SHASUMS256.txt against the pins"
REL="https://nodejs.org/dist/v$NV"
curl -fsSL -o "$WORK/SHASUMS256.txt" "$REL/SHASUMS256.txt"
for pair in "linux-x64:$SHA_X64" "linux-arm64:$SHA_ARM"; do
  plat="${pair%%:*}"; want="${pair#*:}"
  got="$(awk -v f="node-v$NV-$plat.tar.xz" '$2 == f {print $1}' "$WORK/SHASUMS256.txt")"
  [ "$got" = "$want" ] || { echo "sha256 MISMATCH for $plat: SHASUMS256.txt lists '$got', agentteams pins $want — refusing" >&2; exit 1; }
done
echo "linux-x64 and linux-arm64 match the pinned sha256 values"
if command -v gpg >/dev/null; then
  curl -fsSL -o "$WORK/SHASUMS256.txt.sig" "$REL/SHASUMS256.txt.sig"
  mkdir -m 700 "$WORK/gnupg"
  ok=false
  IFS=',' read -r -a FPRS <<<"$SIGNERS"
  for fpr in "${FPRS[@]}"; do
    curl -fsSL -o "$WORK/$fpr.asc" "https://raw.githubusercontent.com/nodejs/release-keys/HEAD/keys/$fpr.asc" || continue
    gpg --homedir "$WORK/gnupg" --batch --quiet --import "$WORK/$fpr.asc" 2>/dev/null || true
    gpg --homedir "$WORK/gnupg" --batch --with-colons --fingerprint 2>/dev/null | grep -q "^fpr:::::::::$fpr:" || continue
    if gpg --homedir "$WORK/gnupg" --batch --status-fd 1 --verify "$WORK/SHASUMS256.txt.sig" "$WORK/SHASUMS256.txt" 2>/dev/null \
        | gpg_status_ok "$fpr"; then ok=true; echo "SHASUMS256.txt: good signature from $fpr"; break; fi
  done
  $ok || { echo "SHASUMS256.txt does NOT verify against the pinned releaser key(s) $SIGNERS — refusing" >&2; exit 1; }
else
  echo "gpg not installed: relying on the sha256 values agentteams pins (verified against the signature when pinned)"
fi

echo "== 2/4 python base image digest"
docker pull -q python:3.12-slim >/dev/null
PYIMG="$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim)"
case "$PYIMG" in python@sha256:*) PYIMG="python:3.12-slim@${PYIMG#python@}";; *) echo "could not resolve a digest: $PYIMG" >&2; exit 1;; esac
echo "$PYIMG"

echo "== 3/4 tools-image build"
"$PY" -P -m agentteams.vulnscan tools-image build --python-image "$PYIMG" \
  --node-version "$NV" --node-sha256-amd64 "$SHA_X64" --node-sha256-arm64 "$SHA_ARM"

echo "== 4/4 tools-selftest"
"$PY" -P -m agentteams.vulnscan tools-selftest
