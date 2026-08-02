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

    Args:
        content: A full file body, with or without a YAML front-matter block.

    Returns:
        ``{key: raw_value}`` for the first front-matter block, or ``{}`` when there is none.
    """
    match = _YAML_FM_RE.match(content)
    if not match:
        return {}
    return {k: v.strip() for k, v in _FM_KEY_RE.findall(match.group(1))}


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
_CAPABILITY_FRONT_MATTER_KEYS: frozenset[str] = frozenset({"tools", "model", "agents"})

#: A front-matter list value, e.g. ``['read', 'search']``. Quoted items only — an unquoted or
#: block-style list is not this shape and must not be guessed at.
_FM_LIST_RE = re.compile(r"^\[(.*)\]$", re.DOTALL)
_FM_LIST_ITEM_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _parse_capability_list(raw: str) -> frozenset[str] | None:
    """Parse a bracketed front-matter list into a set of items.

    Used to decide whether a capability change *narrows* a grant. The comparison must be a
    parsed-set subset test and never a substring one: ``['read']`` is a substring of both
    ``['read', 'edit']`` and ``['read', 'ed']``, and only the first of those is a real narrowing.

    Args:
        raw: The raw front-matter value, e.g. ``"['read', 'search']"``.

    Returns:
        The item set, or ``None`` when the value is not a quoted bracketed list — a scalar like
        ``model: opus`` or an unrecognised shape. ``None`` makes every caller fall through to the
        existing propose-only path, so an unparseable value can never be treated as narrowing.
    """
    match = _FM_LIST_RE.match(raw.strip())
    if not match:
        return None
    items = _FM_LIST_ITEM_RE.findall(match.group(1))
    return frozenset(items) if items else None


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

    merged = dict(current)
    applied: list[str] = []
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
            _t = _parse_capability_list(template_value)
            _d = _parse_capability_list(disk_value or "")
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
            template_set = _parse_capability_list(template_value)
            disk_set = _parse_capability_list(disk_value or "")
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
    body = "\n".join(f"{k}: {keys[k]}" for k in ordered if k in keys)
    return f"---\n{body}\n---\n" + content[match.end():]


