"""
analyze.py — Analyze a project description to produce a team manifest.

Takes the normalized description dict from ingest.py and produces a
team manifest dict conforming to schemas/team-manifest.schema.json.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# _plan_output_files extracted to agentteams/output_plan.py (CH-07);
# re-exported so analyze._plan_output_files resolves unchanged.
from agentteams.output_plan import _plan_output_files  # noqa: F401,E402

from agentteams._utils import _slugify
from agentteams.mcp_detect import detect_mcp_candidates


# ---------------------------------------------------------------------------
# Archetype selection rules
# ---------------------------------------------------------------------------

#: (keywords in project description → archetype slug)
#: First match wins for each archetype; all applicable archetypes are included.
_ARCHETYPE_TRIGGERS: list[tuple[list[str], str]] = [
    # primary-producer is always included
    (["*"], "primary-producer"),
    # quality-auditor: always
    (["*"], "quality-auditor"),
    # cohesion-repairer: writing or documentation projects
    (["chapter", "essay", "paper", "book", "documentation", "doc", "report", "writing"], "cohesion-repairer"),
    # style-guardian: any project with a style reference or writing output
    (["style", "voice", "tone", "brand", "editorial", "chapter", "essay", "paper"], "style-guardian"),
    # technical-validator: code, data, or technical projects
    (["python", "javascript", "rust", "go", "java", "code", "api", "function", "module", "script",
      "pipeline", "data", "sql", "database", "csv", "json", "yaml"], "technical-validator"),
    # format-converter: any project producing a compiled output
    (["latex", "pdf", "pandoc", "html", "markdown", "compile", "build", "convert", "manuscript"], "format-converter"),
    # reference-manager: any project with citations or bibliography
    (["citation", "bibliography", "reference", "bib", "cite", "academic", "paper", "research"], "reference-manager"),
    # output-compiler: multi-component projects that need assembly
    (["chapter", "module", "component", "part", "section", "assemble", "compile", "build", "bundle"], "output-compiler"),
    # visual-designer: projects with figures, diagrams, or visual output
    (["figure", "diagram", "chart", "graph", "visualization", "image", "illustration", "dot", "graphviz"], "visual-designer"),
    # module-doc-author: projects distributing a pip package or producing API documentation
    (["pip", "pypi", "package", "distribution", "install", "api reference", "changelog", "mkdocs", "sphinx", "readthedocs"], "module-doc-author"),
    # module-doc-validator: always paired with module-doc-author
    (["pip", "pypi", "package", "distribution", "install", "api reference", "changelog", "mkdocs", "sphinx", "readthedocs"], "module-doc-validator"),
]

_POST_PRODUCTION_OPERATION_KEYWORDS: tuple[str, ...] = (
    "mutation", "migrate", "migration", "backfill", "sync", "reconcile", "cleanup",
    "deploy", "release", "cutover", "remediation", "upgrade", "downgrade", "reindex",
    "rewrite", "delete", "state change",
)

_POST_PRODUCTION_VERIFICATION_KEYWORDS: tuple[str, ...] = (
    "verify", "verification", "validate", "validation", "post-production", "post production",
    "proof-of-completion", "proof of completion", "outcome", "final state", "correctness",
    "closure", "replay", "source-of-truth", "source of truth",
)

_POST_PRODUCTION_LEGACY_KEYWORDS: tuple[str, ...] = (
    "pipeline", "etl", "collector",
)

_RETRIEVAL_MODES: set[str] = {
    "none",
    "relational-metadata",
    "lexical-index",
    "sparse-vector",
    "embedding-vector",
}


def _should_select_post_production_auditor(text: str) -> bool:
    """Return True when project text indicates outcome-verification needs.

    To reduce noisy auto-selection, require contextual co-occurrence:
    - at least one operation/change cue, and
    - at least one verification/proof cue.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")

    has_operation = any(_contains_keyword(text, kw) for kw in _POST_PRODUCTION_OPERATION_KEYWORDS)
    has_verification = any(_contains_keyword(text, kw) for kw in _POST_PRODUCTION_VERIFICATION_KEYWORDS)
    has_legacy = any(_contains_keyword(text, kw) for kw in _POST_PRODUCTION_LEGACY_KEYWORDS)

    return (has_operation and has_verification) or (has_legacy and has_verification)


