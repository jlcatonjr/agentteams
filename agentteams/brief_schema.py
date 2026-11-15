"""brief_schema.py — check a project description against its schema when it is loaded (IM item 13a, G-17-2).

``ingest.validate`` checks three fields; ``schemas/project-description.schema.json`` was never run on a brief, and it
had drifted from the code (96 of 155 descriptors in the workspace failed it, mostly over archetypes and tool categories
the code supports). This module runs the schema on every JSON description ``ingest.load`` reads and **warns** — it
never refuses this release. Refusal comes later, once the census is clean.

It also names any ``selected_archetypes`` slug that has no ``templates/domain/<slug>.template.md``. Such an archetype is
not rendered by agentteams; another layer (researchteam's layer 2) may provide it, so this is informational and never
fails a run.

Warnings go to stderr, once per description path per process, so repeated loads (fleet, memory index) don't repeat them.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

_PACKAGE = Path(__file__).resolve().parent
SCHEMA_PATH = _PACKAGE / "schemas" / "project-description.schema.json"
DOMAIN_TEMPLATES = _PACKAGE / "templates" / "domain"
#: The most schema errors one warning lists before it says "and N more".
_SHOWN = 5
#: Longest single issue a warning prints: a oneOf/anyOf message echoes the whole value, which can hold an absolute
#: path from the brief (@adversarial).
_MAX_ISSUE = 160
#: The fleet reads these lines out of each render's captured output (``fleet.TargetResult.schema_warnings``).
WARN_PREFIX = "[WARN] "
WARN_MARKER = "does not match the project-description schema"

_warned: set[str] = set()


def known_archetypes() -> frozenset[str]:
    """Return the archetype slugs agentteams renders: every domain template that is an agent, not a doc or reference.

    Returns:
        The slugs (``templates/domain/<slug>.template.md``, minus ``tool-*.doc`` and ``*-reference`` templates). An
        agent template must not be named ``*-reference``: it would drop out of this list and of its sync test.

    Raises:
        Nothing.
    """
    out = set()
    for path in DOMAIN_TEMPLATES.glob("*.template.md"):
        slug = path.name[: -len(".template.md")]
        if slug.endswith(".doc") or slug.endswith("-reference"):
            continue
        out.add(slug)
    return frozenset(out)


@lru_cache(maxsize=1)
def _validator() -> Any:
    """Return the cached Draft 7 validator for the project-description schema (it raises only on a broken install).

    The schema ``$ref``s sibling schemas (``mcp_servers`` items are ``mcp-server.schema.json``), so every schema in
    ``schemas/`` is registered under its ``$id``; without that, a brief with ``mcp_servers`` failed to validate.
    """
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    resources = []
    for sibling in SCHEMA_PATH.parent.glob("*.schema.json"):
        contents = json.loads(sibling.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents, default_specification=DRAFT7)
        resources.append((contents.get("$id", sibling.name), resource))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(schema, registry=Registry().with_resources(resources))


def _check_failures() -> tuple[type[BaseException], ...]:
    """Return the exceptions a schema check can raise on a broken install or schema; ``warn_on_load`` reports them."""
    from jsonschema.exceptions import SchemaError
    from referencing.exceptions import Unresolvable

    return (OSError, ValueError, TypeError, LookupError, RecursionError, SchemaError, Unresolvable)


def schema_errors(description: dict[str, Any]) -> list[str]:
    """Return each schema violation as ``path: message``, in document order.

    Args:
        description: A loaded description.

    Returns:
        The violations; empty when the description conforms.

    Raises:
        Nothing.
    """
    errors = sorted(_validator().iter_errors(description), key=lambda e: list(map(str, e.absolute_path)))
    return [_clip(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}") for e in errors]


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_ISSUE else text[: _MAX_ISSUE - 1] + "…"


def unrendered_archetypes(description: dict[str, Any]) -> list[str]:
    """Return the ``selected_archetypes`` slugs agentteams has no template for (sorted).

    Args:
        description: A loaded description.

    Returns:
        The slugs; empty when every selected archetype is known or none are selected.

    Raises:
        Nothing.
    """
    selected = description.get("selected_archetypes")
    if not isinstance(selected, list):
        return []
    return sorted({s for s in selected if isinstance(s, str)} - known_archetypes())


def warn_on_load(path: Path, description: dict[str, Any]) -> None:
    """Print the schema and unrendered-archetype warnings for *path*, once per process; a check error is a WARN too.

    Args:
        path: The description file.
        description: Its loaded contents.

    Returns:
        None.

    Raises:
        Nothing: an error while checking (a broken install, an unexpected value) is reported, never raised, so a
        load can't fail on a warn-only check (@adversarial).
    """
    key = str(path.resolve())
    if key in _warned:
        return
    _warned.add(key)
    try:
        errors = schema_errors(description)
    except _check_failures() as exc:
        print(f"{WARN_PREFIX}{path.name}: the project-description schema check was skipped ({type(exc).__name__})", file=sys.stderr)
        return
    if errors:
        shown = "; ".join(errors[:_SHOWN]) + (f"; and {len(errors) - _SHOWN} more" if len(errors) > _SHOWN else "")
        print(f"{WARN_PREFIX}{path.name} {WARN_MARKER} ({len(errors)} issue(s); "
              f"not enforced yet): {shown}", file=sys.stderr)
    if unknown := unrendered_archetypes(description):
        print(f"[INFO] {path.name}: selected_archetypes {', '.join(unknown)} have no agentteams template, so "
              "agentteams does not render them (another layer may provide them).", file=sys.stderr)


def schema_warning_lines(output: str) -> list[str]:
    """Return the schema warnings in a render's captured *output*, for the fleet report (@adversarial).

    The fleet runs each render in-process and captures its output, so a WARN printed there never reached the operator.

    Args:
        output: The captured stdout and stderr of one render.

    Returns:
        The warning lines, without the ``[WARN]`` prefix.

    Raises:
        Nothing.
    """
    return [line[len(WARN_PREFIX):].strip() for line in output.splitlines()
            if line.startswith(WARN_PREFIX) and ("project-description schema" in line)]
