"""Reconcile a deployed team's YAML front matter with its templates.

**Why this exists.** Front matter cannot be fenced. YAML must occupy the first bytes of the
file and a fence marker is an HTML comment, so there is nowhere to put one. The merge
therefore treats front matter with a three-way rule rather than as managed content:

* file **unmodified** since generation → the template's value is applied, with a notice;
* file **edited** → the on-disk value is kept, with a drift notice.

That second branch is deliberate — an edit may be a project's own choice, and reverting it
unattended would overwrite a decision the engine has no authority to make. But it means a
capability fix expressed as a ``tools:`` grant silently stops at every edited file, and the
only signal is a notice buried in a long ``--update --merge`` run that an operator has to
happen to be running.

**What this adds.** A standing, standalone answer to "where does this team's front matter
diverge from its templates?", and an apply path that acts only on explicit instruction. The
divergence stays visible without the merge silently escalating anyone's capabilities.

**Applying is privileged and is treated that way.** ``allowed-tools`` / ``tools`` is a
capability grant; C-3 makes widening one a privileged change. So the apply path is a separate
flag, never implied by the report, and it reports every key it changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentteams.front_matter_merge import CAPABILITY_FRONT_MATTER_KEYS
from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_front_matter

#: Keys that carry a capability grant. Named so a report can mark them, because a diverging
#: `allowed-tools` is a different kind of finding from a diverging `description`.
#:
#: Re-exported from :mod:`agentteams.front_matter_merge` rather than defined here. The two
#: modules previously carried DIFFERENT sets — this one listed `allowed-tools`, the merge
#: engine's did not — and the merge engine's was the one gating the escalation check. Keeping a
#: single definition is the fix (CH-05); the alias preserves every existing import.
CAPABILITY_KEYS: frozenset[str] = CAPABILITY_FRONT_MATTER_KEYS


@dataclass(frozen=True)
class Divergence:
    """One front-matter key whose deployed value differs from the template's."""

    rel_path: str
    key: str
    deployed: str
    template: str

    @property
    def is_capability(self) -> bool:
        return self.key in CAPABILITY_KEYS

    def describe(self) -> str:
        mark = "  [capability]" if self.is_capability else ""
        return (
            f"{self.rel_path}: {self.key}{mark}\n"
            f"    deployed: {self.deployed}\n"
            f"    template: {self.template}"
        )


#: Front-matter keys that name the same field across framework generations, newest first.
#: Mirrors ``front_matter_merge._KEY_SUCCESSION`` — kept as a separate constant only because this
#: module is the standalone/fleet path and must not import merge internals. Not restricted to
#: capability keys (the mechanism only renames): ``user-invocable`` (P3, 2026-08-15) is the
#: first non-capability entry.
_CAPABILITY_KEY_SUCCESSION: tuple[tuple[str, str], ...] = (
    ("tools", "allowed-tools"),
    ("user-invocable", "user-invokable"),
)


@dataclass(frozen=True)
class CapabilityKeyMigration:
    """One agent file whose capability key is written under a superseded name."""

    rel_path: str
    old_key: str
    new_key: str
    value: str
    migrated: bool

    def describe(self) -> str:
        verb = "migrated" if self.migrated else "would migrate"
        return f"{self.rel_path}: {verb} {self.old_key!r} -> {self.new_key!r} (value preserved)"


