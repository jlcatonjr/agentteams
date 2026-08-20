"""front_matter_merge.py — three-way merge of a generated file's YAML front matter.

Carved out of ``fences`` on the third CH-07 breach of the day. The seam is not arbitrary: front
matter is the one part of a generated file that **cannot be fenced**, because YAML must be the
literal first bytes and a fence marker is an HTML comment. It therefore needs an entirely
different mechanism — a three-way comparison against the last-emitted baseline — and that
mechanism shares nothing with the fence machinery beyond two regexes.

Splitting here means "why is front matter handled differently?" is answered by the module
boundary and this docstring, rather than reconstructed from a thousand-line file.

``fences`` re-exports these names, so no existing import changed.
"""

from __future__ import annotations

import re

#: Matches the whole YAML front-matter block, including its delimiters.
_YAML_FM_RE = re.compile(r"^(---\n.+?\n---\n)", re.DOTALL)

#: Matches a top-level `key: value` line inside a front-matter block.
_FM_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$", re.MULTILINE)


#: Front-matter keys excluded from drift reporting because they are project-interpolated by
#: construction: every generated file's `name`/`description` embeds {PROJECT_NAME}, so they
#: differ between template and render in EVERY file. Reporting them would make the notice fire
#: universally, and a notice that fires on everything gets muted — which is the same silence
#: this detection exists to end.
_DRIFT_EXEMPT_FRONT_MATTER_KEYS: frozenset[str] = frozenset({"name", "description"})

#: Matches a top-level `key: value` line inside a YAML front-matter block. Deliberately not a
#: YAML parse: front matter here is flat scalars plus inline lists, PyYAML is a test-only
#: dependency, and this module is on the stdlib-only base install path.
_FM_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$", re.MULTILINE)


def _front_matter_keys(content: str) -> dict[str, str]:
    """Return the top-level front-matter keys of ``content`` mapped to their raw values.

    A block-style value (``key:`` alone on its line, with indented lines below it — the shape
    ``agents:``/``handoffs:`` render in whenever the list is non-trivial) is captured in full,
    not just its first line. A single-line regex applied to the whole block, as this function
    used before, matches only the ``key:`` line itself: ``(.*)`` stops at end-of-line, and the
    indented continuation lines don't start with a letter, so they never become part of the
    match. That silently produced ``{"agents": ""}`` — an empty value indistinguishable from a
    genuinely-empty key — which then round-tripped straight back out through
    :func:`_render_front_matter` and blanked the block on disk the moment any *other*
    front-matter key changed on the same file (issue #131). Scanning line-by-line and
    accumulating each key's indented continuation lines keeps the block's raw text intact so it
    round-trips unchanged when nothing about it actually differs.

    Args:
        content: A full file body, with or without a YAML front-matter block.

    Returns:
        ``{key: raw_value}`` for the first front-matter block, or ``{}`` when there is none. A
        block-style value's raw text is prefixed with ``\\n`` (its continuation lines, verbatim)
        so :func:`_render_front_matter` can tell it apart from a same-line scalar.
    """
    match = _YAML_FM_RE.match(content)
    if not match:
        return {}
    # match.group(1) is "---\n<body>\n---\n"; splitting on "\n" always leaves the opening
    # delimiter at index 0 and the closing delimiter followed by a trailing "" at the end.
    body_lines = match.group(1).split("\n")[1:-2]

    keys: dict[str, str] = {}
    current_key: str | None = None
    current_scalar = ""
    block_lines: list[str] = []

    def _flush() -> None:
        if current_key is None:
            return
        # A line only proves this key is block-style if it is INDENTED and carries real
        # (non-blank) content — a genuine YAML block item is always indented under its key.
        # Two failure modes were found under adversarial review of earlier versions of this
        # fix, both from treating any non-key line as block-content proof:
        #   - a blank line trailing a same-line scalar (`name: A\n\ntools: [...]`) — `[""]`
        #     is truthy, so `name` flipped into a phantom empty block;
        #   - an unindented comment line trailing a same-line scalar (`name: A\n# note\n
        #     tools: [...]`) — non-blank but not part of any block, same phantom-block result.
        # Both discarded the scalar's real value, reproducing the exact class of loss this
        # function exists to close. Blank or comment lines that genuinely sit inside an
        # already-proven block (between two indented list items) are still preserved verbatim.
        is_block = any(
            raw_line.strip() and raw_line[:1] in (" ", "\t") for raw_line in block_lines
        )
        keys[current_key] = ("\n" + "\n".join(block_lines)) if is_block else current_scalar.strip()

    for line in body_lines:
        top_level = _FM_KEY_RE.match(line)
        if top_level:
            _flush()
            current_key, current_scalar, block_lines = top_level.group(1), top_level.group(2), []
        elif current_key is not None:
            block_lines.append(line)
    _flush()
    return keys


