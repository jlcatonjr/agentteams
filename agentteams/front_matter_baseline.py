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

**Fix, four parts.**

1. :func:`compute` records, per key, the value agentteams *authored*: the written value when it equals the
   template's render, else the previous trusted baseline's value, else nothing (unknown, which keeps the
   project's value). The log marks this with ``front_matter_baseline_version: 2``.
2. :func:`load` treats a legacy (unversioned, as-written) baseline as unknown: any of its values may be a
   project's kept value, so on the transition run the merge keeps every project value and proposes the
   template's. Nothing legacy is carried forward, so a key that still differs from the template stays
   unknown (a proposal on each run) until the project adopts the template's value or a fresh generation.
3. :func:`keep_disk_front_matter` puts the project's block back before the three-way merge when the legacy
   whole-body migration replaced it with the render's.
4. :func:`restore_dropped_handoffs` is the last line of defence on the ``--merge`` write (and its dry-run
   preview), whatever produced the merged content (the legacy whole-body migration takes the render's front
   matter wholesale): an on-disk handoff the content about to be written lacks is restored, unless the
   trusted baseline shows agentteams authored it (then the template retired it, and that is said). A restore,
   or a ``handoffs:`` it cannot check, is announced with :data:`HANDOFF_KEPT_PREFIX`, which ``fleet`` turns
   into REVIEW. ``--overwrite``, interop and sync writes do not pass through it.

Stdlib only.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from agentteams.front_matter_merge import (
    _YAML_FM_RE,
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
        ``{rel_path: {key: value}}`` for a version-2 baseline; ``{}`` (unknown) for a legacy or absent one.

    Raises:
        Nothing.
    """
    raw = log.get("front_matter_baseline") if isinstance(log, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    if log.get(VERSION_KEY) != BASELINE_VERSION:
        return {}       # legacy: as written, so any value may be a kept project value (see the module docstring)
    return {rel: {k: v for k, v in keys.items() if isinstance(v, str)}
            for rel, keys in raw.items() if isinstance(keys, Mapping)}


def load_for(output_dir: Path, *, version_2_only: bool = False) -> Baseline | None:
    """The trusted baseline recorded in *output_dir*'s ``references/build-log.json``.

    Args:
        output_dir: The agents directory.
        version_2_only: Return None unless the log carries a version-2 baseline (what
            :func:`compute` may carry forward).

    Returns:
        :func:`load`'s result; ``{}`` (unknown, which suppresses every automatic application) when there is
        no readable log, or None under *version_2_only* for anything but a version-2 log.

    Raises:
        Nothing.
    """
    from agentteams import drift

    try:
        log = drift.load_build_log(output_dir)
    except (FileNotFoundError, ValueError, OSError, KeyError):
        log = {}
    if version_2_only and log.get(VERSION_KEY) != BASELINE_VERSION:
        return None
    return load(log)


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


def _reindent_item(lines: list[str], block: str) -> list[str]:
    """Shift an item's lines to the indentation of *block*'s items, so the restored list stays valid YAML."""
    have = len(_ITEM_RE.match(lines[0]).group(1)) if lines and _ITEM_RE.match(lines[0]) else 0
    first = next((m for line in block.split("\n") if (m := _ITEM_RE.match(line))), None)
    want = len(first.group(1)) if first else have
    if want == have:
        return lines
    return [(" " * want + line[have:]) if line[:have].strip() == "" else line for line in lines]


def keep_disk_front_matter(content: str, existing_content: str) -> str:
    """*content* with its front-matter block replaced by *existing_content*'s.

    The legacy whole-body migration writes the render's front matter wholesale; the three-way merge must
    start from the project's block instead, or every project value (``model:``, a wider ``tools:``, an edited
    ``agents:`` roster, handoffs) is lost and then recorded as agentteams-authored.

    Args:
        content: The merged content.
        existing_content: The on-disk file.

    Returns:
        *content* with the on-disk front matter, or *content* unchanged when either has none.

    Raises:
        Nothing.
    """
    new = _YAML_FM_RE.match(content)
    old = _YAML_FM_RE.match(existing_content)
    if not new or not old:
        return content
    return old.group(1) + content[new.end():]


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
    if not new_content.strip():
        return new_content, []          # a merge that failed writes nothing
    disk_value = _front_matter_keys(existing_content).get("handoffs")
    disk = handoff_items(disk_value)
    new_keys = _front_matter_keys(new_content)
    current = new_keys.get("handoffs")
    if not disk:
        unchecked = disk_value and disk_value.strip() not in ("", "[]") and current != disk_value
        return new_content, ([f"{HANDOFF_KEPT_PREFIX}(s) NOT checked: the on-disk `handoffs:` is not a block list "
                              "and the update changes it — compare it with the backup by hand."] if unchecked else [])
    if not new_keys:
        return new_content, [f"{HANDOFF_KEPT_PREFIX}(s) NOT restored: the content to be written has no front "
                             "matter, so its handoffs could not be added back — they are in the backup."]
    new_items = handoff_items(current)
    present = {key for key, _ in new_items}
    base_items = handoff_items((baseline or {}).get("handoffs"))
    authored_keys = {key for key, _ in base_items}
    # A handoff is (agent, label). One whose agent the new block no longer reaches is dropped; one whose agent is
    # still reached is a rewording, unless the disk reached that agent more often than the new block does (two
    # handoffs to the orchestrator, "Return" and "Escalate", against the template's one): the surplus is dropped.
    surplus = Counter(key[0] for key, _ in disk)
    surplus.subtract(Counter(key[0] for key, _ in new_items))
    dropped, reworded = [], []
    for key, lines in disk:
        if key in present:
            continue
        if surplus[key[0]] > 0:
            surplus[key[0]] -= 1
            dropped.append((key, lines))
        elif key not in authored_keys:
            reworded.append(key)
    restore = [(key, lines) for key, lines in dropped if key not in authored_keys]
    retired = [key for key, _ in dropped if key in authored_keys]
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
    new_keys["handoffs"] = block.rstrip("\n") + "".join(
        "\n" + "\n".join(_reindent_item(lines, block)) for _, lines in restore)
    notices.append(f"{HANDOFF_KEPT_PREFIX}(s) the update would have dropped: {names} — the template does not "
                   "carry them and nothing records agentteams writing them, so they were restored. Review "
                   "whether each is still wanted.")
    return _render_front_matter(new_content, new_keys), notices