def migrate_capability_key(
    agents_dir: Path, *, dry_run: bool = True
) -> list[CapabilityKeyMigration]:
    """Rename a superseded capability key in every agent file under *agents_dir*.

    **Why a standalone pass exists at all.** Front matter is preserved verbatim by
    ``--update --merge``, so fixing the emitter reaches new teams only. Every Claude team
    generated before 2026-08-06 declares ``allowed-tools:`` — a key Claude Code's subagent
    schema does not define — and therefore grants every agent every tool no matter what the
    file's own body claims (audit finding W1/F-1). Those files need a migration, not a
    regeneration.

    Only the KEY is renamed. The value is carried across byte-for-byte: the template owns the
    key's name, the project owns its grant, and a rename must not become a back door for
    applying a template's capabilities.

    Args:
        agents_dir: A generated team's agents directory (e.g. ``<repo>/.claude/agents``).
        dry_run: When True (the default) nothing is written and the result describes what would
            change. **The default is deliberate**: this function's realistic caller operates on
            a repository other than its own, where Rule S-2 makes an unrequested write a
            clearance violation. Writing must be asked for.

    Returns:
        One entry per file needing (or receiving) migration. Empty when the tree is already
        migrated or carries no agent files.
    """
    results: list[CapabilityKeyMigration] = []
    if not agents_dir.is_dir():
        return results

    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        fm, rest = text[4:end + 1], text[end + 1:]

        # P3 (2026-08-15): iterate every succession entry per file, not just the first match.
        # A single `break` here was correct while the table had one entry — with two (tools/
        # allowed-tools and user-invocable/user-invokable), it silently migrated only whichever
        # entry matched first and skipped the other; worse, a second migration recomputed its
        # `new_fm` from the pre-migration `fm`, so writing it would have discarded the first
        # migration's change entirely. Caught by a test exercising a file needing both renames.
        fm_changed = False
        for new_key, old_key in _CAPABILITY_KEY_SUCCESSION:
            if re.search(rf"^{re.escape(new_key)}:", fm, re.MULTILINE):
                continue  # already migrated; never touch a file twice
            match = re.search(rf"^{re.escape(old_key)}:(.*)$", fm, re.MULTILINE)
            if not match:
                continue
            fm = fm[:match.start()] + f"{new_key}:{match.group(1)}" + fm[match.end():]
            fm_changed = True
            results.append(CapabilityKeyMigration(
                rel_path=path.name, old_key=old_key, new_key=new_key,
                value=match.group(1).strip(), migrated=not dry_run,
            ))
        if fm_changed and not dry_run:
            path.write_text("---\n" + fm + rest, encoding="utf-8")
    return results


def survey_capability_keys(agents_dir: Path) -> dict[str, int]:
    """Read-only census of which capability key a deployed team uses.

    Separate from :func:`migrate_capability_key` because a survey across other people's
    repositories must be provably incapable of writing. This function opens files for reading
    and returns counts; there is no code path here that writes.

    Returns:
        ``{"agents": n, "superseded": n, "current": n, "none": n}``.
    """
    counts = {"agents": 0, "superseded": 0, "current": 0, "none": 0}
    if not agents_dir.is_dir():
        return counts
    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---", 3)
        fm = text[4:end + 1] if end != -1 else ""
        counts["agents"] += 1
        if re.search(r"^tools:", fm, re.MULTILINE):
            counts["current"] += 1
        elif re.search(r"^allowed-tools:", fm, re.MULTILINE):
            counts["superseded"] += 1
        else:
            counts["none"] += 1
    return counts


def _front_matter_map(text: str) -> dict[str, str]:
    """Top-level ``key: value`` pairs of a file's front matter, or {} when it has none.

    Deliberately flat and textual. This reports and applies whole values; it does not attempt
    to parse or merge inside a value, because a partial capability grant is not a thing an
    operator can approve meaningfully.

    A block-style value (``key:`` alone on its line, with indented items below it — the shape
    ``agents:``/``handoffs:`` render in whenever the list is non-trivial) is captured in full
    rather than read as empty. Reading it as ``""`` was the root cause of issue #131 in the
    sibling ``front_matter_merge`` module, and this function had the same flaw: skipping every
    indented line meant ``find_divergences`` always saw ``deployed[key] == template[key] ==
    ""`` for a block key, so a real divergence in ``agents:``/``handoffs:`` was silently never
    reported by this reconcile tool either — a masking bug rather than a destructive one, since
    `apply_divergences` only rewrites a line it finds verbatim and a multi-line value never
    matches that search, so it fails safe (SKIPPED) rather than corrupting the file.

    Deliberately re-implements the same line-accumulation approach as
    ``front_matter_merge._front_matter_keys`` rather than importing it: that function is
    private, and this module is the standalone/fleet path that must not depend on merge
    internals (see ``_CAPABILITY_KEY_SUCCESSION`` above).
    """
    block, _body = _parse_front_matter(text)
    if not block:
        return {}
    out: dict[str, str] = {}
    current_key: str | None = None
    current_scalar = ""
    cont_lines: list[str] = []

    def _flush() -> None:
        if current_key is None:
            return
        is_block = any(raw_line.strip() for raw_line in cont_lines)
        out[current_key] = ("\n" + "\n".join(cont_lines)) if is_block else current_scalar.strip()

    for line in block.splitlines():
        is_top_level = line.strip() and not line.startswith((" ", "\t", "#")) and ":" in line
        if is_top_level:
            key, sep, value = line.partition(":")
            if sep:
                _flush()
                current_key, current_scalar, cont_lines = key.strip(), value, []
                continue
        if current_key is not None:
            cont_lines.append(line)
    _flush()
    return out


