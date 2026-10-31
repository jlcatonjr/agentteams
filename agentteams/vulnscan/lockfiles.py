"""Read the lockfiles of Go, pnpm, Bun, RubyGems, Gradle and NuGet (stdlib only; :func:`manifests.parse` calls them).

| File | OSV ecosystem | What is read |
|---|---|---|
| ``go.mod`` | ``Go`` | ``require`` lines (single-line and block, ``// indirect`` too), with ``replace`` directives applied |
| ``pnpm-lock.yaml`` (v6, v9) | ``npm`` | the keys of ``packages:`` (peer suffixes dropped), by a restricted line reader, not YAML |
| ``bun.lock`` (text) | ``npm`` | ``packages[k][0]`` (``"name@version"``) after JSONC comments and trailing commas are dropped |
| ``Gemfile.lock`` | ``RubyGems`` | ``name (version[-platform])`` at four-space indent under ``GEM`` / ``specs:`` from rubygems.org |
| ``gradle.lockfile`` | ``Maven`` | ``group:artifact:version=configurations`` lines |
| ``packages.lock.json`` | ``NuGet`` | ``dependencies[tfm][name].resolved`` (``type: Project`` entries skipped) |

Every file here is repository content (C-4: data, never instructions). A name or version outside the format's
pattern (:data:`FORMATS`) makes the whole file unscannable, so no such text reaches a fix-plan command; so does a
file over :data:`MAX_BYTES`, an unknown lockfile version or a malformed file. A reader raises only
:class:`Unreadable`, which :func:`manifests.parse` reports. Entries that are not registry packages (local paths,
git, workspace links, tarball URLs, other gem sources) are skipped: they are not in OSV under that name.

Scopes are conservative: a package is ``dev`` only when the lockfile says so (pnpm v6 ``dev: true``; pnpm v9 when
reachable only from importers' ``devDependencies``; Gradle when every configuration is a test one), else ``runtime``.
Fixes are plan-only: ``executor.validate_diff`` refuses a change to any of these files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

#: Largest lockfile read (bytes); a larger one is unscannable.
MAX_BYTES = 64 * 1024 * 1024
#: Fewest ``go`` directive version (minor) that lists every module the build needs in ``go.mod``.
GO_FULL_LIST = (1, 17)

_GO_MOD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~/-]{0,254}")
_GO_VER = re.compile(r"v[0-9][0-9A-Za-z.+-]{0,127}")
_NPM_NAME = re.compile(r"(@[A-Za-z0-9._~-]{1,214}/)?[A-Za-z0-9._~-]{1,214}")
_SEMVERISH = re.compile(r"[0-9][0-9A-Za-z.+-]{0,127}")
_GEM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_GEM_VER = re.compile(r"[0-9][0-9A-Za-z.]{0,63}")
_MAVEN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,200}:[A-Za-z0-9_.-]{1,200}")
_MAVEN_VER = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}")
_NUGET_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_NUGET_VER = re.compile(r"[0-9][0-9A-Za-z.+-]{0,127}")

GO_STDLIB_NOTE = "Go standard library / toolchain vulnerabilities not judged"
GO_OLD_NOTE = "go < 1.17: indirect modules may be missing"
GO_WORK_NOTE = "go.work present: workspace replaces/uses not applied"


class Unreadable(ValueError):
    """The lockfile cannot be read honestly; the message is the unscannable reason."""


@dataclass
class LockRead:
    """What one reader found: ``(ecosystem, name, version, scope)`` rows and notes for a partial read."""

    deps: list[tuple[str, str, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Format:
    """One lockfile format: how it is read and how its plan-only fix plan is written."""

    ecosystem: str                                  # OSV ecosystem
    section: str                                    # fix-plan heading label
    name: re.Pattern[str]                           # what a package name may be
    version: re.Pattern[str]                        # what a version (as read, and as fixed) may be
    reader: Callable[[Path], LockRead]
    command: Callable[[str, str, str], str]         # (name, installed, fixed) -> the command a person runs
    then: str                                       # what the person runs afterwards


def _text(path: Path) -> str:
    """Return the file's text, refusing anything over :data:`MAX_BYTES` or not UTF-8."""
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise Unreadable(f"larger than {MAX_BYTES} bytes: not read")
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Unreadable(f"could not parse: not UTF-8 ({exc.reason})") from exc
    except OSError as exc:
        raise Unreadable(f"could not parse: {type(exc).__name__}") from exc


