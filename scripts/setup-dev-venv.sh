#!/usr/bin/env bash
# Create the project dev venv (follow-up #20): .venv (gitignored) with .[test,research,signing], so the
# suite and the signing CLI run on the PINNED cryptography (50.0.0) and pytest>=8 rather than a base
# conda/system environment's copies. No sudo; installs from PyPI only, honouring the exact pins in
# pyproject.toml (the [signing] extra pins cryptography==50.0.0, vetted in references/dependency-pins.json).
#
# Rule S-10: review the extras' dependency versions (pyproject.toml) for known vulnerabilities before the
# first install. Dry run by default; --apply creates/updates .venv.
# Usage: bash scripts/setup-dev-venv.sh [--apply]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
if [ "$(id -u)" -eq 0 ]; then echo "refusing: run as your own user (no sudo)." >&2; exit 2; fi
git -C "$ROOT" check-ignore -q .venv/probe || { echo "refusing: .venv is not gitignored in $ROOT" >&2; exit 2; }
if [ "${1:-}" != "--apply" ]; then
  echo "DRY RUN. Would run:"
  echo "  $PY -m venv .venv"
  echo "  .venv/bin/python -m pip install --upgrade pip"
  echo "  .venv/bin/python -m pip install --index-url https://pypi.org/simple -e '.[test,research,signing]'"
  echo "Re-run with --apply. Then use .venv/bin/python -m pytest."
  exit 0
fi
cd "$ROOT"
"$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet --index-url https://pypi.org/simple -e '.[test,research,signing]'
.venv/bin/python - <<'PYV'
import importlib.metadata as m
for d in ("agentteams", "cryptography", "pytest", "build"):
    try:
        print(f"  {d}=={m.version(d)}")
    except m.PackageNotFoundError:
        print(f"  {d}: not installed")
PYV
echo "Done. Run the suite with: .venv/bin/python -m pytest"
