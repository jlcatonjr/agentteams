"""A project's own vulnerability-scanning intent, read from (and written to) its brief.

The brief's ``vulnerability_scanning`` block is committed with the project. It states:

- ``level``: ``off``, ``scan`` (dependency names and versions go to OSV.dev) or ``remediate``
  (also offered to the operator's remediation agent, inside a signed grant);
- ``exclude_paths``: repository-relative globs whose manifests are not scanned;
- ``private_packages``: case-insensitive fnmatch patterns of package names never sent to OSV,
  PyPI or npm (PyPI names compared per PEP 503); the report counts them as "not checked (private)";
- ``include_dev``: whether this project's dev-only findings count toward the exit threshold;
- ``delivery`` (``pr`` | ``plan``: the executor then refuses ``open-pr``) and ``base_branch`` (the PR
  base; ``stage-fix`` refuses when origin has no such branch).

Operator choices win: a brief only narrows what leaves the machine, never what is checked. When the
operator enrols a repository or lists it in ``repo_roots``, ``level: off`` does not stop the scan; it is
reported as a mismatch ("brief says off; scanned by operator enrolment"). Only the operator's own
enrolment at ``off`` skips it. ``exclude_paths`` and ``private_packages`` always apply. The executor
also refuses ``stage-fix``/``open-pr`` when the brief's level is set and is not ``remediate``.

A brief without the block, or without a brief at all, is *unanswered* (``level`` None). An invalid
block is treated as unanswered, with the reason in :attr:`ProjectPolicy.problem`: a policy that
cannot be read never widens what is sent.

The block holds intent only, never machine facts. Every value is checked by :data:`NO_LEAK`: no
host names, IP addresses, URLs, absolute paths, ``..`` or ``~``; list entries made only of wildcards
and separators are refused (:data:`NOT_CATCH_ALL`). The same patterns are in
``project-description.schema.json``; a test keeps the two identical.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

POLICY_KEY = "vulnerability_scanning"
LEVELS = ("off", "scan", "remediate")
DELIVERIES = ("pr", "plan")
KEYS = ("level", "exclude_paths", "private_packages", "include_dev", "delivery", "base_branch")
MAX_ITEMS = 100
ENROLL_HINT = "python -m agentteams.vulnscan enroll"


def _ci(word: str) -> str:
    """Return a case-insensitive regex for ``word`` without flags (JSON Schema patterns have none)."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else re.escape(c) for c in word)


_HOST_SUFFIXES = ("com", "net", "org", "local", "lan", "internal", "corp", "intranet", "localdomain")

#: Lookaheads every value must pass: no newline, absolute path, parent reference, home (``~``),
#: scheme or port (``:``), ``//`` or backslash; no IPv4 address; nothing host-shaped (``localhost``,
#: or a dotted name ending in a common network suffix such as ``.com``, ``.lan`` or ``.internal``).
NO_LEAK = (
    r"(?!.*\n)(?!/)(?!.*(?:\.\.|~|:|//|\\))"
    r"(?!(?:.*[^0-9.])?[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9]))"
    r"(?!(?:.*[./@])?" + _ci("localhost") + r"(?:[./]|$))"
    r"(?!.*\.(?:" + "|".join(_ci(s) for s in _HOST_SUFFIXES) + r")(?:/|$))"
)
#: Lookahead refusing a catch-all pattern: at least one letter or digit outside a ``[...]`` set
#: (``*``, ``**``, ``*/**``, ``?*``, ``.*`` or ``@*/*`` would exclude or withhold everything).
NOT_CATCH_ALL = r"(?=(?:\[[^\]]*\]|[^A-Za-z0-9\[])*[A-Za-z0-9])"
EXCLUDE_PATH_PATTERN = "^" + NO_LEAK + NOT_CATCH_ALL + r"[A-Za-z0-9_.*?\[\]!/+ -]{1,200}$"
PRIVATE_PACKAGE_PATTERN = ("^" + NO_LEAK + NOT_CATCH_ALL
                           + r"@?[A-Za-z0-9_.*?\[\]!-]{1,200}(?:/[A-Za-z0-9_.*?\[\]!-]{1,200})?$")
