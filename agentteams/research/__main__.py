"""``python -m agentteams.research <search|fetch> ...`` — a thin CLI for the two calls that need
no chat backend.

This exists specifically so an LLM agent with shell/``execute`` tool access (but no way to
natively ``import`` and call a Python function) has a concrete, documented way to invoke
``search``/``fetch``. ``verify``'s functions are NOT exposed here: they require a real chat
backend to do their work, which a shelled-out subprocess has no channel to receive from the
caller — a project wiring up ``extract_claims``/``audit_claims``/``revise`` does so via a direct
Python import in its own runtime, not through this CLI. See
``templates/domain/research-analyst.template.md`` for the documented split.

Deliberately separate from ``agentteams``'s own console-script CLI (``build_team.py`` /
``agentteams``/``build-team`` entry points) — adding a flag there would touch ``agentteams.1``
and this repo's man-page-regen/SemVer obligations, a different boundary than this subpackage's.
This module's ``python -m`` invocation touches neither.
"""

from __future__ import annotations

import argparse
import json
import sys

from agentteams.research.search import fetch_text, web_search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentteams.research")
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="No-key web search")
    search_p.add_argument("query")
    search_p.add_argument("-k", type=int, default=5)
    search_p.add_argument("--timeout-s", type=float, default=8.0)

    fetch_p = sub.add_parser("fetch", help="Fetch and extract page text (HTML or PDF)")
    fetch_p.add_argument("url")
    fetch_p.add_argument("--max-chars", type=int, default=4000)
    fetch_p.add_argument("--timeout-s", type=float, default=8.0)

    args = parser.parse_args(argv)

    if args.command == "search":
        results = web_search(args.query, k=args.k, timeout_s=args.timeout_s)
        json.dump([r.__dict__ for r in results], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.command == "fetch":
        text = fetch_text(args.url, max_chars=args.max_chars, timeout_s=args.timeout_s)
        json.dump({"url": args.url, "text": text}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