def _detect_front_matter_drift(new_rendered: str, existing_on_disk: str) -> list[str]:
    """Report front-matter keys whose template value differs from the on-disk one.

    Front matter lies outside every AGENTTEAMS fence, so merge preserves the on-disk version
    verbatim. That is correct — it is how a project keeps its own edits — but it also means a
    template change to a key like ``tools:`` reaches new teams only, and silently. Verified
    2026-07-30 against two downstream repos: the fenced body updated, the tool grant did not,
    and nothing in the run output said so.

    Scope is deliberately narrow. Only *keys* are compared, never values-as-diff, and
    :data:`_DRIFT_EXEMPT_FRONT_MATTER_KEYS` is skipped. Unfenced *prose* drift is not reported
    at all: prose below the front matter is genuinely user-owned, and without a provenance
    mechanism the format does not have, an edit cannot be told apart from intended authorship.
    Front matter is the case where the template is the de-facto owner and the failure was
    demonstrated.

    Args:
        new_rendered: Freshly rendered file content.
        existing_on_disk: Current on-disk content.

    Returns:
        Human-readable notices, one per drifted key. Empty when nothing drifted.
    """
    fresh = _front_matter_keys(new_rendered)
    current = _front_matter_keys(existing_on_disk)
    if not fresh or not current:
        return []
    notices: list[str] = []
    for key, value in fresh.items():
        if key in _DRIFT_EXEMPT_FRONT_MATTER_KEYS:
            continue
        # The notice carries the exact replacement line. Auto-applying it was designed and then
        # withdrawn: `tools:` is a privilege declaration, so writing it unattended is a
        # privilege-escalation path, and the same command runs against consumer repos — one of
        # which has no version control at all, making a bad write unrecoverable. A human applying
        # a one-line edit they can see is the right cost here.
        if key not in current:
            notices.append(
                f"front matter: template adds {key!r} (absent on disk) — add `{key}: {value}`"
            )
        elif current[key] != value:
            notices.append(
                f"front matter: {key!r} differs — template {value!r}, on disk {current[key]!r}"
                f" — to adopt it, set `{key}: {value}`"
            )
    return notices


#: Front-matter keys that declare CAPABILITY rather than metadata. Excluded from automatic
#: application even when three-way provenance proves the project never edited the key: proving
#: nobody touched `tools:` is not the same as having authority to grant a downstream agent shell
#: access. These are always reported as proposals for a human to apply.
#:
#: Every front-matter key that carries a capability grant, across every framework this module
#: emits. ONE constant, deliberately: the module previously carried two disagreeing sets — this
#: one (which omitted ``allowed-tools``) and ``front_matter_reconcile.CAPABILITY_KEYS`` (which
#: included it). The escalation check below keys on this set, so omitting the key the Claude
#: adapter actually emitted meant a widened Claude grant never reached the check at all. See
#: references/plans/constitutional-redteam-audit-2026-08-06.report.md finding W8.
#:
#: ``front_matter_reconcile`` imports this rather than defining its own.
CAPABILITY_FRONT_MATTER_KEYS: frozenset[str] = frozenset({
    "tools",            # copilot-vscode, and Claude subagents since 2026-08-06
    "allowed-tools",    # Claude subagents before 2026-08-06; still on disk everywhere
    "disallowedTools",  # Claude deny-list
    "capabilities",
    "model",
    "agents",
})

