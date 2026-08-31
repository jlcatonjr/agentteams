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

from agentteams.research.scholarly import SOURCES, format_citation, scholarly_search
from agentteams.research.search import fetch_text, web_search_with_provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentteams.research")
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="No-key web search")
    search_p.add_argument("query")
    search_p.add_argument("-k", type=int, default=5)
    search_p.add_argument("--timeout-s", type=float, default=8.0)

    fetch_p = sub.add_parser("fetch", help="Fetch and extract page text (HTML or PDF)")
    fetch_p.add_argument("url")
    fetch_p.add_argument("--max-chars", type=int, default=4000,
                         help="cap on returned text (bounds what enters your context)")
    fetch_p.add_argument("--max-bytes", type=int, default=None,
                         help="cap on bytes DOWNLOADED before extraction (default 400000). "
                              "Distinct from --max-chars: too low and a large page is "
                              "truncated to its navigation header, returning chrome with no "
                              "error to say so.")
    fetch_p.add_argument("--timeout-s", type=float, default=8.0)

    browser_p = sub.add_parser(
        "browser",
        help="Render a JS-heavy page in a real browser and extract text "
        "(requires the separate 'agentteams[browser]' extra + 'playwright install chromium')",
    )
    browser_p.add_argument("url")
    browser_p.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (for a human operator watching locally). "
        "Defaults to headless; fails on a display-less server/CI runner if set.",
    )
    browser_p.add_argument(
        "--wait-until",
        choices=["load", "domcontentloaded", "networkidle"],
        default="networkidle",
        help="Navigation wait condition (default: networkidle). Switch to 'load' or "
        "'domcontentloaded' if a page never goes idle (long-polling/websockets).",
    )
    browser_p.add_argument("--max-chars", type=int, default=4000)
    browser_p.add_argument("--timeout-s", type=float, default=20.0)
    browser_p.add_argument(
        "--screenshot",
        metavar="PATH",
        help="Also save a full-page screenshot to PATH (additive — text extraction still runs).",
    )

    scholar_p = sub.add_parser(
        "scholar",
        help="Search scholarly indexes (OpenAlex, Crossref, arXiv) — key-free, returns DOIs",
    )
    scholar_p.add_argument("query")
    scholar_p.add_argument("-k", type=int, default=5, help="max works AFTER dedup")
    scholar_p.add_argument(
        "--sources",
        default=",".join(SOURCES),
        help=f"comma-separated subset of: {','.join(SOURCES)} (default: all)",
    )
    scholar_p.add_argument("--timeout-s", type=float, default=10.0)
    scholar_p.add_argument(
        "--citations",
        action="store_true",
        help="Also emit a formatted citation line per work (fields present in the record only "
             "— never inferred).",
    )

    args = parser.parse_args(argv)

    if args.command == "search":
        results, note, prov = web_search_with_provenance(
            args.query, k=args.k, timeout_s=args.timeout_s
        )
        json.dump([r.__dict__ for r in results], sys.stdout, indent=2)
        sys.stdout.write("\n")
        if note:
            # stderr, so the JSON on stdout stays machine-parseable. Without this an agent
            # reads `[]` as "no such information exists" and abandons an answerable question.
            print(f"note: {note}", file=sys.stderr)
        # Provenance is emitted unconditionally, not only on the unhappy path: the
        # external-retrieval quality gate requires a summary to be able to state HOW its
        # evidence was obtained, and an agent cannot report what it was never told.
        print(
            f"provenance: backend={prov.backend or 'none'} cached={str(prov.cached).lower()} "
            f"query_used={prov.query_used!r} tried={','.join(prov.backends_tried) or 'none'}",
            file=sys.stderr,
        )
        return 0

    if args.command == "scholar":
        chosen = tuple(s.strip() for s in args.sources.split(",") if s.strip())
        works = scholarly_search(
            args.query, k=args.k, sources=chosen, timeout_s=args.timeout_s
        )
        payload: list[dict[str, object]] = []
        for work in works:
            record: dict[str, object] = dict(work.__dict__)
            if args.citations:
                record["citation"] = format_citation(work)
            payload.append(record)
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        if not works:
            print(
                "note: no works returned. Unlike web search these APIs do not challenge "
                "requests, so this is either a genuine miss or an upstream outage — it is "
                "not evidence the work does not exist.",
                file=sys.stderr,
            )
        print(
            "note: a scholarly index hit is provenance, not endorsement. Retraction status "
            "is NOT checked.",
            file=sys.stderr,
        )
        return 0

    if args.command == "fetch":
        kwargs = {"max_chars": args.max_chars, "timeout_s": args.timeout_s}
        if args.max_bytes is not None:
            kwargs["max_bytes"] = args.max_bytes
        text = fetch_text(args.url, **kwargs)
        json.dump({"url": args.url, "text": text}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.command == "browser":
        # Local import: keeps `python -m agentteams.research search|fetch` free of any Playwright
        # dependency — only the `browser` subcommand ever touches agentteams.research.browser.
        from agentteams.research.browser import browser_fetch, browser_screenshot

        text = browser_fetch(
            args.url,
            headed=args.headed,
            wait_until=args.wait_until,
            timeout_s=args.timeout_s,
            max_chars=args.max_chars,
        )
        screenshot_path = None
        if args.screenshot:
            ok = browser_screenshot(
                args.url,
                args.screenshot,
                headed=args.headed,
                wait_until=args.wait_until,
                timeout_s=args.timeout_s,
            )
            screenshot_path = args.screenshot if ok else None
        json.dump(
            {"url": args.url, "text": text, "screenshot": screenshot_path},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
