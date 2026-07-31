"""Process exit-code determination for the generate/update pipeline.

Carved out of ``cli/generate.py`` (CH-07): that module stood at 1012 lines against the 1000-line
ceiling, and mapping an :class:`~agentteams.emit.EmitResult` plus the parsed CLI flags to a
process exit code is a self-contained decision with one caller — a coherent unit rather than a
size-driven split.

``cli.generate`` re-exports :func:`_finalize_exit_code`, so ``cli.app``'s existing
``from agentteams.cli.generate import _finalize_exit_code`` and any test resolving it there keep
working unchanged.
"""

from __future__ import annotations

import argparse
import sys

from agentteams import emit


def _finalize_exit_code(result: emit.EmitResult, args: argparse.Namespace) -> int:
    """Return the final exit code, applying the optional fail-on-legacy-skip gate.

    A successful emit returns 0 by default. When ``--fail-on-legacy-skip`` is set and any files
    were skipped for missing fence markers, the run is promoted to non-zero so CI can enforce
    template propagation — a silent skip is how a template change fails to reach a deployed team.

    Args:
        result: The emit result for the run.
        args: Parsed CLI namespace.

    Returns:
        0 on success, 1 on failure or a gated legacy skip.
    """
    if not result.success:
        return 1
    if getattr(args, "fail_on_legacy_skip", False) and result.skipped_legacy:
        print(
            f"\nExit 1: --fail-on-legacy-skip set and "
            f"{len(result.skipped_legacy)} legacy file(s) skipped.",
            file=sys.stderr,
        )
        return 1
    return 0
