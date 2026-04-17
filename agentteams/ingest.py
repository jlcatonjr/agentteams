"""
ingest.py — Parse project descriptions into a normalized dict.

Accepts:
  - JSON file matching project-description.schema.json
  - Markdown file with section headings as a structured brief
  - Plain Markdown fallback (unstructured)

Mode B: If existing_project_path is set, scan the directory tree to
supplement missing fields (directory structure, tools, README content).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agentteams._utils import _slugify


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load(source: str | Path, *, scan_project: bool = True) -> dict[str, Any]:
    """Load and return a normalized project description dict.

    Args:
        source:       Path to a .json or .md project description file.
        scan_project: If True and existing_project_path is set in the
                      description, scan the project directory for additional
                      context.

    Returns:
        Normalized project description dict conforming to
        schemas/project-description.schema.json.

    Raises:
        FileNotFoundError: If source does not exist.
        ValueError:        If source cannot be parsed or fails validation.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Description file not found: {path}")

    if path.suffix == ".json":
        description = _load_json(path)
    elif path.suffix in {".md", ".txt", ""}:
        description = _load_markdown(path)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix!r} (expected .json or .md)")

    _validate_required_fields(description)

    if scan_project:
        project_path = description.get("existing_project_path")
        if project_path:
            description = _supplement_from_directory(description, Path(project_path))

    return description


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at top level, got {type(data).__name__}")

    return data


# ---------------------------------------------------------------------------
# Markdown loader
# ---------------------------------------------------------------------------

def _load_markdown(path: Path) -> dict[str, Any]:
    """Parse a structured Markdown brief into a project description dict.

    Recognises these section headings (case-insensitive):
        ## Project Name
        ## Project Goal / Goal
        ## Deliverables
        ## Output Format
        ## Primary Output Directory / Primary Output
        ## Build Output / Build Output Directory
        ## Figures Directory
        ## Existing Project Path / Project Path
        ## Authority Sources / Sources
        ## Style Reference / Style Guide
        ## Tools / Technology Stack
        ## Reference Database / Bibliography
        ## Citation Key / Reference Key Convention
        ## Components / Workstreams
        ## Style Rules

    Also accepts key: value pairs at any point.
    Falls back to returning {project_goal: full_text} if no headers found.
    """
    text = path.read_text(encoding="utf-8")
    sections = _split_markdown_sections(text)

    if not sections:
        # Unstructured fallback — treat entire content as project_goal
        return {"project_goal": text.strip()}

    desc: dict[str, Any] = {}

    for heading, content in sections.items():
        heading_l = heading.lower().strip()
        body = content.strip()

        if "project name" in heading_l:
            desc["project_name"] = _first_line(body)
        elif "project goal" in heading_l or heading_l == "goal":
            desc["project_goal"] = body
        elif "deliverable" in heading_l:
            desc["deliverables"] = _parse_list(body)
        elif "output format" in heading_l:
            desc["output_format"] = _first_line(body)
        elif "primary output" in heading_l:
            desc["primary_output_dir"] = _first_line(body)
        elif "build output" in heading_l:
            desc["build_output_dir"] = _first_line(body)
        elif "figures" in heading_l:
            desc["figures_dir"] = _first_line(body)
        elif "existing project" in heading_l or heading_l in {"project path", "project location"}:
            desc["existing_project_path"] = _first_line(body)
        elif "authority" in heading_l or ("source" in heading_l and "reference" not in heading_l):
            desc["authority_sources"] = _parse_authority_sources(body)
        elif "style reference" in heading_l or "style guide" in heading_l:
            desc["style_reference"] = _first_line(body) or None
        elif "tool" in heading_l or "technology" in heading_l or "tech stack" in heading_l:
            desc["tools"] = _parse_tools(body)
        elif "reference database" in heading_l or "bibliography" in heading_l:
            desc["reference_db_path"] = _first_line(body) or None
        elif "citation key" in heading_l or "reference key" in heading_l:
            desc["reference_key_convention"] = _first_line(body)
        elif "component" in heading_l or "workstream" in heading_l:
            desc["components"] = _parse_components(body)
        elif "style rule" in heading_l:
            desc["style_rules"] = _parse_list(body)

    return desc


