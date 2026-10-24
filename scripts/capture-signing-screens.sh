#!/usr/bin/env bash
# Regenerate the vulnscan walkthrough screenshots from a CLEAN install, in a real terminal, so the
# grant-sign (06) and resolve (09) captures show the real interactive review.
#
# Why this script exists: signing refuses a development checkout (by design), and the resolve prompt
# needs a pseudo-terminal. So the generator must run (a) from agentteams installed into a throwaway venv
# outside every git work tree, and (b) in a terminal.
#
# What it does (nothing outside a temporary directory, except the figures it rewrites):
#   1. creates a venv under $TMPDIR and pip-installs this checkout into it, with the [signing] extra
#      (network: PyPI, for cryptography and the build backend);
#   2. runs scripts/gen_vulnscan_screenshots.py --installed from inside that temp dir, which stages a
#      throwaway demo HOME (demo key, demo repos, example.com host; nothing real is read or contacted
#      except PyPI release data for the demo stage-fix);
#   3. rewrites docs_src/book/figures/vulnscan/*.svg and shows which changed;
#   4. deletes the venv.
#
# Usage:   bash scripts/capture-signing-screens.sh            (from anywhere; uses python3)
#          PYTHON=/path/to/python3.12 bash scripts/capture-signing-screens.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"

[ -t 0 ] && [ -t 1 ] || { echo "Run this in an interactive terminal: the signing and resolve prompts need one." >&2; exit 2; }
command -v "$PY" >/dev/null || { echo "python not found: $PY (set PYTHON=...)" >&2; exit 2; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11+ is required ($("$PY" -V 2>&1))" >&2; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-capture.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "== 1/3 clean install of the committed HEAD into $WORK/venv"
# From `git archive`, not the working tree: a working tree's build/ and __pycache__ leftovers would
# end up in the wheel, and the signing install-location check would report the install as modified.
mkdir "$WORK/src"
git -C "$REPO" archive HEAD | tar -x -C "$WORK/src"
"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install --quiet --upgrade pip
"$WORK/venv/bin/python" -m pip install --quiet "$WORK/src[signing]"

echo "== 2/3 capturing (answer nothing: the script drives the prompts itself)"
cd "$WORK"
"$WORK/venv/bin/python" "$REPO/scripts/gen_vulnscan_screenshots.py" --installed

echo "== 3/3 changed figures"
git -C "$REPO" status --short -- docs_src/book/figures/vulnscan
for f in 06-grant-sign 09-resolve; do
  if [ -f "$REPO/docs_src/book/figures/vulnscan/$f.svg" ]; then echo "ok: $f.svg written"; else echo "MISSING: $f.svg"; fi
done
echo "Done. Report the result: which figures changed, and any MISSING line above."
