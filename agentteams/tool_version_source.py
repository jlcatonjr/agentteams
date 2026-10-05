"""tool_version_source.py — tool versions read from the target project's pin file.

A brief's ``tools[].version`` is fixed when the team is generated. For a tool the target project
pins in a file of its own — a Lean project's ``lean/lean-toolchain``, say — that version goes stale
the moment the project bumps its pin, and a ``latest`` documentation URL was never the pinned
version's documentation to begin with. An optional ``version_source`` on a tool entry names where
the pin lives instead::

    "version_source": {
        "file": "lean/lean-toolchain",
        "pattern": "v(?P<version>[0-9][0-9.]*)",
        "resolver": "mathagents run lean_docs"
    }

and ``docs_url`` may carry ``{version}`` / ``{major_minor}``. The rendered tool document then tells
the agent to resolve the version from the pin file (or the resolver) at use time, and labels the
brief's ``version`` as the generation-time default.

Kept out of ``analyze_tools`` (which classifies tools) and ``render`` (which fills templates):
both only call in here. Pure and stdlib-only. ``resolve_tool_version`` reads one file and runs no
command and no network request — the resolver is something the *agent* runs, never agentteams.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The named group a ``version_source.pattern`` must define.
VERSION_GROUP = "version"

#: Placeholders ``docs_url`` may carry. Lower-case on purpose: the template engine resolves only
#: ``{UPPER_SNAKE}`` tokens, so these pass through rendering untouched when left unexpanded.
_DOCS_URL_PLACEHOLDER_RE = re.compile(r"\{(version|major_minor)\}")


def version_source_errors(tool: dict[str, Any]) -> list[str]:
    """Validation errors for a tool entry's ``version_source``, or ``[]`` when absent or valid.

    Args:
        tool: One ``tools[]`` entry from the project description.

    Returns:
        Human-readable error strings naming the tool and the offending field.
    """
    source = tool.get("version_source")
    if source is None:
        return []
    name = tool.get("name") or "<unnamed tool>"
    if not isinstance(source, dict):
        return [f"tools[{name!r}].version_source must be an object"]
    errors: list[str] = []
    file_ = source.get("file")
    if not isinstance(file_, str) or not file_.strip():
        errors.append(f"tools[{name!r}].version_source.file is required (a path relative to the project root)")
    elif Path(file_).is_absolute() or ".." in Path(file_).parts:
        errors.append(
            f"tools[{name!r}].version_source.file must be relative to the project root "
            f"without '..' segments: {file_!r}"
        )
    pattern = source.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        errors.append(f"tools[{name!r}].version_source.pattern is required")
    else:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            errors.append(f"tools[{name!r}].version_source.pattern does not compile: {exc}")
        else:
            if VERSION_GROUP not in compiled.groupindex:
                errors.append(
                    f"tools[{name!r}].version_source.pattern must define a named group "
                    f"(?P<{VERSION_GROUP}>...): {pattern!r}"
                )
    resolver = source.get("resolver")
    if resolver is not None and (not isinstance(resolver, str) or not resolver.strip()):
        errors.append(f"tools[{name!r}].version_source.resolver must be a non-empty string when given")
    unknown = sorted(set(source) - {"file", "pattern", "resolver"})
    if unknown:
        errors.append(f"tools[{name!r}].version_source has unknown key(s): {', '.join(unknown)}")
    return errors


def resolve_tool_version(tool: dict[str, Any], project_root: str | Path) -> str | None:
    """Read a tool's pinned version from the target project's pin file.

    Applies ``version_source.pattern`` to ``version_source.file`` (relative to *project_root*)
    and returns the ``version`` group of the first match. Never runs ``resolver`` and never
    touches the network.

    Args:
        tool: A ``tools[]`` entry (or a tool-doc spec carrying ``version_source``).
        project_root: The target project's root directory.

    Returns:
        The resolved version, or None when the tool has no ``version_source``, the file is
        missing, unreadable or outside *project_root*, or the pattern does not match.

    Raises:
        re.error: If the pattern does not compile (``version_source_errors`` reports this first
            for any brief that went through ingest).
    """
    source = tool.get("version_source")
    if not isinstance(source, dict) or not source.get("file") or not source.get("pattern"):
        return None
    root = Path(project_root).resolve()
    path = (root / source["file"]).resolve()
    if path != root and root not in path.parents:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(source["pattern"], text)
    if not match or VERSION_GROUP not in match.re.groupindex:
        return None
    return match.group(VERSION_GROUP) or None


def major_minor(version: str) -> str:
    """First two dot-separated components: ``v4.24.0`` → ``v4.24``; ``18`` → ``18``."""
    return ".".join(version.split(".")[:2])


def expand_docs_url(docs_url: str, version: str) -> str:
    """Substitute ``{version}`` and ``{major_minor}`` in *docs_url*; unchanged without them.

    Left unexpanded when *version* is empty, so an empty default never yields a broken URL.
    """
    if not version or not _DOCS_URL_PLACEHOLDER_RE.search(docs_url):
        return docs_url
    values = {"version": version, "major_minor": major_minor(version)}
    return _DOCS_URL_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], docs_url)


def tool_version_placeholders(spec: dict[str, Any], docs_url: str) -> dict[str, str]:
    """``TOOL_VERSION``, ``TOOL_DOCS_URL`` and ``TOOL_VERSION_RESOLUTION`` for one tool doc.

    Without ``version_source`` this reproduces the previous values exactly — ``TOOL_VERSION`` is
    the brief's version, ``TOOL_DOCS_URL`` the docs URL (with any placeholders expanded to that
    version) and ``TOOL_VERSION_RESOLUTION`` is empty — so rendered output is byte-identical.

    Args:
        spec: A ``tool_agents`` / ``reference_tools`` manifest entry.
        docs_url: The docs URL after the render layer's MANUAL fallback.

    Returns:
        Mapping of the three placeholder names to their rendered values.
    """
    default = spec.get("tool_version", "")
    source = spec.get("version_source")
    if not isinstance(source, dict) or not source.get("file"):
        return {
            "TOOL_VERSION": default,
            "TOOL_DOCS_URL": expand_docs_url(docs_url, default),
            "TOOL_VERSION_RESOLUTION": "",
        }
    pin_file = source["file"]
    label = f"(version pinned in {pin_file}"
    label += f"; generation-time default {default})" if default else ")"
    templated = bool(_DOCS_URL_PLACEHOLDER_RE.search(docs_url))
    steps = [
        f"1. Read `{pin_file}` in this project and apply the pattern `{source.get('pattern', '')}`; "
        f"its `{VERSION_GROUP}` group is the pinned version.",
    ]
    if source.get("resolver"):
        steps.append(
            f"2. Run `{source['resolver']}`. It prints the resolved version and documentation "
            "URLs; when it succeeds, its output takes precedence over step 1."
        )
    if templated:
        parts = []
        if "{version}" in docs_url:
            parts.append("`{version}` replaced by the resolved version")
        if "{major_minor}" in docs_url:
            parts.append("`{major_minor}` replaced by its first two components")
        steps.append(f"{len(steps) + 1}. Consult `{docs_url}` with {' and '.join(parts)}.")
    else:
        steps.append(
            f"{len(steps) + 1}. Consult the documentation above for the resolved version, not a "
            "`latest` page."
        )
    fallback = (
        f"`{default}` is the generation-time default recorded when this document was generated; "
        "use it only when the pin file is missing or does not match, and say so."
        if default else
        "No generation-time default was recorded; if the pin cannot be resolved, say so rather "
        "than assuming a version."
    )
    resolution = (
        "\n\n**Version resolution.** The version is not fixed: this project pins it in "
        f"`{pin_file}`. Before relying on version-specific behaviour or documentation:\n\n"
        + "\n".join(steps) + "\n\n" + fallback
    )
    return {
        "TOOL_VERSION": label,
        "TOOL_DOCS_URL": f"`{docs_url}`" if templated else docs_url,
        "TOOL_VERSION_RESOLUTION": resolution,
    }
