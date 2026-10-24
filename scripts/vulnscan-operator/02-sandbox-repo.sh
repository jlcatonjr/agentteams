#!/usr/bin/env bash
# 02 — create a private GitHub test repository with a known-vulnerable pin, for a supervised first run.
#
# Creates <you>/vulnscan-sandbox (private) holding requirements.txt with PyJWT==2.3.0 (advisory
# GHSA-ffqj-6fqr-9h24), and clones it to ~/vulnscan-sandbox-root/vulnscan-sandbox with origin/HEAD set.
# The grant in step 05 covers only ~/vulnscan-sandbox-root, so nothing else is in scope.
# Uses your current `gh` login. Delete the repository afterwards with:
#   gh repo delete <you>/vulnscan-sandbox
#
# Usage:   bash scripts/vulnscan-operator/02-sandbox-repo.sh [NAME]     (default NAME: vulnscan-sandbox)
set -euo pipefail

NAME="${1:-vulnscan-sandbox}"
ROOT="$HOME/vulnscan-sandbox-root"
DEST="$ROOT/$NAME"
gh auth status >/dev/null 2>&1 || { echo "gh is not signed in (gh auth login)" >&2; exit 2; }
OWNER="$(gh api user --jq .login)"
[ -e "$DEST" ] && { echo "$DEST already exists; remove it or pick another NAME" >&2; exit 2; }

mkdir -p "$DEST"
cd "$DEST"
git init -q -b main
printf 'PyJWT==2.3.0\nrequests==2.32.4\n' > requirements.txt
printf '# %s\n\nThrowaway repository for a supervised agentteams vulnscan run. Safe to delete.\n' "$NAME" > README.md
git add -A
git -c user.name="vulnscan sandbox" -c user.email="vulnscan-sandbox@users.noreply.github.com" commit -q -m "sandbox: a known-vulnerable pin"
gh repo create "$OWNER/$NAME" --private --source . --remote origin --push >/dev/null
git remote set-head origin --auto >/dev/null
echo "Created private https://github.com/$OWNER/$NAME"
echo "Local clone: $DEST (origin/HEAD -> $(git symbolic-ref --short refs/remotes/origin/HEAD))"
echo "Repo root for vulnscan: $ROOT"
