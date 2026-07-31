"""``--dry-run --json`` stdout discipline: the JSON document, and nothing else.

``--help`` promises "the per-file action plan as a single JSON document on stdout". It did not
deliver one. Measured 2026-07-30, stdout carried nine lines of human-readable progress and one
``[DRY RUN] WRITE`` line per file *ahead* of the JSON, so ``json.load(sys.stdin)`` failed at line
1, char 0 and the documented ``jq`` piping was impossible.

Auditing every ``print`` in a 900-line pipeline would fix it once and re-break on the next added
line. Instead this module inverts the default: in JSON mode ``sys.stdout`` is redirected to
stderr for the whole run, and the real handle is handed only to the function that emits the
document. Progress narration is preserved rather than suppressed — stderr is where it belongs
once stdout is a data channel.

Carved out of ``cli/generate.py`` rather than living there: that module sat at 998 lines against
the 1000-line CH-07 ceiling, and this logic is a self-contained concern with one entry point, so
it is a natural unit rather than a size-driven split of convenience.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable
from typing import Any

#: The real stdout while a JSON-mode run has redirected ``sys.stdout`` to stderr. ``None``
#: outside such a run, which is the signal that no redirection is in effect.
_JSON_STDOUT: Any = None


def json_stdout() -> Any:
    """Return the real stdout during a JSON-mode run, else ``None``.

    Returns:
        The stashed stdout handle when :func:`run_with_json_stdout` is active, otherwise
        ``None`` — which callers pass straight through as ``stream=None``, meaning "use
        ``sys.stdout``" and preserving pre-existing behaviour outside JSON mode.
    """
    return _JSON_STDOUT


def is_json_mode(args: argparse.Namespace) -> bool:
    """Whether this invocation must emit a machine-readable document on stdout.

    Args:
        args: Parsed CLI namespace.

    Returns:
        True only for ``--dry-run --json``. ``--json`` alone is documented as a no-op, so it
        must not trigger redirection.
    """
    return bool(getattr(args, "json", False) and getattr(args, "dry_run", False))


def run_with_json_stdout(
    inner: Callable[..., int],
    args: argparse.Namespace,
    *rest: Any,
) -> int:
    """Run ``inner(args, *rest)``, redirecting human output to stderr when in JSON mode.

    Outside JSON mode this is a straight call, so a caller running plain ``--dry-run`` sees
    byte-identical output to before and nothing parsing the text report is affected. (The JSON
    contract was broken, so nothing can have been relying on *it*.)

    **Re-entrant by design.** ``cli.app.main`` wraps the whole dispatch and ``cli.generate``
    wraps its own pipeline, so on a normal CLI run this is entered twice. A naive second entry
    would stash the *already-redirected* stdout — i.e. stderr — and the JSON document would go
    there instead. When a redirect is already active the nested call is therefore a pass-through,
    leaving the outermost caller's handle intact.

    Args:
        inner: The function to run. Receives ``(args, *rest)``.
        args: Parsed CLI namespace.
        *rest: Extra positional arguments forwarded to ``inner`` unchanged.

    Returns:
        ``inner``'s exit code.
    """
    global _JSON_STDOUT
    if not is_json_mode(args) or _JSON_STDOUT is not None:
        return inner(args, *rest)

    previous = _JSON_STDOUT
    _JSON_STDOUT = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return inner(args, *rest)
    finally:
        # Restored rather than cleared: tests drive main() repeatedly in one process, and a
        # stale handle would leak into the next run.
        _JSON_STDOUT = previous