def _unusual(what: str) -> Unreadable:
    return Unreadable(f"{what} with an unusual name or version: not read")


# ---------------------------------------------------------------- Go: go.mod


def _go_fields(line: str) -> list[str]:
    """Split one go.mod line into tokens, dropping the comment and the quotes of quoted tokens."""
    out: list[str] = []
    for tok in re.findall(r'"(?:[^"\\]|\\.)*"|`[^`]*`|//.*|\S+', line):
        if tok.startswith("//"):
            break
        out.append(tok[1:-1] if tok[:1] in ('"', "`") else tok)
    return out


def _go_local(target: str) -> bool:
    return target.startswith(("./", "../", "/", ".\\", "..\\")) or target in (".", "..") \
        or bool(re.match(r"^[A-Za-z]:[\\/]", target))


def _go_work_above(path: Path) -> bool:
    """Return whether a ``go.work`` sits beside ``path`` or in a directory above it, up to the repository top."""
    for d in (path.parent, *path.parent.parents):
        if (d / "go.work").is_file():
            return True
        if (d / ".git").exists():
            return False
    return False


def read_go_mod(path: Path) -> LockRead:
    """Read a ``go.mod``: every required module, with ``replace`` directives applied.

    A replace to a local path drops the module (it is the repository's own code) and is named in a note; a replace
    to another module substitutes its name and version. Versions are given to OSV without the ``v``
    (pseudo-versions as they are). A ``go`` directive below 1.17, or none, adds :data:`GO_OLD_NOTE`; a ``go.work``
    beside or above adds :data:`GO_WORK_NOTE`; every Go manifest carries :data:`GO_STDLIB_NOTE`.

    Args:
        path: The go.mod.

    Returns:
        The modules and notes.

    Raises:
        Unreadable: Too large, not UTF-8, a malformed line, or a module path or version of an unusual form.
    """
    requires: list[tuple[str, str]] = []
    replaces: dict[tuple[str, str], tuple[str, str]] = {}
    go_version: tuple[int, int] | None = None
    block = ""
    for raw in _text(path).splitlines():
        toks = _go_fields(raw)
        if not toks:
            continue
        if block:
            if toks == [")"]:
                block = ""
                continue
            verb, args = block, toks
        elif len(toks) == 2 and toks[1] == "(":
            block = toks[0]
            continue
        else:
            verb, args = toks[0], toks[1:]
        if verb == "go" and args:
            hit = re.fullmatch(r"(\d{1,4})\.(\d{1,4})(?:\.\d{1,4})?(?:rc\d+|beta\d+)?", args[0])
            if not hit:
                raise Unreadable("go.mod: a go directive of an unusual form: not read")
            go_version = (int(hit.group(1)), int(hit.group(2)))
        elif verb == "require":
            if len(args) != 2:
                raise Unreadable("go.mod: a malformed require line: not read")
            requires.append((args[0], args[1]))
        elif verb == "replace":
            if "=>" not in args:
                raise Unreadable("go.mod: a malformed replace line: not read")
            i = args.index("=>")
            old, new = args[:i], args[i + 1:]
            if len(old) not in (1, 2) or len(new) not in (1, 2):
                raise Unreadable("go.mod: a malformed replace line: not read")
            replaces[(old[0], old[1] if len(old) == 2 else "")] = (new[0], new[1] if len(new) == 2 else "")
    if block:
        raise Unreadable("go.mod: an unterminated block: not read")
    out, local = LockRead(), []
    seen: set[tuple[str, str]] = set()
    for mod, ver in requires:
        if not (_GO_MOD.fullmatch(mod) and _GO_VER.fullmatch(ver)):
            raise _unusual("go.mod: a module")
        new = replaces.get((mod, ver)) or replaces.get((mod, ""))
        if new and _go_local(new[0]):
            local.append(mod)
            continue
        if new:
            mod, ver = new
            if not (_GO_MOD.fullmatch(mod) and _GO_VER.fullmatch(ver)):
                raise _unusual("go.mod: a replacement module")
        if (mod, ver) not in seen:
            seen.add((mod, ver))
            out.deps.append(("Go", mod, ver[1:], "runtime"))
    if local:
        out.notes.append(f"{len(local)} module(s) replaced by a local path, not scanned: {', '.join(sorted(local))}")
    if go_version is None or go_version < GO_FULL_LIST:
        out.notes.append(GO_OLD_NOTE)
    if _go_work_above(path):
        out.notes.append(GO_WORK_NOTE)
    out.notes.append(GO_STDLIB_NOTE)
    return out


