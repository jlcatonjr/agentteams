"""Shared output-dir resolution for standalone CLI commands (CH-07 carve).

A tiny leaf helper extracted from ``commands.py`` so it can be shared without coupling — no
import cycle (it depends only on ``argparse``/``pathlib``). ``commands.py`` re-imports
:func:`_resolve_output_dir` so every existing call site keeps working.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _resolve_output_dir(args: argparse.Namespace) -> Path:
    """Resolve the agents output dir for a standalone read-only command, mirroring
    ``--verify-waivers``: ``--output`` → ``--project`` → CWD."""
    if getattr(args, "output", None):
        return Path(args.output).resolve()
    if getattr(args, "project", None):
        return Path(args.project).resolve()
    return Path.cwd()
