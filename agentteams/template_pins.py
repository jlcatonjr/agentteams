"""Consumer-side template pinning — a trust root outside the writable surface.

**The gap (security review §4.6).** Tier 1 of the declared authority hierarchy is
``agentteams/templates/``. ``drift.py`` compares template hashes against ``template_hashes``
recorded in the *target's own* ``build-log.json`` — that is change-since-last-build, not
authenticity. Nothing verifies the shipped templates at all. Anyone who can write to the
installed package edits the top of the hierarchy, and every downstream restore then propagates
their version with full ceremony. It is the one gap where the existing controls *amplify* a
compromise rather than merely failing to catch it.

**Why the obvious fixes are worth less than they look.** A checksum manifest shipped inside the
package, or a signature verified against a key pinned inside the package, both put the thing
being protected and the thing doing the protecting on the *same writable surface*. An attacker
with package write access rewrites the manifest or replaces the key. Those are integrity checks
against accident, and should be described that way rather than as authenticity.

**What this does instead.** The consuming repository records the template digests it trusts, in
a file **it commits to its own version control**. Generation compares the *installed* templates
against that record and reports loudly when they differ. The root of trust is the consumer's
git history — outside the surface an attacker with package write access controls.

**The mechanism is that this file is never written automatically.** ``--pin-templates`` is the
only thing that creates or updates it, and it is an explicit operator action. If a mismatch
could silently re-pin, the pin would record whatever it last saw and prove nothing. That is
the whole design; everything else here is bookkeeping.

**What it does not claim.** This is trust-on-first-use. It cannot tell a good first pin from a
compromised one — it detects *change since you pinned*. Pair it with install-time verification
(``pip install --require-hashes``, or attestations) if the wheel itself is in scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a consumer's pin lives, relative to the project root. Deliberately outside the agents
#: output directory: that directory is rewritten by every run, and a trust root that the tool
#: routinely rewrites is not a trust root.
PIN_REL_PATH = ".agentteams/template-pins.json"

PIN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class PinResult:
    """Outcome of comparing installed templates against the consumer's pin."""

    changed: dict[str, tuple[str, str]]   # template -> (pinned, installed)
    added: dict[str, str]                 # template -> installed digest, not in the pin
    removed: dict[str, str]               # template -> pinned digest, no longer installed
    pinned_at: str | None = None

    @property
    def is_clean(self) -> bool:
        return not (self.changed or self.removed)

    def format(self) -> str:
        """Report text. `changed` is the finding; `added` is usually just a new archetype."""
        if self.is_clean and not self.added:
            return "Template pin verified: every pinned template matches the installed copy."
        lines: list[str] = []
        if self.changed:
            lines.append(
                f"⚠  {len(self.changed)} pinned template(s) DIFFER from the installed package:"
            )
            for name, (pinned, installed) in sorted(self.changed.items()):
                lines.append(f"     {name}\n       pinned:    {pinned}\n       installed: {installed}")
        if self.removed:
            lines.append(f"⚠  {len(self.removed)} pinned template(s) are no longer installed:")
            lines += [f"     {n}" for n in sorted(self.removed)]
        if self.added:
            lines.append(
                f"   {len(self.added)} installed template(s) are not in the pin "
                "(normal after adding an archetype):"
            )
            lines += [f"     {n}" for n in sorted(self.added)]
        if self.changed or self.removed:
            lines.append("")
            lines.append(
                "If you did not expect this, do NOT re-pin. A pin that follows whatever it "
                "last saw records nothing. Establish why the installed templates changed — an "
                "upgrade explains it, an unchanged version does not — and re-run "
                "--pin-templates only once you have."
            )
        return "\n".join(lines)


def pin_path(project_root: Path) -> Path:
    return project_root / PIN_REL_PATH


