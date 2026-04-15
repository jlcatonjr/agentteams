# `man` — AgentTeamsModule

Generate a POSIX groff man-page from the `agentteams` argparse parser.

The generated man-page source is committed to the repository root as `agentteams.1` and installed to `share/man/man1/` on pip install.

Regenerate after any CLI flag change:

```bash
python -m src.man > agentteams.1
```

Preview locally:

```bash
man ./agentteams.1
```

> *Source: `agentteams/man.py`*

---

## Functions

### `generate_man_page(parser)`

> *Source: `agentteams/man.py`*

Generate a groff man-page source document from an argparse parser.

Produces sections: `NAME`, `SYNOPSIS`, `DESCRIPTION`, `OPTIONS`, `EXIT STATUS`, and `EXAMPLES`. Derives all content from the parser's `prog`, `description`, and registered arguments — no duplication required.

**Args:**

- `parser` (`argparse.ArgumentParser`) — Configured ArgumentParser instance.

**Returns:** `str` — Complete groff man-page source. Write to `<name>.N` (e.g., `agentteams.1`).

---

## Module Entry Point

When run as a module (`python -m src.man`), imports `_build_parser` from `build_team` and writes the generated man-page to stdout. Used by the CI staleness gate:

```bash
python -m src.man | diff - agentteams.1
```

Exit code is non-zero if the committed `agentteams.1` is stale.
