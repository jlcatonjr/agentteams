# `living_doc` — Living-Document Conformance

> *Source: `agentteams/living_doc.py`*

Constitutional Rule 7 says agent documentation "must not accumulate stale content."
This module finds the one violation of that rule which is unambiguously detectable:
**dated content in unfenced agent prose** — snapshots, archaeology and fix logs that
record a moment rather than a standing instruction.

Carved out of `audit.py` to hold that module under the CH-07 line ceiling.

## Why the scope is *unfenced* prose only

A date inside an `AGENTTEAMS:*` fence is module-generated and expected — build
timestamps, threat-intelligence snapshots, work-summary tables. A date in prose the
operator wrote is the thing Rule 7 forbids.

That distinction is what makes the check worth having. Scanning **all** agent prose
produced 65 signals at roughly 1.5% precision; restricting to unfenced prose produced
1 signal at 100%. The gate for shipping this check was that a hit means something, and
only the narrow scope cleared it.

## API

### `unfenced_spans(text) -> list[tuple[int, int]]`

Return the regions of *text* that lie **outside** every `AGENTTEAMS:BEGIN … END`
fence.

**Args:**

- `text` (`str`) — Full document text.

**Returns:** `list[tuple[int, int]]` — `(start, end)` character-offset spans, in document order.

### `find_dated_prose(file_map) -> list[tuple[str, str, str]]`

Find dated content in unfenced prose across a set of documents. Scoped to paths
ending in `.agent.md`; paths containing `references/` or `.reference.` are
excluded even when they end in `.agent.md`.

**Args:**

- `file_map` (`dict[str, str]`) — `{path: content}` for the agent files to scan.

**Returns:** `list[tuple[str, str, str]]` — `(path, matched_date, containing_line)` per
finding.

## Constants

- `FENCE_RE` — Matches `AGENTTEAMS:BEGIN`/`END` markers, used to compute the unfenced
  complement.
- `DATE_RE` — Matches the date forms that appear in practice (ISO `2026-07-29`).

## Integration

`audit.py` registers each finding as a `WARNING` with code `LIVING-DOC`, whose message
names the policy and directs the fix — *move it to a reference file* — rather than
merely reporting the match. Findings are advisory: a warning never fails a build.

Exercised by `tests/test_living_doc_and_cycles.py`.
