"""rules_projection.py — one project's rules in every provider's entry file (baseAgent P1, P2, P8–P10).

An instance runs on several providers, and each loads its own entry file. An operator who records a standing rule
in one provider's ``## Project-Specific Rules`` region binds that provider only, and nothing reports the gap: in
baseAgent every standing decision lived in ``.claude/CLAUDE.md`` alone. This module takes one source region and
keeps a generated copy of it in every other enabled provider's entry file:

* Copilot: ``.github/copilot-instructions.md``;
* Claude: ``.claude/CLAUDE.md``, else ``CLAUDE.md`` (Claude Code loads both, so the source being either covers it);
* Codex: ``AGENTS.override.md`` when it exists, since Codex loads it *in place of* ``AGENTS.md``, else
  ``AGENTS.md``. Codex reads at most 32 KiB of project instructions, so when a copy would take the file past that,
  Codex gets a binding reference to the source and the rules' table of contents instead (``mode=reference``);
* Goose: ``.goosehints``, which Goose always loads. Freezing Codex freezes the shared ``AGENTS.md``; ``.goosehints``
  is Goose's own, so Goose still gets new rules.

A surface in the brief's ``frozen_surfaces`` is skipped (``AGENTS.override.md`` counts as Codex's). The source never
moves silently: ``project_rules_source`` must be one of :data:`SOURCE_CANDIDATES`, and without it a second file with
rules of its own, or copies recording another source, is refused (:func:`find_source`). Every change to the rules is
printed as a diff when it spreads.

The copy sits inside the target's own Rules region between ``PROJECTED-RULES`` markers, which are not
``AGENTTEAMS`` markers, so the region still carries across ``--overwrite`` and the target's own rules beside the
block are kept. The BEGIN marker records the source's hash and the block's own hash: a block whose text no longer
matches its recorded hash was edited by hand, and is refused rather than overwritten. A target with more than one
Rules region is refused too.

:func:`sync_pointers` keeps a short pointer to the rules in the Project-Specific Notes of ``@security`` and
``@orchestrator`` on each Markdown and Goose surface (P10: a delegated agent may not load its provider's entry
file). A Codex TOML agent is reported, not edited.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.user_regions import HEADINGS, _next_heading, find_region, reindent, skip_banner

_NOTES_HEADING, _RULES_HEADING = HEADINGS
#: Where the source is looked for, in order, when the brief names none.
SOURCE_CANDIDATES: tuple[str, ...] = (".claude/CLAUDE.md", "CLAUDE.md", ".github/copilot-instructions.md", "AGENTS.md")
#: Codex's combined project-instructions budget (``project_doc_max_bytes``).
CODEX_BUDGET_BYTES = 32 * 1024
#: Agents whose notes get a pointer to the rules (P10).
POINTER_AGENTS: tuple[str, ...] = ("security", "orchestrator")

_BLOCK_RE = {
    kind: re.compile(
        rf"^(?P<indent>[ \t]*)<!-- {kind}:BEGIN source=(?P<src>\S+) source-sha256=(?P<ssha>[0-9a-f]{{64}}) "
        rf"copy-sha256=(?P<csha>[0-9a-f]{{64}})(?: mode=(?P<mode>copy|reference))? -->[ \t]*\n"
        rf"(?P<body>.*?)^(?P=indent)<!-- {kind}:END -->[ \t]*\n?",
        re.MULTILINE | re.DOTALL)
    for kind in ("PROJECTED-RULES", "PROJECT-RULES-POINTER")
}
_RULES_HEADING_RE = re.compile(rf"^[ \t]*{re.escape(_RULES_HEADING)}[ \t]*$", re.MULTILINE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Outcome:
    """What a projection did, or would do, to one file."""

    path: str
    status: str  # current | updated | added | refused | skipped
    detail: str = ""
    diff: str = ""  # the rules' change, for an updated copy


@dataclass
class Report:
    """A projection run: the source and one outcome per target."""

    source: str | None
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def drift(self) -> list[Outcome]:
        """Targets that are not current (what ``--check`` fails on)."""
        return [o for o in self.outcomes if o.status in ("updated", "added", "refused")]


def _strip_blocks(region: str) -> str:
    for pattern in _BLOCK_RE.values():
        region = pattern.sub("", region)
    return region


def rules_body(text: str) -> str | None:
    """The operator's own rules in *text*'s Rules region: the region without its heading, the generated
    USER-EDITABLE note, and any projected block. ``None`` when there are none.

    Args:
        text: An instruction file.

    Returns:
        The rules text (ending in a newline), or ``None``.

    Raises:
        Nothing.
    """
    span = find_region(text, _RULES_HEADING)
    if span is None:
        return None
    start, end, indent = span
    following = _next_heading(text, start + 1, indent + "## ")  # a later section is not part of the rules
    if following is not None:
        m = re.search(rf"^{re.escape(following)}[ \t]*$", text[start:end], re.MULTILINE)
        if m:
            end = start + m.start()
    lines = _strip_blocks(text[start:end]).splitlines(keepends=True)[1:]
    body = "".join(lines[skip_banner(lines):]).strip("\n")
    return body + "\n" if body.strip() else None


def _recorded_sources(root: Path) -> set[str]:
    """The sources the existing copies record (their BEGIN markers' ``source=``)."""
    found: set[str] = set()
    for rel in (*SOURCE_CANDIDATES, "AGENTS.override.md", ".goosehints"):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            text = path.read_text(encoding="utf-8")
            found.update(m.group("src") for m in _BLOCK_RE["PROJECTED-RULES"].finditer(text))
    return found


def find_source(root: Path, configured: str | None = None) -> tuple[str, str] | None:
    """The source file and its rules.

    *configured* (the brief's ``project_rules_source``) must be one of :data:`SOURCE_CANDIDATES`. Without it the
    source is the one the existing copies record, else the only candidate whose Rules region holds rules of its own.
    The source never moves silently (adversarial review, 2026-11-02): an agent on any surface can write rules into
    its own entry file, so a file that would *become* the source — copies recording another one, or several
    candidates holding rules — is refused until the brief names it.

    Args:
        root: The project root.
        configured: A project-relative path, or ``None``.

    Returns:
        ``(relative path, rules)``, or ``None`` when no candidate holds rules.

    Raises:
        ValueError: *configured* is not a candidate; the source is ambiguous or would move; the source has
            CRLF line endings or contains projection markers.
        OSError, UnicodeDecodeError: A candidate can't be read.
    """
    if configured is not None and configured not in SOURCE_CANDIDATES:
        raise ValueError(f"project_rules_source must be one of {', '.join(SOURCE_CANDIDATES)}, not {configured!r}")
    own: dict[str, str] = {}
    for rel in ([configured] if configured else list(SOURCE_CANDIDATES)):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            body = rules_body(path.read_text(encoding="utf-8"))
            if body is not None:
                own[rel] = body
    if configured:
        source = configured if configured in own else None
    else:
        recorded = _recorded_sources(root)
        if len(recorded) > 1:
            raise ValueError(f"the copies record different sources ({', '.join(sorted(recorded))}); set "
                             "project_rules_source in the brief")
        if recorded:
            (source,) = recorded
            if source not in own:
                raise ValueError(f"the copies record {source} as the source, but it holds no rules now; set "
                                 "project_rules_source in the brief to the file that should")
        elif len(own) > 1:
            raise ValueError(f"{', '.join(own)} each hold rules of their own; set project_rules_source in the "
                             "brief to the one every provider should get")
        else:
            source = next(iter(own), None)
    if source is None:
        return None
    raw = (root / source).read_bytes()
    if b"\r\n" in raw:
        raise ValueError(f"{source} has CRLF line endings; convert it to LF before projecting its rules")
    body = own[source]
    if re.search(r"<!-- (?:PROJECTED-RULES|PROJECT-RULES-POINTER):", body):
        raise ValueError(f"{source}'s rules contain a projection marker line; remove it")
    return source, body


def _frozen_paths(frozen: object) -> tuple[str, ...]:
    from agentteams.cli.frozen_surfaces import frozen_owned_paths

    paths = frozen_owned_paths(frozen)
    return paths + (("AGENTS.override.md",) if "AGENTS.md" in paths and "codex" in (frozen or []) else ())


def targets(root: Path, source: str, frozen: object = None) -> list[tuple[str, str]]:
    """The entry files to project into: ``(relative path, provider)`` per enabled provider, the source excluded.

    A provider is enabled when its agents directory or entry file exists. Frozen surfaces are left out.

    Args:
        root: The project root.
        source: The source's relative path.
        frozen: The brief's ``frozen_surfaces``.

    Returns:
        The targets, in a stable order.

    Raises:
        ValueError: *frozen* is not a list of known surface ids.
    """
    from agentteams.cli.frozen_surfaces import touches_frozen

    def exists(rel: str) -> bool:
        return (root / rel).exists()

    found: list[tuple[str, str]] = []
    if exists(".github/copilot-instructions.md"):
        found.append((".github/copilot-instructions.md", "copilot"))
    if source not in ("CLAUDE.md", ".claude/CLAUDE.md"):
        claude = next((rel for rel in (".claude/CLAUDE.md", "CLAUDE.md") if exists(rel)), None)
        if claude:
            found.append((claude, "claude"))
    codex = "AGENTS.override.md" if exists("AGENTS.override.md") else "AGENTS.md"
    if exists(codex) and (exists(".codex") or exists("AGENTS.md")):
        found.append((codex, "codex"))
    if exists(".goose") or exists(".goosehints"):
        found.append((".goosehints", "goose"))
    frozen_paths = _frozen_paths(frozen)
    return [(rel, who) for rel, who in found if rel != source and not touches_frozen(rel, frozen_paths)]


def table_of_contents(body: str) -> list[str]:
    """The rules' titles: their headings, else the bold lead of each top-level list item, else the item itself.

    Args:
        body: The rules text.

    Returns:
        The titles, in order, each at most 100 characters, with comment markers and backticks removed.

    Raises:
        Nothing.
    """
    titles = re.findall(r"^#{3,6}[ \t]+(.+?)[ \t]*$", body, re.MULTILINE)
    if not titles:
        titles = re.findall(r"^[-*][ \t]+\*\*(.+?)\*\*", body, re.MULTILINE)
    if not titles:
        titles = re.findall(r"^[-*][ \t]+(.+?)[ \t]*$", body, re.MULTILINE)
    # A title is a label, not an instruction: comment markers and backticks out, capped.
    clean = (re.sub(r"<!--|-->|`", "", t).strip() for t in titles)
    return [t if len(t) <= 100 else t[:99] + "…" for t in clean if t]


def _block(kind: str, source: str, source_sha: str, content: str, mode: str | None, indent: str = "") -> str:
    mode_attr = f" mode={mode}" if mode else ""
    begin = (f"<!-- {kind}:BEGIN source={source} source-sha256={source_sha} copy-sha256={_sha(content)}"
             f"{mode_attr} -->\n")
    raw = begin + content + f"<!-- {kind}:END -->\n"
    return "".join((indent + ln) if ln.strip() else ln for ln in raw.splitlines(keepends=True))


def projection_content(source: str, body: str, mode: str) -> str:
    """What the block holds: the rules verbatim (``copy``), or a binding reference with their titles (``reference``).

    Args:
        source: The source's relative path.
        body: The rules.
        mode: ``copy`` or ``reference``.

    Returns:
        The block's content.

    Raises:
        Nothing.
    """
    note = (f"> **Projected from `{source}`** (`## Project-Specific Rules`) by agentteams. Edit the rules there; "
            "this block is regenerated on `--update` and by `--sync-project-rules`. Agents missing on this provider "
            "are listed in `references/agent-surface-matrix.md`.\n\n")
    if mode == "copy":
        return note + body
    titles, kept = "", 0
    all_titles = table_of_contents(body)
    for title in all_titles:  # a reference must itself stay well inside the budget
        if len((titles + title).encode("utf-8")) > CODEX_BUDGET_BYTES // 2:
            titles += f"- … and {len(all_titles) - kept} more (see the source)\n"
            break
        titles += f"- {title}\n"
        kept += 1
    return (note + f"The project's operator rules are in `{source}`, under `## Project-Specific Rules`. They bind this "
            "session as written there: read that section before acting. This is a reference, not a copy, because a "
            "copy would exceed Codex's 32 KiB project-instructions budget.\n\n" + (f"Rules:\n\n{titles}" if titles
                                                                                  else ""))


def _place(text: str, kind: str, block: str, *, heading: str, add_region: bool) -> tuple[str, str, str]:
    """Put *block* into *text*'s *heading* region: ``(new text, status, detail)``.

    With *add_region*, a file without the region gets one at its end; otherwise that is refused.
    """
    hits = [m for m in re.finditer(rf"^[ \t]*{re.escape(heading)}[ \t]*$", text, re.MULTILINE)]
    if len(hits) > 1:
        return text, "refused", f"it has {len(hits)} '{heading}' regions; keep one"
    pattern = _BLOCK_RE[kind]
    span = find_region(text, heading)
    if span is None:
        if not add_region:
            return text, "refused", f"it has no '{heading}' region"
        head = text.rstrip("\n") + "\n\n" if text.strip() else ""
        return head + f"{heading}\n\n" + block, "added", "region and block added"
    start, end, _ = span
    region = text[start:end]
    existing = list(pattern.finditer(region))
    if len(existing) > 1:
        return text, "refused", f"its '{heading}' region has {len(existing)} {kind} blocks; keep one"
    if existing:
        m = existing[0]
        content = reindent(m.group("body"), m.group("indent"), "") or ""
        if _sha(content) != m.group("csha"):
            return text, "refused", "the projected block was edited by hand; move the edit to the source, then delete the block"
        if m.group(0).rstrip("\n") == block.rstrip("\n"):
            return text, "current", ""
        return text[:start] + region[:m.start()] + block + region[m.end():] + text[end:], "updated", ""
    lines = region.splitlines(keepends=True)
    i = skip_banner(lines, 1)
    head = "".join(lines[:i]).rstrip("\n") + "\n\n"
    rest = "".join(lines[i:]).lstrip("\n")
    return text[:start] + head + block + ("\n" + rest if rest else "") + text[end:], "added", ""


def project(root: Path, *, configured: str | None = None, frozen: object = None, write: bool = True) -> Report:
    """Project the source's rules into every target (:func:`targets`).

    Args:
        root: The project root.
        configured: The brief's ``project_rules_source``.
        frozen: The brief's ``frozen_surfaces``.
        write: Write the changes; ``False`` reports what would change (``--check``, ``--dry-run``).

    Returns:
        The report. ``source`` is ``None`` when no instruction file holds rules (nothing to project). An updated
        copy's outcome carries the rules' change as a unified diff, so what spreads to every provider is shown.

    Raises:
        ValueError: *frozen* is not a list of known surface ids, or :func:`find_source` refuses the source.
        OSError, UnicodeDecodeError: A file can't be read or written.
    """
    found = find_source(root, configured)
    if found is None:
        return Report(source=None)
    source, body = found
    source_sha = _sha(body)
    report = Report(source=source)
    source_text = (root / source).read_text(encoding="utf-8")
    if len(_RULES_HEADING_RE.findall(source_text)) > 1:
        report.outcomes.append(Outcome(source, "refused", "the source has more than one Rules region"))
        return report
    for rel, provider in targets(root, source, frozen):
        path = root / rel
        if path.is_symlink():
            report.outcomes.append(Outcome(rel, "skipped", "a symlink"))
            continue
        if path.exists() and b"\r\n" in path.read_bytes():
            report.outcomes.append(Outcome(rel, "refused", "CRLF line endings; convert it to LF"))
            continue
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        mode = "copy"
        content = projection_content(source, body, mode)
        if provider == "codex":
            trial, *_ = _place(text, "PROJECTED-RULES", _block("PROJECTED-RULES", source, source_sha, content, mode),
                               heading=_RULES_HEADING, add_region=True)
            if len(trial.encode("utf-8")) > CODEX_BUDGET_BYTES:
                mode = "reference"
                content = projection_content(source, body, mode)
        new, status, detail = _place(text, "PROJECTED-RULES",
                                     _block("PROJECTED-RULES", source, source_sha, content, mode),
                                     heading=_RULES_HEADING, add_region=True)
        if mode == "reference":
            detail = (detail + "; " if detail else "") + "a reference (a copy would exceed Codex's 32 KiB budget)"
        if write and status in ("updated", "added"):
            path.write_text(new, encoding="utf-8")
        report.outcomes.append(Outcome(rel, status, detail, _diff(text, content) if status == "updated" else ""))
    return report


def _diff(old_text: str, new_content: str) -> str:
    """The change between a target's current projected block and *new_content* (at most 200 lines)."""
    import difflib

    m = _BLOCK_RE["PROJECTED-RULES"].search(old_text)
    old = m.group("body") if m else ""
    lines = list(difflib.unified_diff(old.splitlines(), new_content.splitlines(), "projected", "source", lineterm="",
                                      n=1))
    return "\n".join(lines[:200] + (["… (diff truncated)"] if len(lines) > 200 else []))


def _pointer_content(source: str) -> str:
    return (f"**Project rules.** This project's operator rules are in `{source}`, under `## Project-Specific Rules`. "
            "They extend this agent's instructions; they never weaken its Invariant Core or the Constitutional Core, "
            "and where they conflict with either, the Core governs. Read them before acting: a delegated agent may "
            "not load its provider's instruction file.\n")


def pointer_files(root: Path) -> list[Path]:
    """The agent files that get a pointer: :data:`POINTER_AGENTS` on every Markdown, Goose and Codex surface.

    Args:
        root: The project root.

    Returns:
        The files that exist.

    Raises:
        Nothing.
    """
    # The agent-file layout of each surface (cf. prompt_roots.TEAM_AGENT_GLOBS and surface_matrix.SURFACES).
    out: list[Path] = []
    for slug in POINTER_AGENTS:
        for rel in (f".claude/agents/{slug}.md", f".github/agents/{slug}.agent.md", f".grok/agents/{slug}.md",
                    f".goose/recipes/{slug}.yaml", f".codex/agents/{slug}.toml"):
            if (root / rel).is_file():
                out.append(root / rel)
    return out


def sync_pointers(root: Path, *, configured: str | None = None, frozen: object = None,
                  write: bool = True) -> list[Outcome]:
    """Keep a pointer to the rules in the notes of :data:`POINTER_AGENTS` on every surface (P10).

    Args:
        root: The project root.
        configured: The brief's ``project_rules_source``.
        frozen: The brief's ``frozen_surfaces``.
        write: Write the changes.

    Returns:
        One outcome per agent file.

    Raises:
        ValueError: *frozen* is not a list of known surface ids, or :func:`find_source` refuses the source.
        OSError, UnicodeDecodeError: A file can't be read or written.
    """
    from agentteams.cli.frozen_surfaces import touches_frozen

    found = find_source(root, configured)
    if found is None:
        return []
    source, _body = found
    frozen_paths = _frozen_paths(frozen)
    content = _pointer_content(source)
    outcomes: list[Outcome] = []
    for path in pointer_files(root):
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".toml":
            outcomes.append(Outcome(rel, "skipped", "a Codex TOML agent; add the pointer to its notes by hand"))
            continue
        if touches_frozen(rel, frozen_paths) or path.is_symlink():
            outcomes.append(Outcome(rel, "skipped", "frozen" if not path.is_symlink() else "a symlink"))
            continue
        text = path.read_text(encoding="utf-8")
        span = find_region(text, _NOTES_HEADING)
        indent = span[2] if span else ""
        # Keyed to the source's path, not its text: a rule edit doesn't rewrite every agent file.
        block = _block("PROJECT-RULES-POINTER", source, _sha(source), content, None, indent)
        new, status, detail = _place(text, "PROJECT-RULES-POINTER", block, heading=_NOTES_HEADING, add_region=False)
        if write and status in ("updated", "added"):
            path.write_text(new, encoding="utf-8")
        outcomes.append(Outcome(rel, status, detail))
    return outcomes


__all__ = ["CODEX_BUDGET_BYTES", "POINTER_AGENTS", "SOURCE_CANDIDATES", "Outcome", "Report", "find_source",
           "pointer_files", "project", "projection_content", "rules_body", "sync_pointers", "table_of_contents",
           "targets"]