def load_pin(project_root: Path) -> dict[str, Any] | None:
    """The consumer's committed pin, or None when they have not opted in.

    Absent is not a failure. Pinning is opt-in: a project that has not chosen a trust root
    should not be blocked, only unprotected.
    """
    path = pin_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def verify(project_root: Path, installed: dict[str, str]) -> PinResult | None:
    """Compare *installed* template digests against the consumer's pin.

    Args:
        project_root: Repository root holding ``.agentteams/template-pins.json``.
        installed:    ``{template rel path: digest}`` from ``render.compute_template_hashes``.

    Returns:
        A :class:`PinResult`, or None when the project has not pinned.
    """
    pin = load_pin(project_root)
    if pin is None:
        return None
    recorded: dict[str, str] = pin.get("template_hashes", {}) or {}

    changed = {
        name: (digest, installed[name])
        for name, digest in recorded.items()
        if name in installed and installed[name] != digest
    }
    removed = {name: d for name, d in recorded.items() if name not in installed}
    added = {name: d for name, d in installed.items() if name not in recorded}
    return PinResult(changed=changed, added=added, removed=removed, pinned_at=pin.get("pinned_at"))


def write_pin(project_root: Path, installed: dict[str, str], *, pinned_at: str) -> Path:
    """Record *installed* as the trusted set. **Only ever called from ``--pin-templates``.**

    Nothing else in the codebase may call this. The pin's entire value is that it does not
    follow the thing it is checking, so an automatic update — on mismatch, on upgrade, on any
    condition at all — would reduce it to a record of the last thing observed.

    Args:
        project_root: Repository root; ``.agentteams/`` is created if absent.
        installed:    Digests to trust, from ``render.compute_template_hashes``.
        pinned_at:    Caller-supplied timestamp (kept out of here so the module stays pure).

    Returns:
        The path written, for the caller to tell the operator to commit.
    """
    path = pin_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PIN_SCHEMA_VERSION,
        "pinned_at": pinned_at,
        "note": (
            "Template digests this project trusts. Commit this file: its value comes from "
            "living in your version control rather than in the installed package. Never "
            "regenerate it to silence a mismatch."
        ),
        "template_hashes": dict(sorted(installed.items())),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def consumer_root(description: dict[str, Any], output_dir: Path) -> Path:
    """Where the pin lives: the consuming project, never the generated output directory.

    ``resolve_output_dir`` returns ``project_root == output_dir`` whenever ``--output`` is
    given explicitly — which is the normal invocation. Deriving the pin location from it put
    the trust root inside ``.claude/agents/``, a directory this tool rewrites on every run.
    A trust root the tool routinely rewrites is not a trust root, and the module docstring
    says so; the code did the opposite until an end-to-end run showed the file landing there.

    Prefers the brief's declared project path, falls back to the working directory, and refuses
    either if it sits inside *output_dir*.
    """
    import os

    candidates = [description.get("existing_project_path"), os.getcwd()]
    out = output_dir.resolve()
    for cand in candidates:
        if not cand:
            continue
        root = Path(cand).resolve()
        if root != out and out not in root.parents and root not in (out, *out.parents[:0]):
            if not str(root).startswith(str(out) + os.sep):
                return root
    return out.parent


def run_pinning(
    args: Any,
    manifest: dict[str, Any],
    description: dict[str, Any],
    output_dir: Path,
    templates_dir: Path,
) -> int | None:
    """CLI entry: write the pin on ``--pin-templates``, otherwise verify against it.

    Lives here rather than in ``cli/generate.py`` for the same reason the writer does — the
    policy that verification never re-pins is this module's, and inlining it put `generate.py`
    over the CH-07 ceiling.

    A mismatch **reports and does not block**. The review's threat is the silent propagation of
    an altered template, which a loud report defeats; refusing to generate would also punish the
    ordinary case of an intentional package upgrade.

    Returns:
        An exit code when ``--pin-templates`` ran, else None so the pipeline continues.
    """
    import sys
    from datetime import datetime, timezone

    from agentteams import render as _render

    installed = _render.compute_template_hashes(manifest, templates_dir=templates_dir)
    project_root = consumer_root(description, output_dir)

    if getattr(args, "pin_templates", False):
        written = write_pin(
            project_root, installed,
            pinned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        print(f"Pinned {len(installed)} template digest(s) to {written}")
        print("Commit this file — its value comes from living in your version control.")
        return 0

    result = verify(project_root, installed)
    if result is not None and not (result.is_clean and not result.added):
        print(result.format(), file=sys.stderr)
    return None
