#!/usr/bin/env bash
# 01 — install agentteams for signing and the daily job (walkthrough "Before you start").
#
# Installs a COMMITTED revision of this checkout with pipx and the [signing] extra. It installs from
# `git archive <ref>` (never the working tree, whose build/ and __pycache__ leftovers would end up in the
# wheel), so the install lives in pipx's venv, outside every git work tree: signing accepts it.
# After a merge or a release, re-run this (or `pipx install agentteams[signing]` from a verified release).
#
# Usage:   bash scripts/vulnscan-operator/01-install.sh [REF]      (default REF: HEAD)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REF="${1:-HEAD}"
command -v pipx >/dev/null || { echo "pipx is required (brew install pipx && pipx ensurepath)" >&2; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-install.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/src"
git -C "$REPO" archive "$REF" | tar -x -C "$WORK/src"
echo "== installing $(git -C "$REPO" rev-parse --short "$REF") with pipx"
pipx install --force "$WORK/src[signing]"

PY="$(pipx environment --value PIPX_LOCAL_VENVS)/agentteams/bin/python"
"$PY" -c 'import agentteams.vulnscan, cryptography' || { echo "install check failed" >&2; exit 1; }
"$PY" -P -m agentteams.vulnscan --help >/dev/null
echo
echo "Installed. The other scripts find this interpreter automatically; to use it by hand:"
echo "  $PY -P -m agentteams.vulnscan --help"
command -v agentteams >/dev/null || echo "Note: 'agentteams' is not on PATH yet: run 'pipx ensurepath' and open a new terminal."
