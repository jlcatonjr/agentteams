"""front_matter_baseline.py — what agentteams authored in each file's front matter, and a guard for handoffs.

**The defect (portal, 2026-10-11 fleet audit).** ``--update --merge`` silently replaced hand-written
``handoffs:`` blocks with the template's: the orchestrator lost its Portal Frontend and Pricing Subsystem
handoffs, and three other agents lost theirs.

**Root cause.** The three-way front-matter merge (:func:`agentteams.front_matter_merge._merge_front_matter`)
applies the template's value when the on-disk value equals the *baseline*, reading that as "nobody has
touched it since generation". But ``build-log.json`` recorded the baseline as the front matter **as
written**, and a merge writes the project's own value whenever it keeps it (template unchanged, or a
conflict). So one run after a project edits a key, the baseline holds the project's value; the disk equals
it; the template differs from it; and the merge "applies the template to an untouched key". A kept conflict
was protected for exactly one run. portal's build log recorded its hand-written handoffs as the baseline.

**Fix, three parts.**

1. :func:`compute` records, per key, the value agentteams *authored*: the written value when it equals the
   template's render, else the previous trusted baseline's value, else nothing (unknown, which keeps the
   project's value). The log marks this with ``front_matter_baseline_version: 2``.
2. :func:`load` reads a legacy (unversioned, as-written) baseline without its block-valued keys
   (``handoffs:``) and capability keys (``agents:``, ``tools:`` ...), where projects author entries and a
   polluted value loses them. Scalar metadata keys keep the old trust for the one transition run; nothing
   legacy is carried forward into a version-2 baseline.
3. :func:`restore_dropped_handoffs` is the last line of defence, whatever path produced the content (a
   whole-body migration that took the render's front matter, an interop re-emit): an on-disk handoff the
   content about to be written lacks is restored, unless the trusted baseline shows agentteams authored it
   (then the template retired it, and that is said). A restore is announced with
   :data:`HANDOFF_KEPT_PREFIX`, which ``fleet`` turns into REVIEW.

Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from agentteams.front_matter_merge import (
    CAPABILITY_FRONT_MATTER_KEYS,
    _front_matter_keys,
    _render_front_matter,
)

#: The build-log key that marks a baseline recorded by :func:`compute`.
VERSION_KEY = "front_matter_baseline_version"
#: The current baseline version (1, implicit, was "front matter as written").
BASELINE_VERSION = 2
#: Notice prefix for restored handoffs. ``fleet`` files these as REVIEW.
HANDOFF_KEPT_PREFIX = "front matter: kept hand-written handoff"

_ITEM_RE = re.compile(r"^([ \t]*)-[ \t]")
_FIELD_RE = re.compile(r"^[ \t]*-?[ \t]*(label|agent):[ \t]*(.*?)[ \t]*$")

Baseline = dict[str, dict[str, str]]


def load(log: Mapping) -> Baseline:
    """The trusted front-matter baseline from a parsed ``build-log.json``.

    Args:
        log: The parsed build log (may be empty).

    Returns:
        ``{rel_path: {key: value}}``. A version-2 baseline is returned whole. A legacy one loses its
        block-valued and capability keys (see the module docstring); an absent one is ``{}`` (unknown).

    Raises:
        Nothing.
    """
    raw = log.get("front_matter_baseline") if isinstance(log, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    if log.get(VERSION_KEY) == BASELINE_VERSION:
        return {rel: dict(keys) for rel, keys in raw.items() if isinstance(keys, Mapping)}
    return {
        rel: {k: v for k, v in keys.items()
              if isinstance(v, str) and not v.startswith("\n") and k not in CAPABILITY_FRONT_MATTER_KEYS}
        for rel, keys in raw.items() if isinstance(keys, Mapping)
    }


def compute(written: Mapping[str, Mapping[str, str]], template: Mapping[str, Mapping[str, str]],
            previous: Mapping[str, Mapping[str, str]] | None) -> Baseline:
    """Record, per file and key, the value agentteams authored.

    Args:
        written: Each emitted file's front matter as written this run.
        template: Each file's front matter in this run's template render.
        previous: The previous run's baseline, only when it was version 2 (else None: legacy values are
            never carried forward).

    Returns:
        ``{rel_path: {key: value}}``. A key is recorded when its written value equals the template's, or
        when the previous baseline has it; otherwise it is left out, so the next merge treats it as
        possibly edited and keeps the project's value. A file with nothing recorded is omitted.

    Raises:
        Nothing.
    """
    out: Baseline = {}
    for rel, keys in written.items():
        fresh = template.get(rel, {})
        prior = (previous or {}).get(rel, {})
        kept = {}
        for key, value in keys.items():
            if fresh.get(key) == value:
                kept[key] = value
            elif key in prior:
                kept[key] = prior[key]
        if kept:
            out[rel] = kept
    return out


def handoff_items(value: str | None) -> list[tuple[tuple[str, str], list[str]]]:
    """Split a block-style ``handoffs:`` value into items.

    Args:
        value: The raw value from :func:`agentteams.front_matter_merge._front_matter_keys` (a block value
            starts with ``\\n``).

    Returns:
        ``[((agent, label), item lines), ...]`` in order; empty for an inline or empty value.

    Raises:
        Nothing.
    """
    if not value or not value.startswith("\n"):
        return []
    lines = value.split("\n")[1:]
    starts = [i for i, line in enumerate(lines) if _ITEM_RE.match(line)]
    if not starts:
        return []
    indent = min(len(_ITEM_RE.match(lines[i]).group(1)) for i in starts)
    starts = [i for i in starts if len(_ITEM_RE.match(lines[i]).group(1)) == indent]
    items = []
    for n, start in enumerate(starts):
        chunk = lines[start:starts[n + 1] if n + 1 < len(starts) else len(lines)]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        fields = {m.group(1): m.group(2).strip("\"'") for line in chunk if (m := _FIELD_RE.match(line))}
        items.append(((fields.get("agent", ""), fields.get("label", "")), chunk))
    return items


def restore_dropped_handoffs(new_content: str, existing_content: str,
                             baseline: Mapping[str, str] | None) -> tuple[str, list[str]]:
    """Never let a write drop a handoff agentteams did not author.

    Args:
        new_content: The content about to be written.
        existing_content: The current on-disk file.
        baseline: This file's trusted baseline (from :func:`load`), or None.

    Returns:
        ``(content, notices)``. *content* is *new_content* with every dropped, non-template handoff
        appended to its ``handoffs:`` block; unchanged when nothing was dropped.

    Raises:
        Nothing.
    """
    disk = handoff_items(_front_matter_keys(existing_content).get("handoffs"))
    if not disk:
        return new_content, []
    new_keys = _front_matter_keys(new_content)
    if not new_keys:
        return new_content, []
    current = new_keys.get("handoffs")
    new_items = handoff_items(current)
    present_agents = {agent for (agent, _), _ in new_items}
    present = {key for key, _ in new_items}
    base_items = handoff_items((baseline or {}).get("handoffs"))
    authored = {agent for (agent, _), _ in base_items}
    authored_keys = {key for key, _ in base_items}
    # Identity is the target agent: a handoff to an agent the new block still reaches is not lost, only
    # reworded (named below); restoring it too would give the agent two handoffs to the same place.
    dropped = [(key, lines) for key, lines in disk if key[0] not in present_agents]
    reworded = sorted({key for key, _ in disk
                       if key not in present and key[0] in present_agents and key not in authored_keys})
    restore = [(key, lines) for key, lines in dropped if key[0] not in authored]
    retired = [key for key, _ in dropped if key[0] in authored]
    notices = []
    if retired:
        notices.append("front matter: removed template handoff(s) the template retired: "
                       + ", ".join(f"{label!r} -> {agent}" for agent, label in retired))
    if reworded:
        notices.append(f"{HANDOFF_KEPT_PREFIX} wording NOT kept for "
                       + ", ".join(f"{label!r} -> {agent}" for agent, label in reworded)
                       + ": the new block still hands off to each agent, with the template's label and prompt. "
                         "The old wording is in the backup; restore it by hand if it was the project's.")
    if not restore:
        return new_content, notices
    names = ", ".join(f"{label!r} -> {agent}" for (agent, label), _ in restore)
    if current is not None and current.strip() not in ("", "[]") and not current.startswith("\n"):
        notices.append(f"{HANDOFF_KEPT_PREFIX}(s) NOT restored: {names} — the new `handoffs:` is not a block "
                       "list, so they could not be added back. They are in the backup; restore them by hand.")
        return new_content, notices
    block = current if current is not None and current.startswith("\n") else ""
    new_keys["handoffs"] = block.rstrip("\n") + "".join("\n" + "\n".join(lines) for _, lines in restore)
    notices.append(f"{HANDOFF_KEPT_PREFIX}(s) the update would have dropped: {names} — the template does not "
                   "carry them and nothing records agentteams writing them, so they were restored. Review "
                   "whether each is still wanted.")
    return _render_front_matter(new_content, new_keys), notices