def _split_markdown_sections(text: str) -> dict[str, str]:
    """Split markdown text on ## headings. Returns {heading: body} dict."""
    pattern = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return {}

    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        heading = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections[heading] = body

    return sections


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("-* ")
        if stripped:
            return stripped
    return ""


def _parse_list(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*•0123456789.) ")
        if stripped:
            items.append(stripped)
    return items


def _parse_authority_sources(text: str) -> list[dict[str, str]]:
    sources = []
    for i, item in enumerate(_parse_list(text), start=1):
        # Try to split "Name: path — scope" or "name | path"
        if ":" in item:
            name, rest = item.split(":", 1)
            path_scope = rest.strip().split(" — ", 1)
            entry = {"name": name.strip(), "path": path_scope[0].strip(), "rank": i}
            if len(path_scope) > 1:
                entry["scope"] = path_scope[1].strip()
        elif "|" in item:
            parts = item.split("|")
            entry = {"name": parts[0].strip(), "path": parts[1].strip() if len(parts) > 1 else "", "rank": i}
        else:
            entry = {"name": item, "path": item, "rank": i}
        sources.append(entry)
    return sources


def _parse_tools(text: str) -> list[dict[str, Any]]:
    tools = []
    for item in _parse_list(text):
        # Recognise "Python 3.11", "LaTeX 2e", "React 18 (framework)"
        parts = item.split("(")
        name_ver = parts[0].strip().split()
        tool: dict[str, Any] = {"name": name_ver[0]}
        if len(name_ver) > 1:
            tool["version"] = " ".join(name_ver[1:])
        if len(parts) > 1:
            category = parts[1].rstrip(")").strip()
            tool["category"] = category
        tools.append(tool)
    return tools


def _parse_components(text: str) -> list[dict[str, Any]]:
    components = []
    # Each component may be "slug: Name" or bullet items
    for i, item in enumerate(_parse_list(text), start=1):
        if ":" in item:
            slug, name = item.split(":", 1)
            component = {"slug": _slugify(slug.strip()), "name": name.strip(), "number": i}
        else:
            component = {"slug": _slugify(item), "name": item, "number": i}
        components.append(component)
    return components


# ---------------------------------------------------------------------------
# Mode B: directory scan
# ---------------------------------------------------------------------------

def _supplement_from_directory(desc: dict[str, Any], project_path: Path) -> dict[str, Any]:
    """Scan an existing project directory and fill in missing description fields."""
    if not project_path.exists():
        return desc

    # Infer project name from directory if not set
    if "project_name" not in desc:
        desc["project_name"] = project_path.name

    # Read README for project_goal if not set
    if "project_goal" not in desc:
        readme = _find_readme(project_path)
        if readme:
            desc["project_goal"] = _extract_readme_goal(readme)

    # Detect tools from project files if not set
    if "tools" not in desc:
        desc["tools"] = _detect_tools(project_path)

    # Supplement tools with dependency manifest contents
    existing_names = {t.get("name", "").lower() for t in desc.get("tools", [])}
    for dep in parse_dependency_manifests(project_path):
        if dep["name"].lower() not in existing_names:
            desc.setdefault("tools", []).append(dep)
            existing_names.add(dep["name"].lower())

    # Detect primary_output_dir from common patterns if not set
    if "primary_output_dir" not in desc:
        detected = _detect_primary_output_dir(project_path)
        if detected:
            desc["primary_output_dir"] = detected

    return desc


def _find_readme(project_path: Path) -> str | None:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        candidate = project_path / name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


def _extract_readme_goal(readme_text: str) -> str:
    """Return the first non-heading paragraph from a README as the project goal."""
    lines = readme_text.splitlines()
    paragraphs = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
        elif stripped.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(stripped)

    if current:
        paragraphs.append(" ".join(current))

    # Return first paragraph that is more than 20 chars (skip badge lines etc.)
    for para in paragraphs:
        if len(para) > 20 and not para.startswith("!["):
            return para[:500]  # truncate very long README goals

    return readme_text.strip()[:500]