def _contains_keyword(text: str, keyword: str) -> bool:
    """Return True if keyword appears as a standalone word (or its plural).

    Word-boundary matching avoids substring collisions such as matching "sync"
    inside "async", "doc" inside "docker", or "pip" inside "pipeline". A trailing
    English plural (-s/-es) is tolerated so "chapter" still matches "chapters" and
    "module" matches "modules" — a plural is the same word, whereas "docker" is
    not an inflection of "doc".
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(keyword, str):
        raise TypeError("keyword must be str")

    if not keyword:
        return False
    # Accept optional hyphen/space variants for phrase matching.
    normalized = re.escape(keyword).replace(r"\ ", r"[-\s]+")
    pattern = rf"(?<![a-z0-9]){normalized}(?:es|s)?(?![a-z0-9])"
    return re.search(pattern, text) is not None

#: Always-included governance agent slugs (tier 2)
GOVERNANCE_AGENTS = [
    "navigator",
    "security",
    "code-hygiene",
    "adversarial",
    "conflict-auditor",
    "conflict-resolution",
    "cleanup",
    "agent-updater",
    "agent-refactor",
    "repo-liaison",
    "git-operations",
]

#: Always-included operational domain agents (generated for every project)
ALWAYS_INCLUDED_DOMAIN_AGENTS = [
    "work-summarizer",
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_manifest(description: dict[str, Any], *, framework: str = "copilot-vscode") -> dict[str, Any]:
    """Build and return a team manifest from a normalized project description.

    Args:
        description: Normalized project description from ingest.load().
        framework:   Target agent framework ('copilot-vscode', 'copilot-cli', 'claude').

    Returns:
        Team manifest dict conforming to schemas/team-manifest.schema.json.
    """
    project_name = _resolve_project_name(description)
    project_goal = description["project_goal"]
    project_type = classify_project_type(description)
    # F8/F-2: propagate the operator's canonical project root (when given) so
    # downstream artifact emitters (e.g. memory-index) do not have to guess
    # from output_dir layout.
    existing_project_path = description.get("existing_project_path")

    # Core path fields
    primary_output_dir = description.get("primary_output_dir") or _default_primary_output_dir(project_type)
    build_output_dir = description.get("build_output_dir") or "build/"
    figures_dir = description.get("figures_dir") or "figures/"
    reference_db_path = description.get("reference_db_path") or _default_reference_db_path(description)
    style_reference_path = description.get("style_reference") or _default_style_reference_path(description)
    deliverable_type = _format_deliverable_type(description.get("deliverables", []), project_type)
    output_format = description.get("output_format") or _default_output_format(project_type)
    conversion_pipeline = description.get("conversion_pipeline")
    pip_package_name = description.get("pip_package_name")
    doc_site_config_file = description.get("doc_site_config_file")
    retrieval_integration = _normalize_retrieval_integration(description.get("retrieval_integration"))
    retrieval_enabled = retrieval_integration.get("mode", "none") != "none"

    # Archetype selection
    if "selected_archetypes" in description:
        archetypes = description["selected_archetypes"]
    else:
        archetypes = select_archetypes(description)

    if retrieval_enabled and "retrieval-integrator" not in archetypes:
        archetypes = list(archetypes) + ["retrieval-integrator"]

    # Tool agents
    tool_agents = detect_tool_agents(description.get("tools", []))

    # Reference-tier tools
    reference_tools = detect_reference_tools(description.get("tools", []))

    # Auto-include tool-doc-researcher when any tool has missing metadata
    if _has_unknown_tool_metadata(tool_agents, reference_tools):
        if "tool-doc-researcher" not in archetypes:
            archetypes = list(archetypes) + ["tool-doc-researcher"]

    # Archetype-implied agents: certain archetypes depend on specific domain agents
    # that may not be independently triggered by keyword matching.
    _ARCHETYPE_IMPLIES: dict[str, list[str]] = {
        "post-production-auditor": ["technical-validator"],
    }
    archetypes = list(archetypes)
    for archetype, implied in _ARCHETYPE_IMPLIES.items():
        if archetype in archetypes:
            for dep in implied:
                if dep not in archetypes:
                    archetypes.append(dep)

    # Components
    components = _normalize_components(description.get("components", []))

    # Authority hierarchy
    authority_hierarchy = build_authority_hierarchy(description)
    components = _enrich_component_sources(components, authority_hierarchy)

    # Workstream expert slugs
    workstream_expert_slugs = [f"{c['slug']}-expert" for c in components]

    # Tool docs are reference/skill documents, NOT agents — they are never
    # added to the orchestrator's `agents:` handoff roster. Including their
    # slugs here would make the orchestrator advertise handoffs to `@tool-*`
    # targets that no `.agent.md` backs, tripping audit AR_DANGLING_AGENT_SLUG.

    # All domain agent slugs (always-included + archetypes only)
    domain_agent_slugs = _dedupe_keep_order(
        ALWAYS_INCLUDED_DOMAIN_AGENTS + list(archetypes)
    )

    # Full slug list for orchestrator
    all_slugs = (
        ["orchestrator"]
        + GOVERNANCE_AGENTS
        + domain_agent_slugs
        + workstream_expert_slugs
    )

    # Auto-resolved placeholder map
    diagram_tools, diagram_extension = _derive_diagram_tools(description.get("tools", []))
    auto_resolved = _build_placeholder_map(
        project_name=project_name,
        project_goal=project_goal,
        primary_output_dir=primary_output_dir,
        build_output_dir=build_output_dir,
        figures_dir=figures_dir,
        reference_db_path=reference_db_path or "{MANUAL:REFERENCE_DB_PATH}",
        style_reference_path=style_reference_path or "{MANUAL:STYLE_REFERENCE_PATH}",
        deliverable_type=deliverable_type,
        output_format=output_format,
        conversion_pipeline=conversion_pipeline or "{MANUAL:CONVERSION_PIPELINE}",
        pip_package_name=pip_package_name or "{MANUAL:PIP_PACKAGE_NAME}",
        doc_site_config_file=doc_site_config_file or "{MANUAL:DOC_SITE_CONFIG_FILE}",
        authority_hierarchy=_format_authority_hierarchy(authority_hierarchy),
        authority_sources_list=_format_authority_sources_list(authority_hierarchy),
        reference_key_convention=description.get("reference_key_convention", "AuthorYear"),
        agent_slug_list=_format_agent_list(all_slugs),
        domain_agent_slugs=_format_agent_list(domain_agent_slugs),
        workstream_expert_slugs=_format_agent_list(workstream_expert_slugs),
        conflict_log_path=".github/agents/references/conflict-log.csv",
        workstream_source_map=_format_workstream_source_map(components),
        style_rules_summary=_format_style_rules(description.get("style_rules", [])),
        domain_agent_list=_format_domain_agent_list(ALWAYS_INCLUDED_DOMAIN_AGENTS + list(archetypes)),
        workstream_expert_list=_format_workstream_expert_list(components),
        diagram_tools=diagram_tools,
        diagram_extension=diagram_extension,
        component_slug="<component-slug>",
        unresolved_tool_list=_format_unresolved_tool_list(tool_agents, reference_tools, framework),
        retrieval_mode=retrieval_integration.get("mode", "none"),
        retrieval_query_entrypoints=_format_string_list(
            retrieval_integration.get("query_entrypoints", []),
            default="No retrieval query entrypoints declared."
        ),
        retrieval_maintenance_entrypoints=_format_string_list(
            retrieval_integration.get("maintenance_entrypoints", []),
            default="No retrieval maintenance entrypoints declared."
        ),
        retrieval_trigger_sources=_format_string_list(
            retrieval_integration.get("trigger_sources", []),
            default="manual"
        ),
        retrieval_source_of_truth=_format_string_list(
            retrieval_integration.get("source_of_truth", []),
            default="No retrieval source-of-truth declared."
        ),
        retrieval_staleness_slo_minutes=str(retrieval_integration.get("staleness_slo_minutes", 60)),
        retrieval_trigger_contract_version=retrieval_integration.get("trigger_contract_version", "v1"),
        security_data_generated_at="Not yet generated",
        security_source_registry=(
            "- CISA KEV (https://www.cisa.gov/known-exploited-vulnerabilities-catalog)\\n"
            "- MITRE CVE (https://cve.org/)\\n"
            "- FIRST EPSS (https://www.first.org/epss/)"
        ),
        security_current_threats_summary=(
            "- Live vulnerability snapshot will be generated during team initialization/update."
        ),
        security_prevention_playbook=(
            "- Patch KEV-listed CVEs first based on active exploitation evidence.\\n"
            "- Prioritize high EPSS vulnerabilities for rapid mitigation.\\n"
            "- Validate compensating controls when patching is delayed."
        ),
        security_vulnerability_watch_json=(
            '{"generated_at":"","sources":[],"vulnerabilities":[],"notes":"Generated during initialization/update."}'
        ),
        security_llm_threats_summary=(
            "- LLM threat intelligence will be generated during team initialization/update.\n"
            "  Reference: https://owasp.org/www-project-top-10-for-large-language-model-applications/"
        ),
        security_osv_packages_summary=(
            "- Package-level vulnerability data will be generated during team initialization/update."
        ),
    )

    # Post-production placeholder requirements are only relevant when the
    # post-production-auditor archetype is selected.
    if "post-production-auditor" in archetypes:
        auto_resolved.update(
            _build_placeholder_map(
                trigger_contract_version="{MANUAL:TRIGGER_CONTRACT_VERSION}",
                bulk_mutation_threshold="{MANUAL:BULK_MUTATION_THRESHOLD}",
                source_of_truth_spec="{MANUAL:SOURCE_OF_TRUTH_SPEC}",
                duplicate_cluster_cap="{MANUAL:DUPLICATE_CLUSTER_CAP}",
                audit_slug="{MANUAL:AUDIT_SLUG}",
            )
        )

    # Manual-required placeholders (unfilled MANUAL tokens)
    manual_required = _collect_manual_required(auto_resolved)

    # Also flag MANUAL tokens embedded in structured authority source paths
    # (these won't be in auto_resolved as top-level keys, so fullmatch won't catch them)
    _seen_manual = {item["placeholder"] for item in manual_required}
    for src in authority_hierarchy:
        path = src.get("path", "")
        m = _MANUAL_TOKEN_FULLMATCH_RE.fullmatch(path.strip())
        if m:
            token = m.group(1)
            if token not in _seen_manual:
                _seen_manual.add(token)
                manual_required.append({
                    "placeholder": token,
                    "agent_file": "multiple",
                    "context": (
                        f"Authority source '{src['name']}' has an unresolved path. "
                        f"Replace {{{{MANUAL:{token}}}}} with the actual path."
                    ),
                    "suggestion": f"Provide the path to '{src['name']}'.",
                })

    manual_required.extend(_collect_tool_metadata_manual_required(tool_agents, reference_tools, framework))
    manual_required.extend(_collect_component_manual_required(components))

    # Output file plan
    output_files = _plan_output_files(archetypes, tool_agents, reference_tools, components, framework)

    manifest = {
        "schema_version": "1.0",
        "project_name": project_name,
        "project_goal": project_goal,
        "project_type": project_type,
        "framework": framework,
        "existing_project_path": existing_project_path,
        "primary_output_dir": primary_output_dir,
        "build_output_dir": build_output_dir,
        "figures_dir": figures_dir,
        "reference_db_path": reference_db_path,
        "style_reference_path": style_reference_path,
        "deliverable_type": deliverable_type,
        "output_format": output_format,
        "conversion_pipeline": conversion_pipeline,
        "retrieval_trigger_contract_version": retrieval_integration.get("trigger_contract_version", "v1"),
        "retrieval_integration": retrieval_integration,
        "selected_archetypes": archetypes,
        "tool_agents": tool_agents,
        "reference_tools": reference_tools,
        "components": components,
        "authority_hierarchy": authority_hierarchy,
        "agent_slug_list": all_slugs,
        "domain_agent_slugs": domain_agent_slugs,
        "workstream_expert_slugs": workstream_expert_slugs,
        "governance_agents": GOVERNANCE_AGENTS,
        "auto_resolved_placeholders": auto_resolved,
        "manual_required_placeholders": manual_required,
        "output_files": output_files,
        # W22: consumer-declared extra index source paths/globs. Read directly
        # from description so _memory_index_sources can see it without depending
        # on the description being re-attached later in the update path.
        "memory_index_extra_dirs": description.get("memory_index_extra_dirs") or [],
    }
    # MCP-suitability detection (report §5). Advisory only — populated solely
    # when the description declares mcp_hints, so manifests for projects without
    # MCP integrations are unchanged. Never auto-provisions a server.
    mcp_candidates = detect_mcp_candidates(description)
    if mcp_candidates:
        manifest["mcp_candidates"] = [c.to_manifest_entry() for c in mcp_candidates]
    return manifest


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_project_type(description: dict[str, Any]) -> str:
    """Return a project type string based on keyword analysis."""
    text = _description_text(description).lower()

    writing_kw = {"chapter", "essay", "paper", "book", "manuscript", "article", "thesis", "report"}
    software_kw = {"module", "function", "api", "class", "python", "javascript", "rust", "go", "java", "code"}
    data_kw = {"pipeline", "dataset", "csv", "etl", "dataframe", "sql", "database", "transform"}
    research_kw = {"research", "hypothesis", "experiment", "citation", "bibliography", "academic", "study"}
    doc_kw = {"documentation", "docs", "readme", "wiki", "manual", "guide"}

    # Word-boundary matching (not bare substring `in`) so short keywords like
    # "go"/"api" don't spuriously match inside "mango"/"rapid" and misclassify.
    scores = {
        "writing": sum(1 for kw in writing_kw if _contains_keyword(text, kw)),
        "software": sum(1 for kw in software_kw if _contains_keyword(text, kw)),
        "data-pipeline": sum(1 for kw in data_kw if _contains_keyword(text, kw)),
        "research": sum(1 for kw in research_kw if _contains_keyword(text, kw)),
        "documentation": sum(1 for kw in doc_kw if _contains_keyword(text, kw)),
    }

    if all(v == 0 for v in scores.values()):
        return "unknown"

    top = max(scores, key=lambda k: scores[k])

    # Mixed: if two types both score >= 2
    sorted_scores = sorted(scores.values(), reverse=True)
    if sorted_scores[0] >= 2 and sorted_scores[1] >= 2:
        return "mixed"

    return top


# ---------------------------------------------------------------------------
# Archetype selection
# ---------------------------------------------------------------------------

def select_archetypes(description: dict[str, Any]) -> list[str]:
    """Auto-select domain archetypes from the project description."""
    text = _description_text(description).lower()
    selected: list[str] = []
    seen: set[str] = set()

    for keywords, archetype in _ARCHETYPE_TRIGGERS:
        if archetype in seen:
            continue
        # Word-boundary matching to honor the documented "boundary-aware" contract
        # (e.g. trigger "doc" must not match inside "docker", "pip" inside "pipeline").
        if keywords == ["*"] or any(_contains_keyword(text, kw) for kw in keywords):
            selected.append(archetype)
            seen.add(archetype)

    # post-production-auditor: outcome-verification trigger for any domain,
    # guarded by contextual co-occurrence to avoid broad false positives.
    if "post-production-auditor" not in seen and _should_select_post_production_auditor(text):
        selected.append("post-production-auditor")
        seen.add("post-production-auditor")

    # Always include primary-producer and quality-auditor
    for required in ("primary-producer", "quality-auditor"):
        if required not in seen:
            selected.append(required)

    return selected


# ---------------------------------------------------------------------------
# Tool importance classification
# ---------------------------------------------------------------------------

#: Categories that automatically qualify for a specialist agent
_SPECIALIST_CATEGORIES: set[str] = {"database", "cli", "build-system"}

#: Tool names (lowercased) that always qualify as specialist-tier
_SPECIALIST_TOOLS: set[str] = {
    "postgresql", "postgres", "mysql", "mariadb", "mongodb", "redis",
    "elasticsearch", "cassandra", "sqlite",
    "docker", "docker compose", "kubernetes", "k8s", "terraform",
    "ansible", "pulumi",
    "github actions", "jenkins", "circleci", "gitlab ci",
    "nginx", "apache", "caddy",
}

#: Categories that qualify for a reference file (lightweight docs)
_REFERENCE_CATEGORIES: set[str] = {"framework", "library"}

#: Tool names (lowercased) that always qualify as reference-tier
_REFERENCE_TOOLS: set[str] = {
    "fastapi", "django", "flask", "express", "react", "vue", "angular",
    "sqlalchemy", "pandas", "numpy", "scipy", "matplotlib",
    "spring", "rails", "laravel", "nextjs", "next.js",
    "pytest", "jest", "mocha", "junit",
    "graphql", "grpc", "protobuf",
}

_KNOWN_TOOL_METADATA: dict[str, dict[str, str]] = {
    "jupyter": {
        "docs_url": "https://docs.jupyter.org/en/latest/",
        "api_surface": "Notebook and Lab workflows, kernels, markdown cells, magics, nbconvert",
        "common_patterns": "Keep notebooks reproducible: restart and run all, move reusable logic into modules, and avoid hidden state between cells.",
    },
    "pandas": {
        "docs_url": "https://pandas.pydata.org/docs/",
        "api_surface": "DataFrame, Series, read_csv, merge, groupby, pivot_table",
        "common_patterns": "Prefer vectorized operations, explicit dtypes, and merge or groupby pipelines over row-wise apply when possible.",
    },
    "numpy": {
        "docs_url": "https://numpy.org/doc/stable/",
        "api_surface": "ndarray, array, arange, where, concatenate, linalg",
        "common_patterns": "Use array operations and broadcasting instead of Python loops where possible, and make shape assumptions explicit.",
    },
    "matplotlib": {
        "docs_url": "https://matplotlib.org/stable/contents.html",
        "api_surface": "pyplot.figure, pyplot.subplots, Axes.plot, Axes.bar, savefig",
        "common_patterns": "Create figures and axes explicitly, label every chart, and save deterministic output paths for reproducibility.",
    },
    "plotly": {
        "docs_url": "https://plotly.com/python/",
        "api_surface": "plotly.express, graph_objects.Figure, update_layout, write_html",
        "common_patterns": "Prefer self-contained HTML exports, centralize layout styling, and validate hover labels and axis formatting before publication.",
    },
    "pandasdatareader": {
        "docs_url": "https://pydata.github.io/pandas-datareader/",
        "api_surface": "data.DataReader, fred.FredReader, wb.download",
        "common_patterns": "Cache downloaded data for reproducibility, document provider-specific limits, and normalize index frequency immediately after fetch.",
    },
    "linearmodels": {
        "docs_url": "https://bashtage.github.io/linearmodels/",
        "api_surface": "PanelOLS, PooledOLS, RandomEffects, fit",
        "common_patterns": "Make panel indexes explicit, state fixed-effects choices clearly, and inspect fit summaries before exporting results.",
    },
    "sqlite": {
        "docs_url": "https://www.sqlite.org/docs.html",
        "api_surface": "sqlite3 CLI, CREATE TABLE, CREATE INDEX, PRAGMA, EXPLAIN QUERY PLAN",
        "common_patterns": "Use parameterized queries, explicit transactions, and indexes validated with EXPLAIN QUERY PLAN.",
    },
    "nmap": {
        "docs_url": "https://nmap.org/book/man.html",
        "api_surface": "nmap -sn (ping scan), -sV (version detection), -O (OS detection), -p (port range), --script (NSE), -oX (XML output), -oG (greppable output)",
        "common_patterns": "Use -sn for fast host discovery, parse XML output programmatically, and always run -O and -sS with sudo. Use --host-timeout to avoid hangs on unreachable hosts.",
    },
    "arpscan": {
        "docs_url": "https://github.com/royhills/arp-scan/wiki",
        "api_surface": "arp-scan --localnet, --interface=<iface>, --retry=<n>, --timeout=<ms>; output: IP, MAC, vendor",
        "common_patterns": "Always run with sudo. Combine with vendor OUI lookup for device classification. Check for DUP lines which may indicate ARP spoofing.",
    },
    "ssh": {
        "docs_url": "https://www.openssh.com/manual.html",
        "api_surface": "ssh user@host, -L (local forward), -R (remote forward), -N -f (background), -o options, ssh-keygen, ssh-copy-id, scp, sftp",
        "common_patterns": "Use autossh or ServerAliveInterval for persistent tunnels. Use -N -f for background port-only tunnels. Manage known_hosts explicitly in production.",
    },
    "d3js": {
        "docs_url": "https://d3js.org/api",
        "api_surface": "d3.select/selectAll, selection.data().enter().exit(), scales (scaleLinear/scaleBand/scaleTime), axes, line/area/arc generators, d3.zoom/drag, d3.transition",
        "common_patterns": "Use the update-enter-exit (or join()) pattern for dynamic data. Use viewBox for responsive sizing. Note: D3 v6+ uses event parameter in callbacks — d3.event is removed.",
    },
    "paramiko": {
        "docs_url": "https://docs.paramiko.org/en/stable/api/",
        "api_surface": "SSHClient.connect/exec_command/invoke_shell/open_sftp, Transport.request_port_forward, SFTPClient.get/put, RSAKey/Ed25519Key.from_private_key_file",
        "common_patterns": "Use AutoAddPolicy only in trusted environments; prefer RejectPolicy in production. Always close connections. Set timeout= on connect() to avoid hangs on unreachable hosts.",
    },
}


def classify_tool_importance(tool: dict[str, Any]) -> str:
    """Classify a tool into an importance tier.

    Args:
        tool: Tool dict with at least 'name', optionally 'category' and
              'needs_specialist_agent'.

    Returns:
        One of 'specialist', 'reference', or 'passive'.
    """
    # An explicit `needs_specialist_agent: true` forces the specialist tier.
    # `false` (and absence) fall back to the category/name heuristics below — a
    # `false` value does not force a non-specialist tier.
    if tool.get("needs_specialist_agent") is True:
        return "specialist"

    return _classify_without_override(tool)


def _classify_without_override(tool: dict[str, Any]) -> str:
    """Classify a tool by its category and name when no explicit override."""
    name_lower = (tool.get("name") or "").lower()
    category = tool.get("category", "other")

    # Specialist tier: databases, infra, CI, build-systems
    if category in _SPECIALIST_CATEGORIES or name_lower in _SPECIALIST_TOOLS:
        return "specialist"

    # Reference tier: frameworks, libraries
    if category in _REFERENCE_CATEGORIES or name_lower in _REFERENCE_TOOLS:
        return "reference"

    return "passive"


def _normalize_tool_key(name: str) -> str:
    """Normalize a tool name into a lookup key for built-in metadata."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _merge_known_tool_metadata(tool: dict[str, Any]) -> dict[str, Any]:
    """Overlay built-in tool metadata when the brief omits it."""
    merged = dict(tool)
    defaults = _KNOWN_TOOL_METADATA.get(_normalize_tool_key(tool.get("name", "")), {})
    for field in ("docs_url", "api_surface", "common_patterns"):
        if not merged.get(field) and defaults.get(field):
            merged[field] = defaults[field]
    return merged