def find_divergences(
    rendered: list[tuple[str, str]],
    output_dir: Path,
) -> list[Divergence]:
    """Every front-matter key where the deployed file and the fresh render disagree.

    Only keys the template actually declares are compared. A key the deployed file has and the
    template does not is a project addition, not a divergence — the template is not entitled to
    an opinion about it, and reporting it would train operators to ignore the report.

    Args:
        rendered:   ``(rel_path, content)`` pairs from the render pipeline.
        output_dir: Root of the deployed agents directory.

    Returns:
        Divergences sorted by path then key. Empty when the team is reconciled.
    """
    found: list[Divergence] = []
    for rel_path, content in rendered:
        deployed_path = output_dir / rel_path
        if not deployed_path.is_file():
            continue
        template_fm = _front_matter_map(content)
        if not template_fm:
            continue
        deployed_fm = _front_matter_map(deployed_path.read_text(encoding="utf-8"))
        if not deployed_fm:
            continue  # cannot receive a block it never had; see the ledger row on that case
        for key, template_value in template_fm.items():
            deployed_value = deployed_fm.get(key)
            if deployed_value is not None and deployed_value != template_value:
                found.append(Divergence(rel_path, key, deployed_value, template_value))
    return sorted(found, key=lambda d: (d.rel_path, d.key))


def apply_divergences(divergences: list[Divergence], output_dir: Path) -> list[str]:
    """Rewrite each deployed file's front-matter key to the template's value.

    Only ever called on an explicit apply instruction. Rewrites the single ``key:`` line and
    nothing else — the body is untouched, and so is every key not in *divergences*.

    Returns:
        One line per applied change, for the caller to print. Capability keys are marked,
        because "which grants did this widen?" must be answerable from the output alone.
    """
    applied: list[str] = []
    by_file: dict[str, list[Divergence]] = {}
    for d in divergences:
        by_file.setdefault(d.rel_path, []).append(d)

    for rel_path, items in sorted(by_file.items()):
        path = output_dir / rel_path
        text = path.read_text(encoding="utf-8")
        block, _body = _parse_front_matter(text)
        if not block:
            continue
        new_text = text
        for d in items:
            old_line = f"{d.key}: {d.deployed}"
            new_line = f"{d.key}: {d.template}"
            if old_line not in new_text:
                applied.append(f"SKIPPED {rel_path}: {d.key} — line not found verbatim")
                continue
            new_text = new_text.replace(old_line, new_line, 1)
            applied.append(
                f"applied {rel_path}: {d.key}"
                + ("  [CAPABILITY GRANT CHANGED]" if d.is_capability else "")
            )
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
    return applied


def format_report(divergences: list[Divergence]) -> str:
    """Human-readable report. Says so explicitly when the team is already reconciled."""
    if not divergences:
        return "Front matter is reconciled with the templates: 0 divergences."
    caps = [d for d in divergences if d.is_capability]
    lines = [f"{len(divergences)} front-matter divergence(s) in {len({d.rel_path for d in divergences})} file(s):", ""]
    lines += [d.describe() for d in divergences]
    lines += ["", f"{len(caps)} of these are capability grants."]
    lines.append(
        "Nothing has been changed. Re-run with --reconcile-apply to take the template's "
        "values; a deployed value may be a deliberate project choice, so this is never "
        "applied automatically."
    )
    return "\n".join(lines)


def run_reconcile(args, rendered: list[tuple[str, str]], output_dir: Path) -> int:
    """CLI entry: report divergence, and apply only when explicitly told to.

    Lives here rather than inline in ``cli/generate.py`` because the policy — that reporting is
    never applying, and that a capability change is announced — is this module's, not the
    argument parser's. It also kept `generate.py` under the CH-07 ceiling, which the inline
    version had pushed it into the warning margin of.

    Returns:
        Process exit code. Always 0: divergence is a finding to read, not an error.
    """
    divergences = find_divergences(rendered, output_dir)
    print(format_report(divergences))
    if not (getattr(args, "reconcile_apply", False) and divergences):
        return 0
    if getattr(args, "dry_run", False):
        print("\n[DRY RUN] --reconcile-apply would change the keys listed above.")
        return 0
    for line in apply_divergences(divergences, output_dir):
        print(f"  {line}")
    return 0