_TOOL_SIGNATURES: list[tuple[str, str, str]] = [
    # (filename_pattern, tool_name, category)
    # --- Languages / runtimes ---
    ("requirements.txt", "Python", "language"),
    ("pyproject.toml", "Python", "language"),
    ("setup.py", "Python", "language"),
    ("Pipfile", "Python", "language"),
    ("package.json", "Node.js", "language"),
    ("Cargo.toml", "Rust", "language"),
    ("go.mod", "Go", "language"),
    ("tsconfig.json", "TypeScript", "language"),
    ("deno.json", "Deno", "language"),
    ("Gemfile", "Ruby", "language"),
    ("composer.json", "PHP", "language"),
    ("*.tex", "LaTeX", "language"),
    ("*.bib", "BibTeX", "library"),
    # --- Build systems ---
    ("pom.xml", "Java (Maven)", "build-system"),
    ("build.gradle", "Java (Gradle)", "build-system"),
    ("Makefile", "Make", "build-system"),
    ("CMakeLists.txt", "CMake", "build-system"),
    ("webpack.config.*", "Webpack", "build-system"),
    ("vite.config.*", "Vite", "build-system"),
    # --- Databases ---
    ("*.sql", "SQL", "database"),
    # --- CLI / infrastructure ---
    ("Dockerfile", "Docker", "cli"),
    ("docker-compose.yml", "Docker Compose", "cli"),
    ("docker-compose.yaml", "Docker Compose", "cli"),
    (".github/workflows", "GitHub Actions", "cli"),
    (".gitlab-ci.yml", "GitLab CI", "cli"),
    ("Jenkinsfile", "Jenkins", "cli"),
    (".circleci", "CircleCI", "cli"),
    ("*.tf", "Terraform", "cli"),
    (".terraform", "Terraform", "cli"),
    ("k8s", "Kubernetes", "cli"),
    ("kubernetes", "Kubernetes", "cli"),
    ("serverless.yml", "Serverless", "cli"),
    ("fly.toml", "Fly.io", "cli"),
    ("vercel.json", "Vercel", "cli"),
    ("Procfile", "Heroku", "cli"),
    ("nginx.conf", "Nginx", "cli"),
    # --- Frameworks (detected by config presence) ---
    ("next.config.*", "Next.js", "framework"),
    ("nuxt.config.*", "Nuxt.js", "framework"),
    ("angular.json", "Angular", "framework"),
    # --- Other ---
    ("flake.nix", "Nix", "other"),
    ("*.proto", "Protocol Buffers", "library"),
    ("*.graphql", "GraphQL", "library"),
    (".eslintrc*", "ESLint", "library"),
    (".prettierrc*", "Prettier", "library"),
]


def _detect_tools(project_path: Path) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sig_pattern, tool_name, category in _TOOL_SIGNATURES:
        if tool_name in seen:
            continue
        if sig_pattern.startswith("*."):
            # Extension glob — walk up to depth 3
            ext = sig_pattern.lstrip("*")
            found = any(True for _ in _walk_depth(project_path, max_depth=3) if _.suffix == ext)
        elif "*" in sig_pattern:
            # Prefix glob like "webpack.config.*" or ".eslintrc*"
            prefix = sig_pattern.split("*")[0]
            found = any(
                f.name.startswith(prefix)
                for f in _walk_depth(project_path, max_depth=3)
            )
        else:
            # Exact path match (file or directory)
            found = (project_path / sig_pattern).exists()

        if found:
            tools.append({"name": tool_name, "category": category})
            seen.add(tool_name)

    return tools


def _detect_primary_output_dir(project_path: Path) -> str | None:
    candidates = ["src", "lib", "html", "docs", "reports", "output"]
    for candidate in candidates:
        if (project_path / candidate).is_dir():
            return f"{candidate}/"
    return None


# ---------------------------------------------------------------------------
# Dependency manifest parsing
# ---------------------------------------------------------------------------

def parse_dependency_manifests(project_path: Path) -> list[dict[str, Any]]:
    """Parse dependency manifests and return discovered library/framework tools.

    Args:
        project_path: Root of the project directory.

    Returns:
        List of tool dicts with name, version (if available), and category.
    """
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()

    parsers: list[tuple[str, Any]] = [
        ("requirements.txt", _parse_requirements_txt),
        ("pyproject.toml", _parse_pyproject_toml),
        ("package.json", _parse_package_json),
        ("Cargo.toml", _parse_cargo_toml),
        ("go.mod", _parse_go_mod),
    ]

    for filename, parser in parsers:
        manifest_path = project_path / filename
        if manifest_path.exists():
            try:
                text = manifest_path.read_text(encoding="utf-8", errors="replace")
                for dep in parser(text):
                    name_lower = dep["name"].lower()
                    if name_lower not in seen:
                        seen.add(name_lower)
                        tools.append(dep)
            except Exception:
                continue  # Skip unparseable manifests

    return tools