# ---------------------------------------------------------------------------
# Tool doc detection
#
# NOTE: tools are never generated as agents. `detect_tool_agents` returns specs
# for *operational tool documents* (Claude skills / Copilot reference docs).
# The historical name and the `tool_agents` manifest key are retained for
# backward compatibility (closed manifest schema, external consumers); the
# OUTPUT is a doc, not an `.agent.md`. See `_plan_output_files`.
# ---------------------------------------------------------------------------

def detect_tool_agents(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return operational tool-doc specs for specialist-tier tools.

    Specialist-tier tools (databases, CLIs, build systems, infra) become
    operational documents — Claude skills or Copilot reference docs — never
    agents. The spec slug stays ``tool-<name>`` to identify the tool.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        List of tool-doc spec dicts for specialist-tier tools.
    """
    agents = []
    for tool in tools:
        tool = _merge_known_tool_metadata(tool)
        tier = classify_tool_importance(tool)
        if tier != "specialist":
            continue
        # A category-classified tool may lack a name; .get is used in the tier
        # check above, so read it tolerantly here too rather than KeyError.
        name = (tool.get("name") or "").strip()
        if not name:
            continue
        slug = f"tool-{_slugify(name)}"
        category = tool.get("category", "other")
        agents.append({
            "slug": slug,
            "tool_name": name,
            "tool_version": tool.get("version", ""),
            "tool_category": category,
            "config_files": tool.get("config_files", []),
            "invocation_command": "",
            "invocation_target": "",
            "docs_url": tool.get("docs_url", ""),
            "api_surface": tool.get("api_surface", ""),
            "common_patterns": tool.get("common_patterns", ""),
        })
    return agents


def detect_reference_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tool specs for tools classified as reference-tier.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        List of tool dicts for reference-tier tools.
    """
    refs = []
    for tool in tools:
        tool = _merge_known_tool_metadata(tool)
        tier = classify_tool_importance(tool)
        if tier != "reference":
            continue
        name = (tool.get("name") or "").strip()
        if not name:
            continue
        refs.append({
            "slug": f"ref-{_slugify(name)}",
            "tool_name": name,
            "tool_version": tool.get("version", ""),
            "tool_category": tool.get("category", "other"),
            "config_files": tool.get("config_files", []),
            "docs_url": tool.get("docs_url", ""),
            "api_surface": tool.get("api_surface", ""),
            "common_patterns": tool.get("common_patterns", ""),
        })
    return refs


# ---------------------------------------------------------------------------
# Authority hierarchy
# ---------------------------------------------------------------------------

def build_authority_hierarchy(description: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an ordered authority hierarchy from description."""
    sources = description.get("authority_sources", [])

    # Normalize ranks
    hierarchy: list[dict[str, Any]] = []
    for i, src in enumerate(sources, start=1):
        # Accept both plain strings and dicts
        if isinstance(src, str):
            src = {"path": src, "name": src}
        hierarchy.append({
            "rank": src.get("rank", i),
            "name": src.get("name", f"Source {i}"),
            "path": src.get("path", ""),
            "scope": src.get("scope", "general"),
        })

    hierarchy.sort(key=lambda x: x["rank"])
    return hierarchy


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each component has all required fields."""
    normalized = []
    for i, comp in enumerate(components, start=1):
        normalized.append({
            "slug": comp.get("slug", f"component-{i:02d}"),
            "name": comp.get("name", f"Component {i}"),
            "number": comp.get("number", i),
            "output_file": comp.get("output_file", ""),
            "description": comp.get("description", ""),
            "sections": comp.get("sections", []),
            "sources": comp.get("sources", []),
            "cross_refs": comp.get("cross_refs", []),
            "quality_criteria": comp.get("quality_criteria", []),
            "tools": comp.get("tools", []),
        })
    return normalized


def _enrich_component_sources(
    components: list[dict[str, Any]],
    authority_hierarchy: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backfill component source lists from component outputs and authority paths."""
    enriched: list[dict[str, Any]] = []
    for component in components:
        updated = dict(component)
        if not updated.get("sources"):
            updated["sources"] = _infer_component_sources(updated, authority_hierarchy)
        enriched.append(updated)
    return enriched


def _infer_component_sources(
    component: dict[str, Any],
    authority_hierarchy: list[dict[str, Any]],
) -> list[str]:
    """Infer a reasonable source list for a component when the brief omits one."""
    sources: list[str] = []
    output_file = (component.get("output_file") or "").strip()
    if output_file:
        sources.append(output_file)

    component_keys = {
        _normalize_match_key(component.get("slug", "")),
        _normalize_match_key(component.get("name", "")),
        _normalize_match_key(output_file),
        _normalize_match_key(output_file.split("/", 1)[0] if output_file else ""),
    }
    component_keys.discard("")

    for source in authority_hierarchy:
        path = (source.get("path") or "").strip()
        if not path:
            continue
        path_root = path.split("/", 1)[0]
        path_keys = {
            _normalize_match_key(path),
            _normalize_match_key(path_root),
            _normalize_match_key(source.get("name", "")),
        }
        path_keys.discard("")
        if output_file and (path == output_file or path.startswith(output_file.rstrip("/") + "/")):
            sources.append(path)
            continue
        if path_root and output_file.startswith(path_root):
            sources.append(path)
            continue
        if component_keys and any(
            comp_key in path_key or path_key in comp_key
            for comp_key in component_keys
            for path_key in path_keys
        ):
            sources.append(path)

    return _dedupe_preserve_order(sources)


def _normalize_match_key(text: str) -> str:
    """Normalize text for loose path/name matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    """Return values in first-seen order with duplicates removed."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _resolve_project_name(description: dict[str, Any]) -> str:
    name = description.get("project_name", "")
    if not name and description.get("existing_project_path"):
        name = Path(description["existing_project_path"]).name
    return name or "MyProject"


def _default_reference_db_path(description: dict) -> str | None:
    """Infer a sensible `reference_db_path` for projects with a doc site.

    Triggers only when the descriptor declares a `doc_site_config_file`
    AND a `docs/` directory exists on disk at the project root. Returns
    None otherwise so the manual-placeholder fallback is preserved.
    Plan: references/plans/F1-self-team-manual-placeholders-2026-05-25.md
    """
    if not description.get("doc_site_config_file"):
        return None
    project_path = description.get("existing_project_path")
    if not project_path:
        return None
    from pathlib import Path as _Path

    if (_Path(project_path) / "docs").is_dir():
        return "docs/"
    return None


def _default_style_reference_path(description: dict) -> str | None:
    """Infer a sensible `style_reference_path` for doc-site projects.

    Prefers `docs_src/` (the mkdocs convention used in agentteams),
    falls back to `docs/` if `docs_src/` is absent. None otherwise.
    """
    if not description.get("doc_site_config_file"):
        return None
    project_path = description.get("existing_project_path")
    if not project_path:
        return None
    from pathlib import Path as _Path

    root = _Path(project_path)
    if (root / "docs_src").is_dir():
        return "docs_src/"
    if (root / "docs").is_dir():
        return "docs/"
    return None


def _default_primary_output_dir(project_type: str) -> str:
    return {
        "writing": "docs/",
        "software": "src/",
        "data-pipeline": "src/",
        "research": "docs/",
        "documentation": "docs/",
        "mixed": "src/",
        "unknown": "src/",
    }.get(project_type, "src/")


def _default_output_format(project_type: str) -> str:
    return {
        "writing": "PDF",
        "software": "Python modules",
        "data-pipeline": "CSV",
        "research": "PDF",
        "documentation": "HTML",
        "mixed": "HTML",
        "unknown": "plain text",
    }.get(project_type, "plain text")


def _format_deliverable_type(deliverables: list[str], project_type: str) -> str:
    if not deliverables:
        return _default_output_format(project_type)
    if len(deliverables) == 1:
        return deliverables[0]
    return ", ".join(deliverables[:-1]) + " and " + deliverables[-1]


# ---------------------------------------------------------------------------
# Placeholder builders
# ---------------------------------------------------------------------------

def _build_placeholder_map(**kwargs: str) -> dict[str, str]:
    """Build the auto_resolved_placeholders dict from keyword arguments."""
    mapping: dict[str, str] = {}
    for key, val in kwargs.items():
        placeholder = key.upper()
        mapping[placeholder] = val
    return mapping


def _format_authority_hierarchy(hierarchy: list[dict[str, Any]]) -> str:
    if not hierarchy:
        return "1. **Project source files** — ground truth for all technical claims"
    lines = []
    for src in hierarchy:
        scope = f" — {src['scope']}" if src.get("scope") else ""
        lines.append(f"{src['rank']}. **{src['name']}** (`{src['path']}`){scope}")
    return "\n".join(lines)


def _format_authority_sources_list(hierarchy: list[dict[str, Any]]) -> str:
    if not hierarchy:
        return "- Project source files (read-only)"
    return "\n".join(f"- `{src['path']}` — {src.get('scope', 'general')}" for src in hierarchy)


def _format_agent_list(slugs: list[str]) -> str:
    if not slugs:
        return "[]"
    formatted = ["  - " + s for s in slugs]
    return "\n" + "\n".join(formatted)


def adopt_orphan_agents(manifest: dict[str, Any], orphan_slugs: list[str]) -> list[str]:
    """Register pre-existing ("orphan") agent files into the team roster.

    Adds each orphan slug to ``agent_slug_list`` and ``domain_agent_slugs`` (so
    the orchestrator declares them as handoff targets) and re-renders the
    matching placeholders. Records them under ``adopted_agents``. Deliberately
    does NOT touch ``output_files`` — the adopted agents' own files are never
    generated or overwritten, preserving their bespoke content.

    Returns the slugs newly adopted (those not already in the roster).
    """
    existing = set(manifest.get("agent_slug_list", []))
    newly = [s for s in orphan_slugs if s and s not in existing]
    if not newly:
        return []
    # Add to the handoff roster only. Deliberately NOT added to
    # domain_agent_slugs: bespoke adopted agents are not standard domain
    # archetypes and carry no auto-generated routing/trigger metadata, so
    # folding them into the domain-routing placeholder would mislabel them.
    # _get_team_slugs unions adopted_agents so the adapter keeps them in the
    # orchestrator's agents:/handoffs: lists regardless.
    manifest["agent_slug_list"] = _dedupe_keep_order(
        list(manifest.get("agent_slug_list", [])) + newly
    )
    manifest["adopted_agents"] = _dedupe_keep_order(
        list(manifest.get("adopted_agents", [])) + newly
    )
    placeholders = manifest.setdefault("auto_resolved_placeholders", {})
    # Placeholder key is UPPERCASE (resolve_placeholders matches {AGENT_SLUG_LIST}).
    placeholders["AGENT_SLUG_LIST"] = _format_agent_list(manifest["agent_slug_list"])
    return newly


def _format_workstream_source_map(components: list[dict[str, Any]]) -> str:
    if not components:
        return "No components defined."
    lines = []
    for comp in components:
        output = comp.get("output_file") or "TBD"
        lines.append(f"- `{comp['slug']}` → `{output}`")
    return "\n".join(lines)


def _format_style_rules(rules: list[str]) -> str:
    if not rules:
        return "No project-specific style rules defined."
    return "\n".join(f"- {r}" for r in rules)


def _format_string_list(values: list[str], *, default: str) -> str:
    """Format a plain Markdown bullet list or return a default string."""
    cleaned = [v for v in values if v]
    if not cleaned:
        return default
    return "\n".join(f"- {v}" for v in cleaned)


_DIAGRAM_TOOL_MAP: dict[str, tuple[str, str]] = {
    # tool-name-lower → (display-name, file-extension)
    "mermaid": ("Mermaid", "mmd"),
    "graphviz": ("Graphviz/DOT", "dot"),
    "dot": ("Graphviz/DOT", "dot"),
    "plantuml": ("PlantUML", "puml"),
    "d2": ("D2", "d2"),
    "drawio": ("Draw.io", "drawio"),
}


def _derive_diagram_tools(tools: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (diagram_tools_display, diagram_extension) from the tools list.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        Tuple of (display name string, file extension string).
    """
    for tool in tools:
        name_lower = tool.get("name", "").lower()
        if name_lower in _DIAGRAM_TOOL_MAP:
            display, ext = _DIAGRAM_TOOL_MAP[name_lower]
            return display, ext
    # Default when no diagram tool is explicitly listed
    return "Mermaid or Graphviz/DOT", "mmd"


def _format_domain_agent_list(agent_slugs: list[str]) -> str:
    lines = []
    descriptions = {
        "work-summarizer": "synthesizes daily/weekly/monthly work summaries from plan artifacts and git history",
        "primary-producer": "drafts and revises primary deliverables",
        "quality-auditor": "read-only structural and prose quality audit",
        "cohesion-repairer": "repairs within-section cohesion failures",
        "style-guardian": "enforces voice and style fidelity",
        "technical-validator": "verifies technical accuracy against authority sources",
        "retrieval-integrator": "validates retrieval query, maintenance, and trigger contracts",
        "format-converter": "converts deliverables to final output format",
        "reference-manager": "manages the reference/bibliography database",
        "output-compiler": "assembles components into the final deliverable package",
        "visual-designer": "creates and revises diagrams and figures",
    }
    for slug in _dedupe_keep_order(agent_slugs):
        desc = descriptions.get(slug, "specialized domain agent")
        lines.append(f"- `@{slug}` — {desc}")
    return "\n".join(lines) if lines else "No domain agents selected."


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _format_workstream_expert_list(components: list[dict[str, Any]]) -> str:
    lines = []
    for comp in components:
        lines.append(f"- `@{comp['slug']}-expert` — {comp['name']}")
    return "\n".join(lines) if lines else "No workstream experts defined."


_MANUAL_TOKEN_FULLMATCH_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")


def _collect_manual_required(placeholder_map: dict[str, str]) -> list[dict[str, str]]:
    """Identify placeholders whose resolved value is itself a {MANUAL:...} token.

    Uses fullmatch to avoid false positives from composed values (e.g.
    authority hierarchy entries) whose text happens to contain MANUAL tokens
    as embedded documentation or path references.
    """
    manual = []
    for placeholder, value in placeholder_map.items():
        if value is None:
            continue
        if _MANUAL_TOKEN_FULLMATCH_RE.fullmatch(value.strip()):
            manual.append({
                "placeholder": placeholder,
                "agent_file": "multiple",
                "context": f"The placeholder {{{placeholder}}} could not be auto-resolved.",
                "suggestion": "",
            })
    return manual


def _collect_tool_metadata_manual_required(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
    framework: str = "copilot-vscode",
) -> list[dict[str, str]]:
    """Return setup items for tool metadata fields still missing after enrichment."""
    manual: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    field_specs = (
        ("docs_url", "TOOL_DOCS_URL", "official documentation URL"),
        ("api_surface", "TOOL_API_SURFACE", "project-relevant API surface summary"),
        ("common_patterns", "TOOL_COMMON_PATTERNS", "tool-specific usage patterns and pitfalls"),
    )

    def add_items(specs: list[dict[str, Any]], *, reference: bool) -> None:
        for spec in specs:
            if reference:
                rel_path = f"references/{spec['slug']}-reference.md"
                doc_label = "reference file"
            else:
                # Operational tool doc — a skill (Claude) or reference doc (Copilot),
                # never an agent. Point setup items at the actual emitted path.
                slug = spec["slug"]
                base = slug[len("tool-"):] if slug.startswith("tool-") else slug
                if framework == "claude":
                    rel_path = f"../skills/{slug}.md"
                    doc_label = "skill document"
                else:
                    rel_path = f"references/ref-{base}-reference.md"
                    doc_label = "reference document"

            for field_name, placeholder, description in field_specs:
                if spec.get(field_name):
                    continue
                dedupe_key = (rel_path, placeholder)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                manual.append({
                    "placeholder": placeholder,
                    "agent_file": rel_path,
                    "context": (
                        f"The {doc_label} for '{spec['tool_name']}' is missing a {description}. "
                        "Add it to the tools[] entry in the project brief or extend the built-in tool metadata catalog."
                    ),
                    "suggestion": "",
                })

    add_items(tool_agents, reference=False)
    add_items(reference_tools, reference=True)
    return manual


def _collect_component_manual_required(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return setup items for unresolved per-component placeholders."""
    manual: list[dict[str, str]] = []
    for component in components:
        rel_path = f"{component['slug']}-expert.agent.md"
        field_specs = (
            ("description", "COMPONENT_SPEC", "component specification"),
            ("sections", "COMPONENT_SECTIONS", "section outline"),
            ("sources", "COMPONENT_SOURCES", "source list"),
            ("quality_criteria", "COMPONENT_QUALITY_CRITERIA", "quality criteria list"),
        )
        for field_name, placeholder, label in field_specs:
            value = component.get(field_name)
            if value:
                continue
            manual.append({
                "placeholder": placeholder,
                "agent_file": rel_path,
                "context": (
                    f"Component '{component['name']}' is missing its {label}. "
                    "Add the field to the component entry in the project brief so the expert brief renders completely."
                ),
                "suggestion": "",
            })
    return manual


def _description_text(description: dict[str, Any]) -> str:
    """Return a single string for keyword analysis."""
    parts = [
        description.get("project_goal", ""),
        description.get("project_name", ""),
        description.get("output_format", ""),
        " ".join(description.get("deliverables", [])),
        " ".join(c.get("description", "") for c in description.get("components", [])),
    ]
    # Include tool names, categories, and versions for richer keyword matching
    for t in description.get("tools", []):
        parts.append(t.get("name", ""))
        parts.append(t.get("category", ""))
        parts.append(t.get("version", ""))
    retrieval = description.get("retrieval_integration", {})
    if isinstance(retrieval, dict):
        parts.append(retrieval.get("mode", ""))
        parts.extend(retrieval.get("query_entrypoints", []))
        parts.extend(retrieval.get("maintenance_entrypoints", []))
        parts.extend(retrieval.get("trigger_sources", []))
    return " ".join(parts)


def _normalize_retrieval_integration(raw: Any) -> dict[str, Any]:
    """Normalize retrieval integration contract to a stable shape."""
    if not isinstance(raw, dict):
        return {
            "mode": "none",
            "query_entrypoints": [],
            "maintenance_entrypoints": [],
            "trigger_sources": ["manual"],
            "source_of_truth": [],
            "staleness_slo_minutes": 60,
            "trigger_contract_version": "v1",
        }

    mode = str(raw.get("mode", "none")).strip().lower()
    if mode not in _RETRIEVAL_MODES:
        mode = "none"

    def _list(name: str) -> list[str]:
        values = raw.get(name, [])
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if str(v).strip()]

    trigger_sources = _list("trigger_sources")
    if not trigger_sources:
        trigger_sources = ["manual"]

    try:
        staleness = int(raw.get("staleness_slo_minutes", 60) or 60)
    except (TypeError, ValueError):
        staleness = 60

    return {
        "mode": mode,
        "query_entrypoints": _list("query_entrypoints"),
        "maintenance_entrypoints": _list("maintenance_entrypoints"),
        "trigger_sources": trigger_sources,
        "source_of_truth": _list("source_of_truth"),
        "staleness_slo_minutes": staleness,
        "trigger_contract_version": str(raw.get("trigger_contract_version", "v1") or "v1"),
    }


def _has_unknown_tool_metadata(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
) -> bool:
    """Return True if any tool is missing docs_url, api_surface, or common_patterns.

    Args:
        tool_agents:    Specialist-tier tool agent specs from detect_tool_agents().
        reference_tools: Reference-tier tool specs from detect_reference_tools().

    Returns:
        True when at least one tool has an incomplete metadata set.
    """
    fields = ("docs_url", "api_surface", "common_patterns")
    for spec in list(tool_agents) + list(reference_tools):
        if any(not spec.get(f) for f in fields):
            return True
    return False


def _format_unresolved_tool_list(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
    framework: str = "copilot-vscode",
) -> str:
    """Format a Markdown bullet list of tools with missing documentation metadata.

    Args:
        tool_agents:    Operational tool-doc specs from detect_tool_agents().
        reference_tools: Reference-tier tool specs from detect_reference_tools().
        framework:      Target framework — determines the doc path shown.

    Returns:
        Markdown string listing each tool with its missing fields, or a 'none'
        message when all tools are fully resolved.
    """
    fields_checked = (("docs_url", "docs URL"), ("api_surface", "API surface"), ("common_patterns", "usage patterns"))
    lines: list[str] = []

    for spec in tool_agents:
        gaps = [label for field, label in fields_checked if not spec.get(field)]
        if gaps:
            slug = spec["slug"]
            base = slug[len("tool-"):] if slug.startswith("tool-") else slug
            if framework == "claude":
                doc_ref = f"skill `.claude/skills/{slug}.md`"
            else:
                doc_ref = f"reference doc `references/ref-{base}-reference.md`"
            lines.append(
                f"- **{spec['tool_name']}** ({doc_ref}) "
                f"— missing: {', '.join(gaps)}"
            )

    for spec in reference_tools:
        gaps = [label for field, label in fields_checked if not spec.get(field)]
        if gaps:
            ref_path = f"references/{spec['slug']}-reference.md"
            lines.append(
                f"- **{spec['tool_name']}** (reference file `{ref_path}`) "
                f"— missing: {', '.join(gaps)}"
            )

    return "\n".join(lines) if lines else "No tools with missing metadata."