#: Back-compat alias for the private name this module used before the sets were unified.
_CAPABILITY_FRONT_MATTER_KEYS = CAPABILITY_FRONT_MATTER_KEYS

#: A front-matter list value, e.g. ``['read', 'search']``. Quoted items only — an unquoted or
#: block-style list is not this shape and must not be guessed at.
_FM_LIST_RE = re.compile(r"^\[(.*)\]$", re.DOTALL)
_FM_LIST_ITEM_RE = re.compile(r"""['"]([^'"]+)['"]""")

#: A bare comma-separated scalar, e.g. ``Read, Grep, Bash(python -m agentteams.research:*)`` —
#: the shape Claude subagent front matter uses. Items may contain parentheses, colons, dots and
#: asterisks (scoped Bash permissions), but never a bracket or a quote, which would mean this is
#: really one of the other two shapes.
_FM_SCALAR_LIST_ITEM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*(?:\([^()]*\))?$")


#: Capability keys whose value is a SCALAR, not a list. `model:` sits in the capability key set
#: because changing it is privileged, but `model: opus` is one value rather than a one-item
#: grant, and reading it as a set would let a model change be scored as a capability widening.
_SCALAR_CAPABILITY_KEYS: frozenset[str] = frozenset({"model"})


def _parse_capability_list(raw: str, *, key: str | None = None) -> frozenset[str] | None:
    """Parse a front-matter capability value into a set of items.

    Used to decide whether a capability change *narrows* or *widens* a grant. The comparison
    must be a parsed-set subset test and never a substring one: ``['read']`` is a substring of
    both ``['read', 'edit']`` and ``['read', 'ed']``, and only the first of those is a real
    narrowing.

    Two shapes are recognised:

    * bracketed and quoted — ``['read', 'search']`` (copilot-vscode);
    * bare comma-separated — ``Read, Grep, Glob`` (Claude subagents).

    The second was added 2026-08-06. Without it every Claude capability value returned ``None``,
    which made the widening check structurally unreachable for Claude teams even once the key
    itself was recognised — the two halves of finding W8 had to be fixed together or neither
    worked.

    Args:
        raw: The raw front-matter value.
        key: The front-matter key the value belongs to, when the caller knows it. It
            disambiguates a bare single token: ``Read`` under ``tools:`` is a one-item grant,
            while ``opus`` under ``model:`` is a scalar. **Without a key a bare single token is
            treated as unparseable**, because guessing wrong in that direction would let a
            model change be scored as a capability widening — and a caller that does not know
            its own key has no business asserting either reading.

    Returns:
        The item set, or ``None`` when the value matches neither shape. ``None`` makes every
        caller fall through to the propose-only path, so an unparseable value can never be read
        as narrowing.
    """
    text = raw.strip()
    if not text:
        return None
    if key is not None and key in _SCALAR_CAPABILITY_KEYS:
        return None

    match = _FM_LIST_RE.match(text)
    if match:
        items = _FM_LIST_ITEM_RE.findall(match.group(1))
        return frozenset(items) if items else None

    if "[" in text or "]" in text or "'" in text or '"' in text:
        return None  # a malformed list shape, not a scalar list — do not guess

    parts = [p.strip() for p in text.split(",")]
    if not all(parts) or not all(_FM_SCALAR_LIST_ITEM_RE.match(p) for p in parts):
        return None
    if len(parts) == 1 and key is None:
        return None  # ambiguous without a key — see Args
    return frozenset(parts)


#: Front-matter keys that name the SAME field across framework/upstream-doc generations, newest
#: first. When a template declares the new key and the deployed file carries only the old one,
#: the merge renames in place rather than adding a second, contradictory declaration.
#:
#: NOT restricted to capability keys despite the historical name below — the mechanism only
#: renames, it never reads :data:`CAPABILITY_FRONT_MATTER_KEYS`. ``user-invocable`` (P3,
#: 2026-08-15) is the first non-capability entry; the notice text branches on capability-ness
#: (see :func:`_migrate_superseded_keys`) so a UI-visibility toggle isn't described as a "grant".
_KEY_SUCCESSION: tuple[tuple[str, str], ...] = (
    ("tools", "allowed-tools"),
    ("user-invocable", "user-invokable"),
)


