#!/usr/bin/env python3
"""Resilient wrapper around ``goose run``: detects a dead turn and auto-continues.

SCOPE — WHAT THIS DOES *NOT* COVER (added 2026-07-24)
-----------------------------------------------------
This wraps the ``goose run`` CLI. It cannot protect the **Goose VS Code extension**,
the desktop app, or anything else speaking the Agent Client Protocol: those run
``goose acp`` as a long-lived daemon that this script never invokes, so no subprocess
wrapper can observe or retry their turns. That is a limit of the approach, not a bug.

For a mitigation that covers **every** goose surface, select the upstream backend
instead of retrying after the fact: ``scripts/goose-openrouter-route-proxy.py`` injects
OpenRouter provider routing under ``OPENROUTER_HOST``, which goose honors on all paths.
Backend choice matters a lot -- measured leak rates on one real payload ranged from 0/12
to 3/12 across backends serving the *same* model. See ``docs_src/goose-cloud-providers.md``.
The two are complementary: route to a good backend, and still auto-continue on the CLI.

Some tool-calling-capable models occasionally emit their tool call as literal
``<tool_call>...</tool_call>`` text *inside* a ``"thinking"`` response block instead
of via the provider's structured tool-call field. Goose only recognizes the
structured form, so the turn ends with no ``text`` and no ``toolRequest`` content —
nothing reaches the user, and goose logs no error anywhere (confirmed empirically:
see ``references/plans/goose-openrouter-tool-call-reasoning-leak-2026-07-24.report.md``).
The turn just silently "dies."

This was confirmed live against OpenRouter + a reasoning-capable model. **Whether
non-OpenRouter providers (including local Ollama) ever hit the same failure mode, or
write the same ``llm_request.*.jsonl`` log shape this script reads, is UNVERIFIED** —
Ollama's grammar-constrained decoding makes the specific tag-leak structurally
unlikely, but that is not the same as confirming this script's log-classification
logic behaves correctly there. Accordingly this script is **fail-closed**: any log
read it cannot confidently classify (missing file, empty, malformed JSON, or a shape
it doesn't recognize) is treated as "alive" (no action taken), never as "dead." It
will only ever resubmit "Continue" when it has positively confirmed no actionable
content reached the user for the run *it itself launched* — never as a guess.

This is a client-side safety net that recovers a turn after the fact, not a fix for
the underlying cause. **Correction (2026-07-24):** an earlier version of this note
said ``OPENROUTER_PARAMETERS.provider.require_parameters: true`` "was tested and does
NOT fix it." That setting is **inert in Goose 1.37.0** — goose never sends it — so it
was never actually tested. What *does* reduce the failure rate is choosing the
upstream backend, which varies substantially in tool-call correctness for the same
model; see ``scripts/goose-openrouter-route-proxy.py`` and the scope note above.

Usage::

    python3 goose-run-resilient.py -t "<prompt>" [--provider P] [--model M] \\
        [--max-continues N] [-- <extra goose run args>]
    python3 goose-run-resilient.py --recipe path/to/recipe.yaml [options...]

Exit codes::

    0  the run produced actionable content (first try or after continuing)
    1  every attempt (including all continues) ended in a dead turn
    2  setup error (goose binary missing, no -t/--recipe given, etc.)

Caveat carried from the source investigation: ``--max-continues`` defaults to 3,
which is a *floor*, not a comfortable margin — the worst case observed in live
testing needed exactly 3 continues before recovering. Raise it for sessions that
can tolerate extra latency/cost on the rare worst case.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MAX_CONTINUES = 3
DEFAULT_TIMEOUT = 120.0
LOG_GLOB = os.path.expanduser("~/.local/state/goose/logs/llm_request.*.jsonl")


# ---------------------------------------------------------------------------
# Pure logic (unit-tested offline; no network, no subprocess, no real filesystem
# beyond the log-file reads this module is specifically about classifying)
# ---------------------------------------------------------------------------


def classify_request_log(lines: list[str]) -> dict[str, object]:
    """Classify one ``llm_request.*.jsonl`` file's lines (already read) as alive/dead.

    Fail-closed: any line that isn't valid JSON is skipped, not treated as an error.
    A file with zero recognizable content blocks classifies as alive (``ok=True``,
    ``reason='no_content_blocks_found'``) — never as dead. Dead is returned ONLY when
    at least one recognizable response line was found and NONE of them carried
    ``text`` or ``toolRequest`` content.
    """
    saw_any_content_block = False
    has_tool_request = False
    has_text = False
    output_tokens = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if isinstance(data, dict):
            for block in data.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in ("text", "toolRequest"):
                    saw_any_content_block = True
                    if block_type == "toolRequest":
                        has_tool_request = True
                    else:
                        has_text = True
                elif block_type == "thinking":
                    saw_any_content_block = True
        usage = entry.get("usage")
        if isinstance(usage, dict):
            output_tokens = usage.get("output_tokens")

    if not saw_any_content_block:
        return {"ok": True, "dead": False, "reason": "no_content_blocks_found", "output_tokens": output_tokens}

    dead = not has_text and not has_tool_request
    return {
        "ok": True,
        "dead": dead,
        "reason": "dead_turn" if dead else "alive",
        "has_text": has_text,
        "has_tool_request": has_tool_request,
        "output_tokens": output_tokens,
    }


def request_matches_prompt(first_line: str, prompt_substring: str) -> bool:
    """Guard against a ring-buffer race with a concurrent goose session: confirm the
    captured log file's request actually contains this run's own prompt text before
    trusting anything else read from it. Fail-closed: any parse failure -> no match.
    """
    try:
        entry = json.loads(first_line)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(entry, dict):
        return False
    messages = entry.get("input", {}).get("messages", []) if isinstance(entry.get("input"), dict) else []
    blob = json.dumps(messages)
    needle = prompt_substring[:40]
    return bool(needle) and needle in blob


def pick_newest_candidate(paths_with_mtime: list[tuple[str, float]], after_mtime: float) -> str | None:
    """Pure selection logic: newest-mtime path at/after a cutoff, or None."""
    candidates = [(mtime, path) for path, mtime in paths_with_mtime if mtime >= after_mtime]
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


# ---------------------------------------------------------------------------
# Edge I/O (filesystem, subprocess) — kept out of the pure-logic path above
# ---------------------------------------------------------------------------


class SetupError(Exception):
    """A setup failure unrelated to any single turn's classification."""


