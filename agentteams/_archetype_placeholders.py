"""Placeholders an archetype needs once a brief names it in ``selected_archetypes`` (#392 item 1).

Shared by the usability-mode placeholder policy (:mod:`agentteams.cli.render_pipeline`) and the ``--enrich``
rule fills (:mod:`agentteams.enrich._fills`), so neither replaces such a token with an "N/A" default.
"""

from __future__ import annotations

#: Placeholder key -> (the archetype that needs it, the brief field that fills it). Whether the value was filled
#: is read from the manifest (``analyze`` also infers doc-site defaults), never from brief fields.
ARCHETYPE_PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "STYLE_REFERENCE_PATH": ("style-guardian", "style_reference"),
    "REFERENCE_DB_PATH": ("reference-manager", "reference_db_path"),
}


def named_archetype_keys(description: object) -> set[str]:
    """Placeholder keys whose archetype the brief names in an explicit ``selected_archetypes`` list.

    Args:
        description: The project description (brief); anything other than a dict names nothing.

    Returns:
        The keys of :data:`ARCHETYPE_PLACEHOLDERS` whose archetype is named. Such a key must never be
        replaced by an "N/A" default while its value is unresolved.
    """
    named = description.get("selected_archetypes") if isinstance(description, dict) else None
    if not isinstance(named, list):
        return set()
    return {key for key, (slug, _field) in ARCHETYPE_PLACEHOLDERS.items() if slug in named}