def _parse_requirements_txt(text: str) -> list[dict[str, Any]]:
    """Parse requirements.txt lines into tool dicts."""
    deps: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle version specifiers: package==1.0, package>=1.0, package~=1.0
        match = re.match(r"^([A-Za-z0-9_\-\.]+)\s*([=<>~!]+\s*[\d\.\*]+)?", line)
        if match:
            name = match.group(1)
            version = (match.group(2) or "").strip().lstrip("=<>~! ")
            deps.append({"name": name, "version": version, "category": "library"})
    return deps


def _parse_pyproject_toml(text: str) -> list[dict[str, Any]]:
    """Parse pyproject.toml dependencies (stdlib only, basic TOML parsing)."""
    deps: list[dict[str, Any]] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        # Detect [project.dependencies] or [tool.poetry.dependencies]
        if re.match(r"^\[(project\.)?dependencies\]", stripped) or \
           re.match(r"^\[tool\.poetry\.dependencies\]", stripped):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps:
            # String item in a list: "package>=1.0"
            list_match = re.match(r'^"([A-Za-z0-9_\-\.]+)\s*([=<>~!]+\s*[\d\.\*]+)?"', stripped)
            if list_match:
                name = list_match.group(1)
                version = (list_match.group(2) or "").strip().lstrip("=<>~! ")
                deps.append({"name": name, "version": version, "category": "library"})
                continue
            # Key-value: package = "^1.0" (poetry style)
            kv_match = re.match(r'^([A-Za-z0-9_\-\.]+)\s*=', stripped)
            if kv_match:
                name = kv_match.group(1)
                if name.lower() != "python":
                    deps.append({"name": name, "version": "", "category": "library"})
    return deps


def _parse_package_json(text: str) -> list[dict[str, Any]]:
    """Parse package.json dependencies."""
    deps: list[dict[str, Any]] = []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return deps
    for section in ("dependencies", "devDependencies"):
        for name, version in data.get(section, {}).items():
            version_clean = version.lstrip("^~>=<! ")
            deps.append({"name": name, "version": version_clean, "category": "library"})
    return deps


def _parse_cargo_toml(text: str) -> list[dict[str, Any]]:
    """Parse Cargo.toml [dependencies] section."""
    deps: list[dict[str, Any]] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[dependencies]":
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps:
            match = re.match(r'^([A-Za-z0-9_\-]+)\s*=', stripped)
            if match:
                deps.append({"name": match.group(1), "version": "", "category": "library"})
    return deps


def _parse_go_mod(text: str) -> list[dict[str, Any]]:
    """Parse go.mod require block."""
    deps: list[dict[str, Any]] = []
    in_require = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if stripped == ")" and in_require:
            in_require = False
            continue
        if in_require:
            parts = stripped.split()
            if len(parts) >= 2:
                # Module path like "github.com/gin-gonic/gin" → last segment
                module = parts[0].rstrip("/").split("/")[-1]
                version = parts[1].lstrip("v")
                deps.append({"name": module, "version": version, "category": "library"})
    return deps


def _walk_depth(path: Path, max_depth: int = 3) -> list[Path]:
    results: list[Path] = []
    for root, dirs, files in os.walk(path):
        depth = len(Path(root).relative_to(path).parts)
        if depth >= max_depth:
            dirs.clear()
        for fname in files:
            results.append(Path(root) / fname)
    return results


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(description: dict[str, Any]) -> list[str]:
    """Return a list of validation errors. Returns [] if valid."""
    errors: list[str] = []

    if "project_goal" not in description or not description["project_goal"].strip():
        errors.append("project_goal is required and must not be empty")

    for component in description.get("components", []):
        if not re.match(r"^[a-z0-9\-]+$", component.get("slug", "")):
            errors.append(
                f"Component slug must be lowercase alphanumeric with hyphens: {component.get('slug')!r}"
            )

    return errors


def _validate_required_fields(description: dict[str, Any]) -> None:
    errors = validate(description)
    if errors:
        raise ValueError("Project description validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

# _slugify is imported from agentteams._utils