def _migrate_superseded_keys(
    fresh: dict[str, str], current: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Rename a deployed front-matter key when the template has moved to its successor.

    Front matter is preserved verbatim by ``--update --merge``, so a change to the *name* of a
    key would otherwise never reach a deployed team: the file would keep the old key forever,
    and if the new key were merely added the file would carry two declarations with nothing
    saying which the runtime honours.

    The rename is provenance-keyed and narrow. It fires only when the template declares the
    successor key and the deployed file carries only the superseded one. **The value is carried
    across untouched** — the template owns the key's NAME, the project owns its VALUE, and a
    rename must not become a back door for silently applying a template's value.

    Args:
        fresh: Front-matter keys of the freshly rendered file.
        current: Front-matter keys of the on-disk file.

    Returns:
        ``(migrated_current, notices)``. ``migrated_current`` is a copy; ``current`` is not
        mutated.
    """
    migrated = dict(current)
    notices: list[str] = []
    for new_key, old_key in _KEY_SUCCESSION:
        if new_key in fresh and old_key in migrated and new_key not in migrated:
            value = migrated.pop(old_key)
            migrated[new_key] = value
            if old_key in CAPABILITY_FRONT_MATTER_KEYS:
                notices.append(
                    f"front matter: capability key {old_key!r} renamed to {new_key!r} "
                    f"(value preserved: {value!r}). {old_key!r} is not read by this "
                    f"framework's runtime, so the grant it declared was never enforced."
                )
            else:
                notices.append(
                    f"front matter: key {old_key!r} renamed to {new_key!r} "
                    f"(value preserved: {value!r}). {old_key!r} is not recognised by this "
                    f"framework's runtime, so its value was never honoured."
                )
    return migrated, notices


def _merge_front_matter(
    rendered: str,
    on_disk: str,
    baseline: dict[str, str] | None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Three-way merge of a file's YAML front matter.

    Front matter cannot be fenced — YAML must be the literal first bytes of the file, before any
    HTML comment — so it needs a different mechanism rather than a better fence. Comparing the
    template's value, the on-disk value and the value emitted *last time* distinguishes "the
    project chose this" from "nobody has touched it since generation", which a two-way comparison
    cannot.

    That distinction is the whole point. An earlier `--sync-front-matter` design was withdrawn
    because, without a baseline, applying the template's value would overwrite deliberate project
    choices. With one, the safe subset is provable.

    | template vs baseline | on-disk vs baseline | outcome                          |
    |----------------------|---------------------|----------------------------------|
    | unchanged            | anything            | keep the on-disk value           |
    | changed              | unchanged           | apply the template's value       |
    | changed              | changed             | conflict — keep on-disk, report  |
    | new key              | absent              | add it                           |

    Capability keys (:data:`_CAPABILITY_FRONT_MATTER_KEYS`) are never applied automatically; they
    are reported as proposals however clean their provenance.

    Args:
        rendered: Freshly rendered file content.
        on_disk: Current on-disk content.
        baseline: Front matter as emitted at last generation, or ``None`` when unknown (an older
            build log). Without it nothing is applied — an unknown baseline is treated as
            "the project may have edited everything", which is the conservative reading.

    Returns:
        ``(merged_keys, applied_notices, proposal_notices)``.
    """
    fresh = _front_matter_keys(rendered)
    current = _front_matter_keys(on_disk)
    if not fresh or not current:
        return current, [], []

    # A key the template has RENAMED is migrated before anything else, so the comparisons below
    # see one declaration rather than an old key and a new key that disagree.
    current, key_migrations = _migrate_superseded_keys(fresh, current)

    merged = dict(current)
    applied: list[str] = list(key_migrations)
    proposals: list[str] = []

    for key, template_value in fresh.items():
        if key in _DRIFT_EXEMPT_FRONT_MATTER_KEYS:
            continue
        disk_value = current.get(key)
        if disk_value == template_value:
            continue

        base_value = baseline.get(key) if baseline else None
        untouched = baseline is not None and disk_value == base_value
        template_moved = baseline is None or base_value != template_value

        # A capability grant that is WIDER on disk than in the template is reported whatever the
        # template did, and this check must precede the `template_moved` early-continue below.
        # The escalation case is exactly "template unchanged, disk widened" — which took that
        # continue and produced no notice at all. Measured 2026-08-01: not merely unapplied, as
        # the review's §4.3 had it, but entirely silent. The merge still does not touch the value;
        # naming it is the whole remediation.
        if key in _CAPABILITY_FRONT_MATTER_KEYS:
            _t = _parse_capability_list(template_value, key=key)
            _d = _parse_capability_list(disk_value or "", key=key)
            if _t is not None and _d is not None and _t < _d and not (untouched and template_moved):
                proposals.append(
                    f"front matter: {key!r} on disk GRANTS MORE than the template — extra: "
                    f"{sorted(_d - _t)}. Not applied: this file has been edited since generation, "
                    f"so the wider grant may be a deliberate project choice or may be an "
                    f"escalation. Review it, then set `{key}: {template_value}` to revoke."
                )
                continue

        if not template_moved:
            continue                                    # template unchanged; project's value wins

        if key in _CAPABILITY_FRONT_MATTER_KEYS:
            template_set = _parse_capability_list(template_value, key=key)
            disk_set = _parse_capability_list(disk_value or "", key=key)
            narrowing = (
                template_set is not None
                and disk_set is not None
                and template_set < disk_set          # strict subset, on parsed sets
            )
            if narrowing and untouched:
                merged[key] = template_value
                applied.append(
                    f"front matter: {key!r} narrowed to {template_value} — removed "
                    f"{sorted(disk_set - template_set)} (unmodified since generation)"
                )
            else:
                proposals.append(
                    f"front matter: {key!r} is a capability declaration — template has "
                    f"{template_value}, on disk {disk_value!r}. Not applied automatically; "
                    f"set `{key}: {template_value}` to adopt it."
                )
        elif untouched:
            merged[key] = template_value
            applied.append(
                f"front matter: {key!r} updated to {template_value} "
                f"(unmodified since generation)"
            )
        elif disk_value is None and baseline is not None:
            # Only with a baseline: absent-on-disk could equally mean "deliberately removed",
            # and an unknown baseline must not be read as permission.
            merged[key] = template_value
            applied.append(f"front matter: added {key!r} = {template_value}")
        else:
            proposals.append(
                f"front matter: {key!r} changed in BOTH template and this file — template "
                f"{template_value}, on disk {disk_value!r}. Kept yours; reconcile by hand."
            )
    return merged, applied, proposals


def _render_front_matter(content: str, keys: dict[str, str]) -> str:
    """Return ``content`` with its front-matter block rewritten from ``keys``.

    Key order follows the existing block so a merge never reshuffles a file the project reads.
    A key added by the merge is appended after the ones already present.

    A block-style value (see :func:`_front_matter_keys`) carries a leading ``\\n`` sentinel and
    is written straight after ``key:`` with no separating space, reproducing
    ``key:\\n  - item`` rather than a scalar's ``key: value`` — the latter would leave a stray
    trailing space on the ``key:`` line and, worse, is what let issue #131's already-truncated
    ``""`` render as a visibly blanked key in the first place.

    Args:
        content: The file whose front matter is being rewritten.
        keys: The merged key/value mapping.

    Returns:
        The file with its front matter replaced; unchanged when it has none.
    """
    match = _YAML_FM_RE.match(content)
    if not match:
        return content
    existing_order = list(_front_matter_keys(content))
    ordered = existing_order + [k for k in keys if k not in existing_order]
    body = "\n".join(
        f"{k}:{keys[k]}" if keys[k].startswith("\n") else f"{k}: {keys[k]}"
        for k in ordered
        if k in keys
    )
    return f"---\n{body}\n---\n" + content[match.end():]