def read_log_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def find_and_classify_latest_run(prompt_text: str, after_mtime: float) -> dict[str, object]:
    """Find the newest llm_request.*.jsonl written at/after `after_mtime`, verify it
    belongs to THIS run (prompt match), and classify it. Fail-closed at every step:
    no matching file, or a file that doesn't match this run's prompt, -> alive/no-op.
    """
    paths_with_mtime: list[tuple[str, float]] = []
    for path in glob.glob(LOG_GLOB):
        try:
            paths_with_mtime.append((path, os.path.getmtime(path)))
        except OSError:
            continue

    chosen = pick_newest_candidate(paths_with_mtime, after_mtime)
    if chosen is None:
        return {"ok": True, "dead": False, "reason": "no_log_file_found"}

    lines = read_log_lines(chosen)
    if not lines:
        return {"ok": True, "dead": False, "reason": "log_file_empty"}

    if not request_matches_prompt(lines[0], prompt_text):
        return {"ok": True, "dead": False, "reason": "log_did_not_match_this_run_prompt_race_guard"}

    return classify_request_log(lines)


def run_goose(args: list[str], timeout: float) -> tuple[str, int]:
    try:
        proc = subprocess.run(
            ["goose", "run"] + args, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise SetupError("goose binary not found on PATH") from exc
    except subprocess.TimeoutExpired:
        return "", 124
    return proc.stdout, proc.returncode


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_with_autocontinue(
    prompt_text: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_continues: int = DEFAULT_MAX_CONTINUES,
    timeout: float = DEFAULT_TIMEOUT,
    extra_args: list[str] | None = None,
    session_name: str | None = None,
) -> dict[str, object]:
    session_name = session_name or f"resilient-{abs(hash((prompt_text, time.monotonic())))%10**8}"
    instruction = prompt_text
    transcript: list[dict[str, object]] = []
    final_stdout = ""

    for attempt in range(max_continues + 1):
        args = ["-t", instruction, "-n", session_name]
        if attempt > 0:
            args.append("--resume")
        if provider:
            args += ["--provider", provider]
        if model:
            args += ["--model", model]
        args += extra_args or []

        after_mtime = time.time() - 1
        stdout, returncode = run_goose(args, timeout)
        time.sleep(0.3)  # let goose's own log writer flush before we read it

        classify_prompt = instruction if attempt == 0 else "Continue"
        classification = find_and_classify_latest_run(classify_prompt, after_mtime)
        is_dead = bool(classification.get("dead")) and bool(stdout.strip()) is False

        transcript.append(
            {
                "attempt": attempt,
                "instruction": instruction,
                "returncode": returncode,
                "classification": classification,
            }
        )
        final_stdout = stdout
        if not is_dead:
            break
        instruction = "Continue"

    return {
        "session_name": session_name,
        "num_attempts": len(transcript),
        "needed_continue": len(transcript) > 1,
        "final_dead": bool(transcript[-1]["classification"].get("dead")),
        "final_stdout": final_stdout,
        "transcript": transcript,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("-t", "--text", help="prompt text (same as `goose run -t`)")
    target.add_argument("--recipe", help="path to a recipe file (same as `goose run --recipe`)")
    parser.add_argument("--provider", default=None, help="forwarded to `goose run --provider` if given")
    parser.add_argument("--model", default=None, help="forwarded to `goose run --model` if given")
    parser.add_argument("--max-continues", type=int, default=DEFAULT_MAX_CONTINUES,
                         help=f"retry cap (default {DEFAULT_MAX_CONTINUES} — a floor from one observed "
                              "worst case, not a comfortable margin; raise it if your session can "
                              "tolerate extra latency/cost on the rare worst case)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-attempt timeout (s)")
    parser.add_argument("--session-name", default=None, help="override the auto-generated session name")
    parser.add_argument("--json", action="store_true", help="emit a JSON report instead of just stdout")
    parser.add_argument("extra_args", nargs=argparse.REMAINDER,
                         help="any remaining args are forwarded to `goose run` unchanged")
    args = parser.parse_args(argv)

    prompt_text = args.text if args.text is not None else f"Follow the recipe at {args.recipe}"
    extra = (["--recipe", args.recipe] if args.recipe else []) + list(args.extra_args or [])

    try:
        result = run_with_autocontinue(
            prompt_text,
            provider=args.provider,
            model=args.model,
            max_continues=args.max_continues,
            timeout=args.timeout,
            extra_args=extra,
            session_name=args.session_name,
        )
    except SetupError as exc:
        print(f"goose-run-resilient: SETUP ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["final_stdout"])
        if result["needed_continue"]:
            print(
                f"\n--- {result['num_attempts']} attempt(s) needed "
                f"(recovered from {result['num_attempts'] - 1} dead turn(s)) ---",
                file=sys.stderr,
            )

    return 1 if result["final_dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