BASE_BRANCH_PATTERN = "^" + NO_LEAK + r"(?!-)(?!.*(?:\.lock(?:/|$)|/\.|/$|@\{))[A-Za-z0-9._/-]{1,200}$"


@dataclass(frozen=True)
class ProjectPolicy:
    """The ``vulnerability_scanning`` block of one project's brief."""

    level: str | None                       # None = unanswered (no brief, no block, or an invalid block)
    exclude_paths: tuple[str, ...] = ()     # repository-relative globs
    private_packages: tuple[str, ...] = ()  # fnmatch patterns, case-insensitive
    include_dev: bool | None = None
    delivery: str = "pr"                    # "pr" | "plan"
    base_branch: str | None = None
    brief_path: Path | None = None
    problem: str = ""                       # why the block is invalid ("" if fine); invalid => level None


def find_brief(repo: Path) -> Path | None:
    """Return the project's brief, resolved exactly as fleet updates resolve it.

    Args:
        repo: Project (repository) directory.

    Returns:
        The rich brief when present, else the thin stub, else None.
    """
    from agentteams.fleet import _resolve_descriptor   # the canonical lookup (redteam F-3)

    return _resolve_descriptor(Path(repo))


def _list_problems(name: str, value: object, pattern: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{name} must be a list"]
    if len(value) > MAX_ITEMS:
        return [f"{name} has more than {MAX_ITEMS} entries"]
    bad = [v for v in value if not isinstance(v, str) or not re.fullmatch(pattern, v)]
    if bad:
        return [(f"{name}: {len(bad)} value(s) are not allowed (hosts, IP addresses, URLs, absolute or "
                 "parent paths, ~ and catch-all patterns such as * are refused; paths are repository-relative)")]
    return [f"{name} has duplicate entries"] if len(set(value)) != len(value) else []


def block_problems(block: object) -> list[str]:
    """Return why a ``vulnerability_scanning`` block is invalid (empty when it is valid).

    Args:
        block: The block's decoded JSON value.

    Returns:
        One message per problem; messages never echo the offending values.
    """
    if not isinstance(block, dict):
        return [f"{POLICY_KEY} must be an object"]
    out = [f"unknown key {k!r}" if isinstance(k, str) and len(k) <= 40 else "unknown key"
           for k in block if k not in KEYS]
    if "level" not in block:
        out.append("level is required")
    elif block["level"] not in LEVELS:
        out.append(f"level must be one of {', '.join(LEVELS)}")
    if "exclude_paths" in block:
        out += _list_problems("exclude_paths", block["exclude_paths"], EXCLUDE_PATH_PATTERN)
    if "private_packages" in block:
        out += _list_problems("private_packages", block["private_packages"], PRIVATE_PACKAGE_PATTERN)
    if "include_dev" in block and not isinstance(block["include_dev"], bool):
        out.append("include_dev must be true or false")
    if "delivery" in block and block["delivery"] not in DELIVERIES:
        out.append(f"delivery must be one of {', '.join(DELIVERIES)}")
    if "base_branch" in block and not (isinstance(block["base_branch"], str)
                                       and re.fullmatch(BASE_BRANCH_PATTERN, block["base_branch"])):
        out.append("base_branch must be a branch name")
    return out


def policy_from_brief(data: object, brief_path: Path | None = None) -> ProjectPolicy:
    """Return the policy stated by a decoded brief.

    Args:
        data: The brief's decoded JSON.
        brief_path: Where it was read from (recorded on the result).

    Returns:
        The policy; level None when the block is absent or invalid.
    """
    if not isinstance(data, dict):
        return ProjectPolicy(level=None, brief_path=brief_path, problem="the brief is not a JSON object")
    if POLICY_KEY not in data:
        return ProjectPolicy(level=None, brief_path=brief_path)
    block = data[POLICY_KEY]
    problems = block_problems(block)
    if problems:
        return ProjectPolicy(level=None, brief_path=brief_path, problem="; ".join(problems))
    return ProjectPolicy(level=block["level"], exclude_paths=tuple(block.get("exclude_paths", ())),
                         private_packages=tuple(block.get("private_packages", ())),
                         include_dev=block.get("include_dev"), delivery=block.get("delivery", "pr"),
                         base_branch=block.get("base_branch"), brief_path=brief_path)


def _load(brief: Path) -> tuple[object, str]:
    """Return (decoded brief, "") or (None, why it cannot be read)."""
    try:
        return json.loads(brief.read_text(encoding="utf-8")), ""
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:   # RecursionError: deep nesting
        return None, f"the brief cannot be read as JSON ({type(exc).__name__})"


def read_policy(repo: Path) -> ProjectPolicy:
    """Return the scanning policy of the project at ``repo``. Never raises on a bad or missing brief.

    Args:
        repo: Project (repository) directory.

    Returns:
        The policy; level None (unanswered) when there is no brief, no block, or an invalid one.
    """
    brief = find_brief(repo)
    if brief is None:
        return ProjectPolicy(level=None)
    data, why = _load(brief)
    if why:
        return ProjectPolicy(level=None, brief_path=brief, problem=why)
    return policy_from_brief(data, brief)


def _pep503(name: str) -> str:
    """Return a PyPI name normalised per PEP 503: lowercase, each run of ``-``, ``_`` and ``.`` one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_private(name: str, patterns: Iterable[str], ecosystem: str = "") -> bool:
    """Return whether a package name matches one of a project's ``private_packages`` patterns.

    Args:
        name: Package name as written in a manifest.
        patterns: fnmatch patterns; matching is case-insensitive.
        ecosystem: ``PyPI`` normalises the name and every pattern per PEP 503 first, so
            ``Acme_Core``, ``acme.core`` and ``acme-core`` are one package (as pip treats them).

    Returns:
        True when the name must not leave the machine.
    """
    norm = _pep503 if ecosystem == "PyPI" else str.lower
    low = norm(name)
    return any(fnmatch.fnmatchcase(low, norm(p)) for p in patterns)


def is_excluded(rel: str, patterns: Iterable[str]) -> bool:
    """Return whether a repository-relative path, or a directory above it, matches ``exclude_paths``.

    Args:
        rel: POSIX path relative to the repository.
        patterns: Globs (``*`` crosses ``/``, as in fnmatch); a trailing ``/`` is ignored.

    Returns:
        True when the manifest at ``rel`` is not to be scanned.
    """
    parts = rel.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(p, pat.rstrip("/")) for pat in patterns for p in prefixes)


def repo_dir(path: Path, root: Path) -> Path:
    """Return the git repository holding ``path`` (nearest ancestor with ``.git``, not above ``root``).

    Args:
        path: A file under ``root``.
        root: Scan root.

    Returns:
        The repository directory, or ``root`` when none is found below it.
    """
    for parent in [path.parent, *path.parent.parents]:
        if (parent / ".git").exists():
            return parent
        if parent == root:
            break
    return root


class PolicyCache:
    """Read each repository's policy once per scan."""

    def __init__(self) -> None:
        self._seen: dict[Path, ProjectPolicy] = {}

    def get(self, repo: Path) -> ProjectPolicy:
        """Return the (cached) policy of ``repo``."""
        if repo not in self._seen:
            self._seen[repo] = read_policy(repo)
        return self._seen[repo]


def unanswered_line(project: Path | str, policy: ProjectPolicy) -> str:
    """Return the one-line notice for a project whose scan level is unanswered, else "".

    Args:
        project: Project directory, as it should appear in the command.
        policy: Its policy.

    Returns:
        The notice, naming the operator command that answers it.
    """
    if policy.level is not None:
        return ""
    why = f" ({policy.problem})" if policy.problem else ""
    return f"vulnerability scanning: unanswered{why} — answer with `{ENROLL_HINT} {project}`"


def report_unanswered(description: dict) -> None:
    """Print the unanswered notice for a loaded description (``--update``). Writes nothing.

    Args:
        description: The loaded brief.
    """
    project = description.get("existing_project_path") or "."
    line = unanswered_line(project, policy_from_brief(description))
    if line:
        print(line)


def _top_level_spans(text: str) -> dict[str, tuple[int, int]] | None:
    """Return {key: (value start, value end)} for the top-level object in ``text``, or None."""
    dec = json.JSONDecoder()
    i = len(text) - len(text.lstrip())
    if text[i:i + 1] != "{":
        return None
    spans: dict[str, tuple[int, int]] = {}
    i += 1
    ws = re.compile(r"\s*")
    while True:
        i = ws.match(text, i).end()
        if text[i:i + 1] == "}":
            return spans
        key, i = dec.raw_decode(text, i)
        i = ws.match(text, i).end() + 1          # past ':'
        i = ws.match(text, i).end()
        start = i
        _value, i = dec.raw_decode(text, i)
        spans[key] = (start, i)
        i = ws.match(text, i).end()
        if text[i:i + 1] == ",":
            i += 1


def _indent_of(text: str) -> str:
    m = re.search(r"\n([ \t]+)\"", text)
    return m.group(1) if m else "  "


def plan_level(repo: Path, level: str) -> tuple[Path | None, str, str, str]:
    """Return (brief, current text, new text, problem) for setting the scan level; writes nothing.

    Only the block's text changes: every other key keeps its text, order and indentation.

    Args:
        repo: Project directory.
        level: One of :data:`LEVELS`.

    Returns:
        A problem ("" when the edit can be made); new text equals current text when nothing changes.
    """
    if level not in LEVELS:
        return None, "", "", f"level must be one of {', '.join(LEVELS)}"
    brief = find_brief(repo)
    if brief is None:
        return None, "", "", "no brief found; build the team first (a brief is never created from nothing)"
    if brief.suffix != ".json":
        return brief, "", "", "the brief is not a JSON file"
    if brief.is_symlink():
        return brief, "", "", "the brief is a symlink; never written through one"
    real_repo = Path(os.path.realpath(repo))
    if real_repo not in Path(os.path.realpath(brief)).parents:
        return brief, "", "", "the brief is outside the project; never written there"
    data, why = _load(brief)
    if why or not isinstance(data, dict):
        return brief, "", "", why or "the brief is not a JSON object"
    text = brief.read_text(encoding="utf-8")
    try:
        spans = _top_level_spans(text)
    except (json.JSONDecodeError, IndexError, RecursionError):
        spans = None
    if spans is None:
        return brief, text, "", "the brief's layout could not be followed; edit it by hand"
    ind = _indent_of(text)
    old = data.get(POLICY_KEY)
    block = dict(old) if isinstance(old, dict) else {}
    block["level"] = level
    rendered = json.dumps(block, indent=ind, ensure_ascii=False).replace("\n", "\n" + ind)
    if POLICY_KEY in spans:
        start, end = spans[POLICY_KEY]
        new = text[:start] + rendered + text[end:]
    else:
        close = text.rstrip().rfind("}")
        head = text[:close].rstrip()
        sep = "" if head.endswith("{") else ","
        new = f'{head}{sep}\n{ind}"{POLICY_KEY}": {rendered}\n' + text[close:]
    expected = dict(data)
    expected[POLICY_KEY] = block
    if json.loads(new) != expected:      # never write text that decodes to anything else
        return brief, text, "", "the edit could not be made safely; edit the brief by hand"
    return brief, text, new, ""


def level_diff(name: str, old: str, new: str) -> str:
    """Return the unified diff shown before a level change (``name``: the brief, relative to the project)."""
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"a/{name}", tofile=f"b/{name}"))


def set_level(repo: Path, level: str, *, confirm: Callable[[str], bool],
              say: Callable[[str], None] | None = None) -> bool:
    """Write ``level`` into the project's brief after ``confirm`` approves the diff.

    The block is created when absent (its other keys are kept when present). A brief is never
    created from nothing.

    Args:
        repo: Project directory.
        level: One of :data:`LEVELS`.
        confirm: Called with the unified diff; the brief is written only when it returns True.
        say: Receives the reason when nothing is written (default: stderr).

    Returns:
        Whether the brief was written.
    """
    say = say or (lambda msg: print(msg, file=sys.stderr))
    brief, old, new, problem = plan_level(Path(repo), level)
    if problem:
        say(f"vulnerability scanning level not set: {problem}")
        return False
    if new == old:
        say(f"vulnerability scanning level is already {level} in {brief.name}")
        return False
    try:
        name = brief.relative_to(Path(repo)).as_posix()
    except ValueError:
        name = brief.name
    if not confirm(level_diff(name, old, new)):
        say("vulnerability scanning level not set: not confirmed")
        return False
    brief.write_text(new, encoding="utf-8")
    return True
