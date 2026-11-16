"""Placeholders an archetype needs once a brief names it in ``selected_archetypes`` (#392 item 1).

Shared by the usability-mode placeholder policy (:mod:`agentteams.cli.render_pipeline`) and the ``--enrich``
rule fills (:mod:`agentteams.enrich._fills`), so neither replaces such a token with an "N/A" default.
"""

from __future__ import annotations

#: Archetypes whose placeholder a brief must fill once it names them in ``selected_archetypes`` (#392 item 1).
ARCHETYPE_PLACEHOLDERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "STYLE_REFERENCE_PATH": ("style-guardian", ("style_reference", "style_reference_path")),
    "REFERENCE_DB_PATH": ("reference-manager", ("reference_db_path", "reference_db")),
}


def explicitly_selected_unfilled(description: dict | None) -> set[str]:
    """Placeholder keys whose archetype the brief names in ``selected_archetypes`` without the value it needs.

    Args:
        description: The project description (brief), or ``None``.

    Returns:
        The keys of :data:`ARCHETYPE_PLACEHOLDERS` that stay ``{MANUAL:*}`` in every mode.
    """
    desc = description or {}
    named = desc.get("selected_archetypes")
    if not isinstance(named, list):
        return set()
    return {key for key, (slug, fields) in ARCHETYPE_PLACEHOLDERS.items()
            if slug in named and not any(desc.get(f) for f in fields)}