# ---------------------------------------------------------------- pnpm: pnpm-lock.yaml (restricted line reader)

#: ``key:`` or ``key: value``; a plain key holds no whitespace and ends at the first ``:`` followed by a space or the
#: end of the line (``foo@https://x/y.tgz:`` is one key).
_YAML_KEY = re.compile(r"""^( *)('(?:[^']|'')*'|"(?:[^"\\]|\\.)*"|[^\s'"#][^\s]*?)\s*:(?:\s+(.*?))?\s*$""")


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except json.JSONDecodeError as exc:
            raise Unreadable("pnpm-lock.yaml: a malformed quoted key: not read") from exc
    return text


def _yaml_tree(text: str) -> dict:
    """Read the block mappings of a pnpm lockfile into nested dicts (``key: value`` scalars as strings).

    Lines that are not ``key:`` / ``key: value`` (sequence items, flow-collection continuations, block-scalar text)
    are ignored: YAML indents them deeper than their key, so they can only add to that key, never fake a shallower
    one. Comments and blank lines are dropped.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or "\t" in raw[:len(raw) - len(raw.lstrip())]:
            continue
        hit = _YAML_KEY.match(raw)
        if not hit or raw.lstrip().startswith("- "):
            continue
        indent, key, value = len(hit.group(1)), _unquote(hit.group(2)), hit.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            raise Unreadable("pnpm-lock.yaml: malformed indentation: not read")
        parent = stack[-1][1]
        if value is None or value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _unquote(value.split(" #", 1)[0].strip())
    return root


def _strip_peers(text: str) -> str:
    return text.split("(", 1)[0]


def _pnpm_key(key: str, v6: bool) -> tuple[str, str] | None:
    """Split a ``packages:`` key into (name, version); None for a non-registry entry; raise for an unusual one."""
    if v6:
        if not key.startswith("/"):
            return None   # v6 keys of non-registry packages are paths or URLs, not "/name@version"
        key = key[1:]
    key = _strip_peers(key)
    at = key.rfind("@")
    if at <= 0:
        raise _unusual("pnpm-lock.yaml: a package")
    name, version = key[:at], key[at + 1:]
    if ":" in version or "/" in version or "#" in version:
        return None       # file:, link:, git or tarball URL
    if not (_NPM_NAME.fullmatch(name) and _SEMVERISH.fullmatch(version)):
        raise _unusual("pnpm-lock.yaml: a package")
    return name, version


def _pnpm_dev_only(tree: dict) -> set[str] | None:
    """Return the ``name@version`` of v9 packages reachable only from importers' ``devDependencies``.

    None when that cannot be determined (no importers or no snapshots): every package is then ``runtime``.
    """
    importers, snapshots = tree.get("importers"), tree.get("snapshots")
    if not isinstance(importers, dict) or not isinstance(snapshots, dict):
        return None
    graph: dict[str, list[str]] = {}
    for key, info in snapshots.items():
        kids: list[str] = []
        if isinstance(info, dict):
            for sec in ("dependencies", "optionalDependencies"):
                for child, ver in (info.get(sec) or {}).items() if isinstance(info.get(sec), dict) else ():
                    if isinstance(ver, str):
                        kids.append(_node(child, ver))
        graph.setdefault(_strip_peers(key), []).extend(kids)

    def reach(starts: list[str]) -> set[str]:
        seen: set[str] = set()
        todo = list(starts)
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(graph.get(n, ()))
        return seen

    run, dev = [], []
    for imp in importers.values():
        if not isinstance(imp, dict):
            return None
        for sec, bucket in (("dependencies", run), ("optionalDependencies", run), ("devDependencies", dev)):
            for name, spec in (imp.get(sec) or {}).items() if isinstance(imp.get(sec), dict) else ():
                ver = spec.get("version") if isinstance(spec, dict) else spec
                if isinstance(ver, str):
                    bucket.append(_node(name, ver))
    return reach(dev) - reach(run)


def _node(name: str, ver: str) -> str:
    ver = _strip_peers(ver)
    return ver if "@" in ver[1:] else f"{name}@{ver}"   # an alias's version is "realname@version"


def read_pnpm_lock(path: Path) -> LockRead:
    """Read a ``pnpm-lock.yaml`` (lockfile v6 or v9): every registry package of ``packages:``.

    v6 keys are ``/name@1.2.3`` or ``/@scope/name@1.2.3(peer@x)`` and carry ``dev: true`` for dev-only packages; v9
    keys are ``name@1.2.3`` or ``'@scope/name@1.2.3'``, and a package is ``dev`` only when it is reachable solely from
    importers' ``devDependencies`` through ``snapshots:`` (else, and whenever that cannot be told, ``runtime``).

    Args:
        path: The lockfile.

    Returns:
        The packages.

    Raises:
        Unreadable: Too large, another lockfile version, or a package of an unusual form.
    """
    tree = _yaml_tree(_text(path))
    version = str(tree.get("lockfileVersion", ""))
    major = version.split(".", 1)[0]
    if major not in ("6", "9"):
        raise Unreadable(f"pnpm-lock.yaml: lockfile version {version[:20] or '(none)'!r} not read (v6 and v9 are)")
    packages = tree.get("packages")
    if not isinstance(packages, dict):
        packages = {}
    v6 = major == "6"
    dev_only = None if v6 else _pnpm_dev_only(tree)
    out, seen = LockRead(), set()
    for key, info in packages.items():
        got = _pnpm_key(key, v6)
        if not got or got in seen:
            continue
        seen.add(got)
        if v6:
            dev = isinstance(info, dict) and info.get("dev") == "true"
        else:
            dev = dev_only is not None and f"{got[0]}@{got[1]}" in dev_only
        out.deps.append(("npm", got[0], got[1], "dev" if dev else "runtime"))
    return out


# ---------------------------------------------------------------- Bun: bun.lock (JSONC)


def _strip_jsonc(text: str) -> str:
    """Drop ``//`` and ``/* */`` comments and trailing commas outside strings."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise Unreadable("bun.lock: an unterminated comment: not read")
            i = j + 2
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1   # a trailing comma
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_bun_lock(path: Path) -> LockRead:
    """Read a text ``bun.lock``: ``packages[k][0]`` is ``"name@version"`` (the last ``@`` past index 0 splits it).

    Workspace, git, file and tarball entries (a version holding ``:``, ``/`` or ``#``) are skipped. Every package is
    ``runtime``. The binary ``bun.lockb`` is never read.

    Args:
        path: The lockfile.

    Returns:
        The packages.

    Raises:
        Unreadable: Too large, not JSONC, another lockfile version, or a package of an unusual form.
    """
    try:
        data = json.loads(_strip_jsonc(_text(path)))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise Unreadable(f"bun.lock: could not parse: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("lockfileVersion") not in (0, 1):
        raise Unreadable("bun.lock: an unknown lockfile version: not read")
    packages = data.get("packages") or {}
    if not isinstance(packages, dict):
        raise Unreadable("bun.lock: packages is not an object: not read")
    out, seen = LockRead(), set()
    for entry in packages.values():
        if not (isinstance(entry, list) and entry and isinstance(entry[0], str)):
            raise _unusual("bun.lock: a package")
        spec = entry[0]
        at = spec.rfind("@")
        if at <= 0:
            raise _unusual("bun.lock: a package")
        name, version = spec[:at], spec[at + 1:]
        if ":" in version or "/" in version or "#" in version:
            continue
        if not (_NPM_NAME.fullmatch(name) and _SEMVERISH.fullmatch(version)):
            raise _unusual("bun.lock: a package")
        if (name, version) not in seen:
            seen.add((name, version))
            out.deps.append(("npm", name, version, "runtime"))
    return out


# ---------------------------------------------------------------- RubyGems: Gemfile.lock

_GEM_SPEC = re.compile(r"^    (\S+) \(([^()\s]+)\)$")
_GEM_PLATFORM = re.compile(r"-(?:java|jruby|mswin\w*|mingw\w*|x64-mingw[\w-]*|universal-[\w.-]+"
                           r"|(?:x86_64|x86|i[3-6]86|arm64|aarch64|armv?\d*\w*|ppc64le|s390x|riscv64|x64)-[\w.-]+)$")
_RUBYGEMS = re.compile(r"^https?://rubygems\.org/?$")


def read_gemfile_lock(path: Path) -> LockRead:
    """Read a ``Gemfile.lock``: the specs of each ``GEM`` section whose remote is rubygems.org.

    Entries sit at four-space indent under ``specs:`` as ``name (version)`` or ``name (version-platform)``; a
    platform suffix is dropped only when it matches a known platform. ``GIT`` and ``PATH`` sections are not
    registry gems and are skipped; gems of another gem source are counted in a note (never looked up: they may be
    private). Every gem is ``runtime``.

    Args:
        path: The lockfile.

    Returns:
        The gems.

    Raises:
        Unreadable: Too large, or a gem of an unusual form.
    """
    out, seen, other = LockRead(), set(), 0
    section, remotes, in_specs = "", [], False
    for line in _text(path).splitlines():
        if line and not line.startswith(" "):
            section, remotes, in_specs = line.strip(), [], False
            continue
        if section != "GEM":
            continue
        if line.startswith("  remote: "):
            remotes.append(line.split(": ", 1)[1].strip())
        elif line == "  specs:":
            in_specs = True
        elif in_specs and line.startswith("    ") and not line.startswith("     "):
            hit = _GEM_SPEC.match(line)
            if not hit:
                raise _unusual("Gemfile.lock: a gem")
            if not remotes or not all(_RUBYGEMS.match(r) for r in remotes):
                other += 1
                continue
            name, version = hit.group(1), _GEM_PLATFORM.sub("", hit.group(2))
            if not (_GEM_NAME.fullmatch(name) and _GEM_VER.fullmatch(version)):
                raise _unusual("Gemfile.lock: a gem")
            if (name, version) not in seen:
                seen.add((name, version))
                out.deps.append(("RubyGems", name, version, "runtime"))
    if other:
        out.notes.append(f"{other} gem(s) from a source other than rubygems.org, not scanned")
    return out


# ---------------------------------------------------------------- Gradle: gradle.lockfile (Maven)

_GRADLE_LINE = re.compile(r"^([^:=\s]+):([^:=\s]+):([^:=\s]+)=(.*)$")


def read_gradle_lockfile(path: Path) -> LockRead:
    """Read a ``gradle.lockfile``: ``group:artifact:version=conf,...`` lines, named ``group:artifact``.

    A dependency is ``dev`` when every configuration it is locked for is a test one (name holding ``test``).

    Args:
        path: The lockfile.

    Returns:
        The dependencies.

    Raises:
        Unreadable: Too large, a malformed line, or a coordinate of an unusual form.
    """
    out, seen = LockRead(), set()
    for raw in _text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("empty="):
            continue
        hit = _GRADLE_LINE.match(line)
        if not hit:
            raise Unreadable("gradle.lockfile: a malformed line: not read")
        name, version = f"{hit.group(1)}:{hit.group(2)}", hit.group(3)
        if not (_MAVEN_NAME.fullmatch(name) and _MAVEN_VER.fullmatch(version)):
            raise _unusual("gradle.lockfile: a dependency")
        confs = [c.strip() for c in hit.group(4).split(",") if c.strip()]
        scope = "dev" if confs and all("test" in c.lower() for c in confs) else "runtime"
        if (name, version) not in seen:
            seen.add((name, version))
            out.deps.append(("Maven", name, version, scope))
    return out


# ---------------------------------------------------------------- NuGet: packages.lock.json


def read_nuget_lock(path: Path) -> LockRead:
    """Read a NuGet ``packages.lock.json``: ``dependencies[tfm][name].resolved`` over every target framework.

    ``type: Project`` entries (the solution's own projects) are skipped. Every package is ``runtime``.

    Args:
        path: The lockfile.

    Returns:
        The packages.

    Raises:
        Unreadable: Too large, not JSON, another lockfile version, or a package of an unusual form.
    """
    try:
        data = json.loads(_text(path))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise Unreadable(f"packages.lock.json: could not parse: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("version") not in (1, 2):
        raise Unreadable("packages.lock.json: an unknown lockfile version: not read")
    frameworks = data.get("dependencies") or {}
    if not isinstance(frameworks, dict):
        raise Unreadable("packages.lock.json: dependencies is not an object: not read")
    out, seen = LockRead(), set()
    for pkgs in frameworks.values():
        if not isinstance(pkgs, dict):
            raise Unreadable("packages.lock.json: a target framework is not an object: not read")
        for name, info in pkgs.items():
            if not isinstance(info, dict):
                raise _unusual("packages.lock.json: a package")
            if info.get("type") == "Project":
                continue
            version = info.get("resolved")
            if not (isinstance(version, str) and _NUGET_NAME.fullmatch(name) and _NUGET_VER.fullmatch(version)):
                raise _unusual("packages.lock.json: a package")
            if (name.lower(), version) not in seen:
                seen.add((name.lower(), version))
                out.deps.append(("NuGet", name, version, "runtime"))
    return out


# ---------------------------------------------------------------- the formats

FORMATS: dict[str, Format] = {
    "go.mod": Format("Go", "Go", _GO_MOD, re.compile(r"v?[0-9][0-9A-Za-z.+-]{0,127}"), read_go_mod,
                     lambda n, _o, new: f"`go get {n}@v{new.removeprefix('v')}`", "`go mod tidy`, the build and the tests"),
    "pnpm-lock.yaml": Format("npm", "npm (pnpm)", _NPM_NAME, _SEMVERISH, read_pnpm_lock,
                             lambda n, _o, new: f"`pnpm update {n}@{new}`", "`pnpm install` and the tests"),
    "bun.lock": Format("npm", "npm (Bun)", _NPM_NAME, _SEMVERISH, read_bun_lock,
                       lambda n, _o, new: f"`bun update {n}@{new}`", "`bun install` and the tests"),
    "Gemfile.lock": Format("RubyGems", "RubyGems", _GEM_NAME, _GEM_VER, read_gemfile_lock,
                           lambda n, _o, _new: f"`bundle update --conservative {n}`",
                           "check the lockfile holds at least \"→\", then the tests"),
    "gradle.lockfile": Format("Maven", "Maven (Gradle)", _MAVEN_NAME, _MAVEN_VER, read_gradle_lockfile,
                              lambda n, _o, new: f"set `{n}` to `{new}` in the build script, then "
                                                 "`./gradlew dependencies --write-locks`", "the build and the tests"),
    "packages.lock.json": Format("NuGet", "NuGet", _NUGET_NAME, _NUGET_VER, read_nuget_lock,
                                 lambda n, _o, new: f"`dotnet add package {n} --version {new}`",
                                 "`dotnet restore --locked-mode` and the tests"),
}
