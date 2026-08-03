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

from dataclasses import dataclass
from pathlib import Path

from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_front_matter

#: Keys that carry a capability grant. Named so a report can mark them, because a diverging
#: `allowed-tools` is a different kind of finding from a diverging `description`.
CAPABILITY_KEYS: frozenset[str] = frozenset({"tools", "allowed-tools", "capabilities"})


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


def _front_matter_map(text: str) -> dict[str, str]:
    """Top-level ``key: value`` pairs of a file's front matter, or {} when it has none.

    Deliberately flat and textual. This reports and applies whole values; it does not attempt
    to parse or merge inside a value, because a partial capability grant is not a thing an
    operator can approve meaningfully.
    """
    block, _body = _parse_front_matter(text)
    if not block:
        return {}
    out: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
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
