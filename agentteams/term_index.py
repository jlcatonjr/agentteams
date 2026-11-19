"""term_index.py — a term register, an occurrence index over a document set, and the checks on both (beta).

**Status: beta** (outside SemVer in 1.3.x; see ``STABILITY.md``). The core of researchteam's term index
(researchteam 5beca9f), moved into agentteams by operator ruling (issue #548) so one implementation
serves both. The points that were research-specific are parameters of :class:`TermIndexConfig`: the
directory that holds the register, the sections that are not prose, how a source is validated, which
text of a document is scanned, and the names written into generated files.

A **register** (``<dir_name>/terms.json``) lists terms: an ``id``, a ``label``, a ``definition``, and
optional ``variants``, ``discouraged`` forms, ``source``, ``notes`` and ``plural``. :func:`assess`
validates it, finds every occurrence of every form in the in-scope documents, and renders the generated
files (a manifest, one occurrence index per document, a glossary) in memory. :func:`check_project`
compares that with what is committed and returns blocking defects and advisory reviews, coded as in
researchteam's protocol: ``SCHEMA AMBIG SRC SCOPE STALE DISC ORPHAN NOREG`` block; ``REBUILD NOSRC
UNUSED QUOTED`` advise.

Two register schemas are read: the neutral ``agentteams-term-register`` and, unchanged,
``researchteam-term-register``, so a researchteam register keeps its hash and its committed index stays
current.

CEILING (do not soften): matching finds forms, not meanings. A clean check establishes that the register
is well formed and the index current. It does not establish that a term is used in its defined sense.
Nothing here writes a file. Standard library only.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentteams._term_text import (
    WORD, Matcher, form_key, form_keys, glob_match, lead_key, mask, match_words, quoted_spans, term_forms,
)

__all__ = [
    "BAD", "FOREIGN", "MISSING", "NEUTRAL_SCHEMA", "OK", "RESEARCHTEAM_SCHEMA", "SCHEMA_VERSION",
    "Assessment", "Matcher", "SourceValidator", "TermIndexConfig", "assess", "check_project", "discover",
    "disc_violations", "documents", "form_key", "glob_match", "load_register", "mask", "quoted_spans",
    "read_document", "read_manifest", "register_text_problems", "shown", "stray_index_files",
]

NEUTRAL_SCHEMA = "agentteams-term-register"
RESEARCHTEAM_SCHEMA = "researchteam-term-register"
SCHEMA_VERSION = "1.0"
CEILING = ("A current index establishes that the register is well formed and that the index matches "
           "the documents. It does not establish that a term is used in its defined sense. "
           "Matching finds forms, not meanings.")
MAX_TERMS, MAX_FORMS, MAX_FORM_CHARS, MAX_ALL_FORMS = 5000, 50, 200, 20000
# Bounds on matching work, refused at load time so that no input can stall a scan.
MAX_FORM_WORDS, MAX_FORMS_PER_LEAD, MAX_SCOPE_PATTERNS = 16, 32, 64
MAX_REGISTER_BYTES, MAX_DOC_BYTES = 1_000_000, 5_000_000
#: Cap on a committed generated file read back (the manifest, an index file), so a planted huge file can't be loaded.
MAX_GENERATED_BYTES = 20_000_000

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR_FD = os.open in os.supports_dir_fd and _NOFOLLOW and hasattr(os, "O_DIRECTORY")


def read_capped(root: Path, rel: str | Path, cap: int) -> bytes | None:
    """The bytes of ``root/rel`` when it is a regular file of at most *cap* bytes reached through no symbolic link.

    Every component is opened relative to its parent's descriptor with ``O_NOFOLLOW`` (where the platform has
    ``dir_fd``), so a link swapped in after any earlier check is not followed; the file itself is opened
    ``O_NONBLOCK`` (a FIFO can't hang the read), checked with ``fstat``, and read through that descriptor up to
    *cap* + 1 bytes, so a file that grows after the size check is still refused (researchteam #193, #551 review).

    Args:
        root: The directory the path is relative to (the project); it is trusted.
        rel: The path below *root*, with no ``..`` component.
        cap: The size limit in bytes.

    Returns:
        The bytes, or ``None`` for a link anywhere below *root*, a non-regular file, or a file over *cap*.

    Raises:
        FileNotFoundError: A component does not exist.
        OSError: Another error opening or reading the file.
        ValueError: *rel* is absolute or has a ``..`` component.
    """
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        raise ValueError(f"not a path below the project: {rel}")
    fds: list[int] = []
    try:
        if _DIR_FD:
            fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY))
            for part in parts[:-1]:
                try:
                    fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fds[-1]))
                except OSError as exc:  # a link under O_NOFOLLOW|O_DIRECTORY (ENOTDIR, ELOOP or EMLINK), or a file
                    if exc.errno in (errno.ENOTDIR, errno.ELOOP, getattr(errno, "EMLINK", -1)):
                        return None
                    raise
            opener = {"dir_fd": fds[-1]}
            target: str | Path = parts[-1]
        else:  # no dir_fd (Windows): check every component, then open the last without following it
            here = root
            for part in parts:
                here = here / part
                if here.is_symlink():
                    return None
            opener, target = {}, root.joinpath(*parts)
        try:
            fd = os.open(target, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), **opener)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                return None  # the last component is a link
            raise
        fds.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            return None
        chunks, left = [], cap + 1
        while left > 0:
            chunk = os.read(fd, min(left, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b"".join(chunks)
        return None if len(data) > cap else data
    finally:
        for fd in reversed(fds):
            os.close(fd)
MISSING, FOREIGN, BAD, OK = "missing", "foreign", "bad", "ok"  # register status
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/ -]+$")
TERM_KEYS = {"id", "label", "definition", "variants", "discouraged", "source", "notes", "plural"}
TOP_KEYS = {"schema", "schema_version", "scope", "terms", "allow"}

#: ``source_validator(project, term) -> [(code, message), ...]``: defects for one term's ``source`` list.
SourceValidator = Callable[[Path, dict], list]


@dataclass(frozen=True)
class TermIndexConfig:
    """Where a register lives and how its documents are read. Every field has a neutral default.

    Attributes:
        generator: Written into the manifest. Change it whenever generated bytes would change for the same
            inputs: the check uses it to tell an upgrade (advisory ``REBUILD``) from a hand edit (``STALE``).
        dir_name: The directory under a project that holds ``terms.json`` and the generated files.
        schemas: The ``schema`` values accepted as a register; any other ``terms.json`` is ``FOREIGN``.
        skip_heading: Heading lines whose sections are not prose (researchteam: its reference lists).
        source_validator: Checks a term's ``source`` list; ``None`` accepts any non-empty strings.
        region_filter: Maps a document's text to the text that is scanned. It must keep length and line
            breaks (blank what is skipped with ``\\x00``); ``None`` scans the whole document.
        default_include: Scope globs used when the register names none.
        do_not_edit: The banner written into each generated file.
        rebuild_hint: The command a message tells the reader to run to rebuild.
        glossary_hint: The glossary's second banner line (where to query occurrences).
    """

    generator: str = "agentteams-term-index/1"
    dir_name: str = "terminology"
    schemas: tuple[str, ...] = (NEUTRAL_SCHEMA, RESEARCHTEAM_SCHEMA)
    skip_heading: re.Pattern | None = None
    source_validator: SourceValidator | None = None
    region_filter: Callable[[str], str] | None = None
    default_include: tuple[str, ...] = ("**/*.md",)
    do_not_edit: str = "GENERATED from terminology/terms.json by agentteams.term_index. Do not edit."
    rebuild_hint: str = "term-build"
    glossary_hint: str = "Where each term occurs: the term-index query."


@dataclass
class Assessment:
    """One pass over a project. ``problems`` are ``(code, message)`` defects found before any comparison."""

    status: str
    register: dict | None = None
    register_sha256: str = ""
    problems: list = field(default_factory=list)
    docs: dict = field(default_factory=dict)       # document -> (sha256, occurrences)
    files: dict = field(default_factory=dict)      # path under dir_name -> bytes
    manifest: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """Whether the register loaded with no defect."""
        return self.status == OK and not self.problems


def shown(path: str) -> str:
    """A path for a message an agent will read: withheld unless it is made of plain characters.

    Args:
        path: A relative path.

    Returns:
        The path, or a placeholder.
    """
    return path if SAFE_PATH.match(path) else "<path withheld: unusual characters>"


def discover(base: Path, is_project: Callable[[Path], bool], *, pruned: frozenset[str] = frozenset(),
             skip_dir: str = "terminology", max_depth: int = 12) -> list[Path]:
    """Every project directory under ``base``, nested ones included, down to ``max_depth`` levels.

    Args:
        base: The directory searched (researchteam: ``Projects/``). A symbolic link is not followed.
        is_project: Whether a directory is a project.
        pruned: Directory names never entered.
        skip_dir: A project's own register directory, which is not searched.
        max_depth: How deep to search.

    Returns:
        Project directories, in sorted walk order.

    Raises:
        OSError: A directory under ``base`` cannot be listed.
    """
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        for child in sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink()):
            if child.name in pruned or (child.name == skip_dir and is_project(d)):
                continue
            if is_project(child):
                found.append(child)
            if depth < max_depth:
                walk(child, depth + 1)
    if base.is_dir() and not base.is_symlink():
        walk(base, 1)
    return found


# ----------------------------------------------------------------------------- the register
def _strings(value, what: str, where: str, problems: list) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        problems.append(("SCHEMA", f"{where}: '{what}' must be a list of non-empty strings"))
        return []
    return value


def _check_term(term, n: int, seen: set, problems: list) -> int:
    """Validate one term record. Returns how many forms it has."""
    if not isinstance(term, dict):
        problems.append(("SCHEMA", f"term {n}: must be an object"))
        return 0
    tid = term.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        problems.append(("SCHEMA", f"term {n}: 'id' must be a slug of 1 to 64 lower-case letters, digits, hyphens"))
        return 0
    where = f"term '{tid}'"
    if tid in seen:
        problems.append(("SCHEMA", f"{where}: id is defined more than once"))
    seen.add(tid)
    if set(term) - TERM_KEYS:
        problems.append(("SCHEMA", f"{where}: unknown key (allowed: {', '.join(sorted(TERM_KEYS))})"))
    for key in ("label", "definition"):
        if not isinstance(term.get(key), str) or not term[key].strip():
            problems.append(("SCHEMA", f"{where}: '{key}' must be a non-empty string"))
    forms = [term["label"]] if isinstance(term.get("label"), str) else []
    for key in ("variants", "discouraged"):
        if key in term:
            forms += _strings(term[key], key, where, problems)
    if len(forms) > MAX_FORMS or any(len(f) > MAX_FORM_CHARS or len(match_words(f)) > MAX_FORM_WORDS for f in forms):
        problems.append(("SCHEMA", f"{where}: at most {MAX_FORMS} forms, each at most {MAX_FORM_CHARS} characters "
                                   f"and {MAX_FORM_WORDS} words"))
    if any(f != f.strip() or f != f.strip(">*_-") for f in forms):
        problems.append(("SCHEMA", f"{where}: a form may not begin or end with a space, hyphen, underscore, > or *"))
    if any(re.search(r"\s-|-\s|--", f) for f in forms):  # the matcher allows one separator between words
        problems.append(("SCHEMA", f"{where}: in a form, a hyphen may not touch a space or another hyphen"))
    # Every word needs a letter or digit: a word of only markup characters could match almost anywhere.
    if any(not match_words(f) or not all(WORD.search(w) for w in match_words(f)) for f in forms):
        problems.append(("SCHEMA", f"{where}: every word of a form must contain a letter or a digit"))
    if not isinstance(term.get("plural", True), bool):
        problems.append(("SCHEMA", f"{where}: 'plural' must be true or false"))
    if "notes" in term and not isinstance(term["notes"], str):
        problems.append(("SCHEMA", f"{where}: 'notes' must be a string"))
    src = term.get("source")
    if src is not None and src != "project definition" and not (
            isinstance(src, list) and src and all(isinstance(k, str) and k.strip() for k in src)):
        problems.append(("SCHEMA", f"{where}: 'source' must be \"project definition\" or a list of .bib keys"))
    return len(forms)


def _check_scope_and_allow(reg: dict, ids: set, problems: list) -> None:
    scope = reg.get("scope", {})
    if not isinstance(scope, dict) or set(scope) - {"include", "exclude"}:
        problems.append(("SCHEMA", "'scope' must be an object with 'include' and/or 'exclude'"))
    else:
        for key in scope:
            if isinstance(scope[key], list) and len(scope[key]) > MAX_SCOPE_PATTERNS:
                problems.append(("SCOPE", f"scope '{key}' lists more than {MAX_SCOPE_PATTERNS} patterns"))
            for n, pat in enumerate(_strings(scope[key], key, "scope", problems), 1):
                if pat.startswith("/") or ".." in pat.split("/") or "\\" in pat or len(pat) > MAX_FORM_CHARS:
                    problems.append(("SCOPE", f"scope pattern {n} in '{key}' is absolute, too long or leaves "
                                              "the project"))
    allow = reg.get("allow", [])
    if not isinstance(allow, list):
        problems.append(("SCHEMA", "'allow' must be a list"))
        return
    for n, entry in enumerate(allow, 1):
        if not (isinstance(entry, dict) and set(entry) == {"term", "file", "reason"}
                and all(isinstance(v, str) and v.strip() for v in entry.values())):
            problems.append(("SCHEMA", f"allow entry {n}: needs exactly 'term', 'file' and 'reason' (strings)"))
        elif entry["term"] not in ids:
            problems.append(("SCHEMA", f"allow entry {n}: names a term id that is not registered"))


def register_text_problems(reg: dict) -> list:
    """Validate a parsed register (schema marker already accepted): records, scope, allow, ambiguity.

    Args:
        reg: The parsed ``terms.json``.

    Returns:
        ``(code, message)`` defects; empty when the register is sound.
    """
    problems: list = [("SCHEMA", "unknown top-level key")] if set(reg) - TOP_KEYS else []
    if reg.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        problems.append(("SCHEMA", f"unsupported schema_version (this builder reads {SCHEMA_VERSION})"))
    terms = reg.get("terms")
    if not isinstance(terms, list) or len(terms) > MAX_TERMS:
        return problems + [("SCHEMA", f"'terms' must be a list of at most {MAX_TERMS} records")]
    seen: set[str] = set()
    if sum(_check_term(term, n, seen, problems) for n, term in enumerate(terms, 1)) > MAX_ALL_FORMS:
        problems.append(("SCHEMA", f"the register holds more than {MAX_ALL_FORMS} forms"))
    _check_scope_and_allow(reg, seen, problems)
    if not any(code == "SCHEMA" for code, _ in problems):
        problems += _ambiguities(terms) + _crowded_leads(terms)
    return problems


def load_register(proj: Path, config: TermIndexConfig = TermIndexConfig()) -> Assessment:
    """Read and validate ``<dir_name>/terms.json``.

    Args:
        proj: The project directory.
        config: Where the register lives and which schemas are accepted.

    Returns:
        An :class:`Assessment` whose status is ``missing``, ``foreign`` (a ``terms.json`` without an
        accepted schema marker), ``bad`` or ``ok``.
    """
    tdir, path = proj / config.dir_name, proj / config.dir_name / "terms.json"
    if tdir.is_symlink() or path.is_symlink():
        return Assessment(BAD, problems=[("SCHEMA", f"{config.dir_name}/ or terms.json is a symbolic link (refused)")])
    if not path.is_file():
        return Assessment(MISSING)
    try:
        raw = read_capped(proj, Path(config.dir_name) / "terms.json", MAX_REGISTER_BYTES)
        if raw is None:
            return Assessment(BAD, problems=[("SCHEMA", f"terms.json is a link, not a regular file, or larger than "
                                                        f"{MAX_REGISTER_BYTES} bytes (refused)")])
        reg = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        where = f"line {exc.lineno}, column {exc.colno}"
        return Assessment(BAD, problems=[("SCHEMA", f"terms.json is not valid JSON at {where}")])
    except (OSError, ValueError, RecursionError) as exc:  # unreadable, not UTF-8, or JSON the parser refuses
        return Assessment(BAD, problems=[("SCHEMA", f"terms.json cannot be read ({type(exc).__name__})")])
    if not isinstance(reg, dict) or reg.get("schema") not in config.schemas:
        return Assessment(FOREIGN)
    problems = register_text_problems(reg)
    return Assessment(BAD if problems else OK, reg, hashlib.sha256(raw).hexdigest(), problems)


def _ambiguities(terms: list) -> list:
    owner: dict[str, tuple[str, bool]] = {}
    out = []
    for term in terms:
        for form, kind in term_forms(term):
            for key in form_keys(form, term.get("plural", True)):
                prev = owner.setdefault(key, (term["id"], kind == "discouraged"))
                if prev[0] != term["id"]:
                    out.append(("AMBIG", f"terms '{prev[0]}' and '{term['id']}' claim the same form"))
                elif prev[1] != (kind == "discouraged"):
                    out.append(("AMBIG", f"term '{term['id']}' lists one form as both accepted and discouraged"))
    return sorted(set(out))


def _crowded_leads(terms: list) -> list:
    counts: dict[tuple, int] = {}
    for term in terms:
        for form, _ in term_forms(term):
            counts[lead_key(form)[:2]] = counts.get(lead_key(form)[:2], 0) + 1  # counted across offsets
    return [("SCHEMA", f"more than {MAX_FORMS_PER_LEAD} forms begin with the same word (one group has {n})")
            for n in sorted(set(counts.values())) if n > MAX_FORMS_PER_LEAD][-1:]


# ----------------------------------------------------------------------------- which documents
def _is_nested_project(d: Path, config: TermIndexConfig) -> bool:
    return any((d / config.dir_name / f).exists() for f in ("terms.json", "term-index.json"))


def documents(proj: Path, reg: dict, config: TermIndexConfig = TermIndexConfig(),
              is_project: Callable[[Path], bool] | None = None) -> list[str]:
    """In-scope files: never ``dir_name/``, hidden directories, symbolic links or a nested project.

    Args:
        proj: The project directory.
        reg: The validated register (its ``scope``).
        config: The configuration.
        is_project: Marks a nested project to skip; defaults to "holds its own register".

    Returns:
        POSIX paths relative to ``proj``, sorted by walk.

    Raises:
        OSError: A directory or file cannot be inspected.
    """
    nested = is_project or (lambda d: _is_nested_project(d, config))
    scope = reg.get("scope", {})
    include, exclude = scope.get("include", list(config.default_include)), scope.get("exclude", [])
    found = []
    for base, dirs, files in os.walk(proj, followlinks=False):
        here = Path(base)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (here / d).is_symlink()
                         and not (here == proj and d == config.dir_name) and not nested(here / d))
        for name in sorted(files):
            if (here / name).is_symlink():
                continue
            rel = (here / name).relative_to(proj).as_posix()
            if any(glob_match(p, rel) for p in include) and not any(glob_match(p, rel) for p in exclude):
                found.append(rel)
    return found


def read_document(path: Path, root: Path | None = None) -> bytes | None:
    """The bytes of a regular file within the size limit; ``None`` for anything else (a link, a pipe, too big).

    Args:
        path: The file.
        root: The project; given, *path* is read through :func:`read_capped` below it (no link anywhere on the
            path, nothing past the cap read). Without it, the file's parent is taken as the root.

    Returns:
        Its bytes, or ``None``.

    Raises:
        OSError: The file cannot be inspected or read.
    """
    base = root if root is not None else path.parent
    try:
        return read_capped(base, path.relative_to(base), MAX_DOC_BYTES)
    except FileNotFoundError:
        return None


def disc_violations(reg: dict, rel: str, occs: list[dict]) -> list[dict]:
    """Occurrences of a discouraged form in ``rel`` that are neither quoted nor covered by an ``allow`` entry.

    Args:
        reg: The register.
        rel: The document's relative path.
        occs: Its occurrences.

    Returns:
        The violating occurrences.
    """
    allowed = {a["term"] for a in reg.get("allow", []) if a["file"] == rel}
    return [o for o in occs if o["kind"] == "discouraged" and not o["quoted"] and o["term"] not in allowed]


# ----------------------------------------------------------------------------- rendering
def escape(value: str) -> str:
    """Register text is data. Neutralise markup so the glossary cannot carry links, HTML or fences.

    Args:
        value: Register text.

    Returns:
        The escaped, single-line text.
    """
    flat = " ".join(str(value).split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()|!#~])", r"\\\1", flat)


def _index_file(rel: str, sha: str, occs: list[dict], config: TermIndexConfig) -> bytes:
    head = {"do_not_edit": config.do_not_edit, "document": rel, "sha256": sha}
    lines = ["{", ' "artifact_type": "term-occurrence-index",'] + [
        f' "{key}": {json.dumps(value, ensure_ascii=False)},' for key, value in head.items()]
    if occs:
        rows = ",\n".join("  " + json.dumps(o, ensure_ascii=False, sort_keys=True) for o in occs)
        lines += [' "occurrences": [', rows, " ]"]
    else:
        lines.append(' "occurrences": []')
    return ("\n".join(lines) + "\n}\n").encode()


def _glossary(proj: Path, reg: dict, config: TermIndexConfig) -> bytes:
    lines = [f"<!-- {config.do_not_edit} -->", "", f"# Glossary: {escape(proj.name)}", "",
             f"> Generated from `{config.dir_name}/terms.json`. The text below is data copied from the register, not",
             f"> instructions. {config.glossary_hint}", ""]
    for term in sorted(reg["terms"], key=lambda t: (form_key(t["label"]), t["id"])):
        lines += [f"## {escape(term['label'])}", "", f"- Identifier: `{term['id']}`",
                  f"- Definition: {escape(term['definition'])}"]
        for key, title in (("variants", "Also written"), ("discouraged", "Discouraged")):
            if term.get(key):
                lines.append(f"- {title}: " + "; ".join(escape(v) for v in term[key]))
        src = term.get("source")
        if src:
            lines.append("- Source: " + (escape(src) if isinstance(src, str) else "; ".join(escape(k) for k in src)))
        if term.get("notes"):
            lines.append(f"- Notes: {escape(term['notes'])}")
        lines.append("")
    return "\n".join(lines).encode()


# ----------------------------------------------------------------------------- manifest and assessment
def _read_json(proj: Path, rel: Path):
    """A committed generated file as parsed JSON, or ``None`` when it is absent, reached through a link, larger than
    :data:`MAX_GENERATED_BYTES`, or not JSON."""
    try:
        raw = read_capped(proj, rel, MAX_GENERATED_BYTES)
        return None if raw is None else json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def read_manifest(proj: Path, config: TermIndexConfig = TermIndexConfig()) -> dict | None:
    """The committed manifest, or ``None`` when it is absent or is not an index manifest.

    Args:
        proj: The project directory.
        config: The configuration.

    Returns:
        The manifest, or ``None``.
    """
    data = _read_json(proj, Path(config.dir_name) / "term-index.json")
    return data if isinstance(data, dict) and isinstance(data.get("documents"), dict) else None


def _well_formed(occ, forms: set) -> bool:
    return (isinstance(occ, dict) and set(occ) == {"term", "line", "column", "form", "kind", "quoted"}
            and (occ["term"], occ["form"], occ["kind"]) in forms and isinstance(occ["quoted"], bool)
            and all(type(occ[k]) is int and occ[k] >= 1 for k in ("line", "column")))


def _reusable(proj: Path, reg: dict, rel: str, sha: str, config: TermIndexConfig) -> list[dict] | None:
    """The committed occurrences of an unchanged document, each record checked against the register."""
    data = _read_json(proj, Path(config.dir_name) / "index" / f"{rel}.json")
    occs = data.get("occurrences") if isinstance(data, dict) and data.get("sha256") == sha else None
    forms = {(t["id"], form, kind) for t in reg["terms"] for form, kind in term_forms(t)}
    return occs if isinstance(occs, list) and all(_well_formed(o, forms) for o in occs) else None


def assess(proj: Path, config: TermIndexConfig = TermIndexConfig(), reuse: bool = False,
           is_project: Callable[[Path], bool] | None = None) -> Assessment:
    """Validate the register, scan the documents and render every generated file, in memory.

    Args:
        proj: The project directory.
        config: The configuration.
        reuse: Keep the committed occurrences of a document whose hash, register hash and generator all
            match the committed manifest (a hook's fast path; a gate always scans).
        is_project: Passed to :func:`documents`.

    Returns:
        The :class:`Assessment`; ``files`` maps paths under ``dir_name`` to the bytes a build would write.

    Raises:
        OSError: A document cannot be read (an unreadable register is a ``SCHEMA`` problem instead).
    """
    a = load_register(proj, config)
    if a.status != OK:
        return a
    old = read_manifest(proj, config) if reuse else None
    if not (old and old.get("generator") == config.generator and old.get("register_sha256") == a.register_sha256):
        old = None
    names = documents(proj, a.register, config, is_project)
    if not names:
        a.problems.append(("SCOPE", "the scope matches no document"))
    matcher = None
    for rel in names:
        raw = read_document(proj / rel, proj)
        if raw is None:
            a.problems.append(("SCOPE", f"{shown(rel)}: not a regular file, or larger than {MAX_DOC_BYTES} bytes"))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        occs = _reusable(proj, a.register, rel, sha, config) if old and old["documents"].get(rel) == sha else None
        if occs is None:
            matcher = matcher or Matcher(a.register, config.skip_heading)
            text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
            occs = matcher.occurrences(config.region_filter(text) if config.region_filter else text)
        a.docs[rel] = (sha, occs)
    a.manifest = {"artifact_type": "term-index-manifest", "do_not_edit": config.do_not_edit, "ceiling": CEILING,
                  "generator": config.generator, "register_sha256": a.register_sha256,
                  "documents": {rel: sha for rel, (sha, _) in sorted(a.docs.items())}}
    a.files = {"term-index.json": (json.dumps(a.manifest, indent=1, ensure_ascii=False) + "\n").encode(),
               "glossary.md": _glossary(proj, a.register, config)}
    for rel, (sha, occs) in a.docs.items():
        a.files[f"index/{rel}.json"] = _index_file(rel, sha, occs, config)
    return a


def stray_index_files(proj: Path, files: dict[str, bytes], config: TermIndexConfig = TermIndexConfig()) -> set[str]:
    """Files under ``<dir_name>/index/`` that a fresh build would not have written.

    Args:
        proj: The project directory.
        files: :attr:`Assessment.files`.
        config: The configuration.

    Returns:
        Their paths relative to ``dir_name``.
    """
    tdir = proj / config.dir_name
    if not (tdir / "index").is_dir() or (tdir / "index").is_symlink():
        return set()
    on_disk = {p.relative_to(tdir).as_posix() for p in (tdir / "index").rglob("*") if p.is_symlink() or not p.is_dir()}
    return on_disk - set(files)


# ----------------------------------------------------------------------------- the check
def _freshness(proj: Path, a: Assessment, config: TermIndexConfig) -> tuple[list, list]:
    hint, d = f"(run: {config.rebuild_hint})", config.dir_name
    old = read_manifest(proj, config)
    if old is None:
        return [("STALE", f"no usable index manifest {hint}")], []
    changes = [] if old.get("register_sha256") == a.register_sha256 else ["the register changed"]
    old_docs, new_docs = old["documents"], a.manifest["documents"]
    for rel in sorted(set(old_docs) | set(new_docs)):
        name = shown(rel)
        if rel not in old_docs:
            changes.append(f"{name}: entered the scope")
        elif rel not in new_docs:
            changes.append(f"{name}: left the scope")
        elif old_docs[rel] != new_docs[rel]:
            changes.append(f"{name}: changed")
    if changes:
        return [("STALE", f"{c} since the index was built {hint}") for c in changes], []
    same_generator = old.get("generator") == config.generator
    defects = []
    for rel, data in sorted(a.files.items()):  # a different generator may write other bytes: skip only then
        path = proj / d / rel
        try:  # no link anywhere on the path; nothing past the expected size is read (None: a link, or larger)
            on_disk = read_capped(proj, Path(d) / rel, len(data))
        except (OSError, ValueError):
            on_disk = None
        if path.is_symlink() or not path.is_file():
            defects.append(("STALE", f"{d}/{shown(rel)}: missing (edited by hand, or incomplete; run: {config.rebuild_hint})"))
        elif same_generator and on_disk != data:
            defects.append(("STALE", f"{d}/{shown(rel)}: differs from a fresh build although the inputs are unchanged "
                                     f"(edited by hand, or incomplete; run: {config.rebuild_hint})"))
    defects += [("STALE", f"{d}/{shown(rel)}: not a file the builder writes (edited by hand, or incomplete; "
                          f"run: {config.rebuild_hint})") for rel in sorted(stray_index_files(proj, a.files, config))]
    reviews = [] if same_generator else [
        ("REBUILD", f"the inputs are unchanged but the index was built by another generator version {hint}")]
    return defects, reviews


def _usage(proj: Path, a: Assessment, config: TermIndexConfig) -> tuple[list, list]:
    defects, reviews, used, quoted = [], [], set(), 0
    for rel, (_, occs) in sorted(a.docs.items()):
        used |= {o["term"] for o in occs if o["kind"] != "discouraged"}
        quoted += sum(1 for o in occs if o["kind"] == "discouraged" and o["quoted"])
        for o in disc_violations(a.register, rel, occs):
            defects.append(("DISC", f"{shown(rel)}:{o['line']}:{o['column']} term '{o['term']}': a "
                                    "discouraged form outside a quotation, with no 'allow' entry"))
    for term in a.register["terms"]:
        src = term.get("source")
        if src is None:
            reviews.append(("NOSRC", f"term '{term['id']}': the definition has no 'source'"))
        elif isinstance(src, list) and config.source_validator is not None:
            defects += config.source_validator(proj, term)
        if term["id"] not in used:
            reviews.append(("UNUSED", f"term '{term['id']}': no occurrence of the label or a variant"))
    if quoted:
        reviews.append(("QUOTED", f"{quoted} discouraged form(s) inside quotations (exempt; listed so that "
                                  "quoting stays visible)"))
    return defects, reviews


def check_project(proj: Path, config: TermIndexConfig = TermIndexConfig(), *, required: bool = False,
                  indexed: bool = True, is_project: Callable[[Path], bool] | None = None) -> tuple[bool, list, list]:
    """Check one project's register and, when ``indexed``, that its committed index is current.

    Args:
        proj: The project directory.
        config: The configuration.
        required: A missing register is a ``NOREG`` defect (otherwise it is not checked).
        indexed: Compare with the committed generated files (``STALE``/``REBUILD``). With ``False`` the
            check is live only: the register, its scope, discouraged forms and sources.
        is_project: Passed to :func:`documents`.

    Returns:
        ``(a register was checked, defects, reviews)``; each finding is ``(code, message)``.

    Raises:
        OSError: A document or committed generated file cannot be read.
    """
    a = assess(proj, config, is_project=is_project)
    if a.status in (MISSING, FOREIGN):
        tdir = proj / config.dir_name
        if (tdir / "term-index.json").exists() or (tdir / "index").exists():
            return False, [("ORPHAN", "generated index files exist but there is no term register")], []
        if required:
            hint = "its terms.json has no accepted schema marker" if a.status == FOREIGN else "add terms.json"
            return False, [("NOREG", f"the project has no term register ({hint})")], []
        return False, [], []
    if a.status == BAD:
        return True, a.problems, []
    fresh_defects, fresh_reviews = _freshness(proj, a, config) if indexed else ([], [])
    usage_defects, usage_reviews = _usage(proj, a, config)
    return True, a.problems + fresh_defects + usage_defects, fresh_reviews + usage_reviews
