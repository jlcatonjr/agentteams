"""Container base images: support status and pinning of the images a Dockerfile or compose file names.

:func:`refs` reads the image references of one container manifest (``Dockerfile*``, ``*.Dockerfile``,
``Containerfile``, ``compose.y(a)ml``, ``docker-compose*.y(a)ml``) with stdlib line readers (no YAML library):

- a Dockerfile's ``FROM [--platform=..] <ref> [AS <name>]``. A ``FROM`` naming an earlier stage, and
  ``scratch``, are skipped. ``${NAME}`` / ``$NAME`` resolve to the default of an ``ARG NAME=default`` written
  before the first ``FROM`` (labelled "ARG default; `--build-arg` may override"); anything else unresolved is
  listed, never guessed;
- a compose file's ``services.<name>.image``. A service with ``build:`` is skipped (its ``image:`` names the
  build result, and its Dockerfile is scanned on its own); an ``image:`` with ``${VAR}`` is unresolved.

:func:`judge` then decides each reference, sending only endoflife.date product slugs (never an image name):

- an **official image** (bare name, ``library/<name>`` or ``docker.io/library/<name>``; ``.NET`` on
  ``mcr.microsoft.com/dotnet/{runtime,aspnet,sdk}``) is mapped to a :data:`runtimes.PRODUCTS` kind
  (:data:`IMAGE_PRODUCTS`) or an :data:`runtimes.OS_PRODUCTS` key (:data:`OS_IMAGES`), and its tag to a version;
- a **release-line tag** (``3.12-slim``, ``18-alpine``) is judged on the line alone: end of life is HIGH, anything
  else is fine (a rebuild picks up the line's newest patch);
- a **full-patch tag** (``3.11.4``) is never rebuilt to a newer patch, so a newer patch on the line is MODERATE
  ("pinned patch tag behind <latest>"); a missing security release stays HIGH;
- a tag with **fewer parts than the release line** (``python:3``, ``redis:7``) or a **keyword tag**
  (``lts``, ``stable``, ``current``) floats: not judged, listed as such, never fine;
- ``:latest`` or **no tag** is LOW ("unpinned tag: rebuilds change silently");
- ``openjdk`` is deprecated upstream (LOW) and judged on Eclipse Temurin's dates; ``amazoncorretto`` is judged
  as ``java-corretto``;
- the **OS in the tag** (``-bookworm``, ``-jammy``, ``-alpine3.18``, ``-al2023``) is judged with
  :func:`runtimes.evaluate_os`, for official and other public images alike (``mcr.microsoft.com/playwright/
  python:v1.57.0-jammy`` gets its Ubuntu 22.04 judged; the playwright line itself is not judged);
- an image on a **private registry** (any registry not in :data:`PUBLIC_REGISTRIES`) or a bare name vulnscan does
  not know (a local build) is not judged, and its name is not written to the report;
- a ``@sha256:`` digest is counted in the manifest's coverage note ("contents frozen at the pinned digest").

The image **contents** (OS packages and libraries in the layers) are **not scanned**: every container manifest
keeps an unscannable entry saying so (:data:`CONTAINER_REASON`). Coverage keys on file names, so a workspace with
only container manifests stays in the ``unsupported ecosystem`` coverage state.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan import runtimes
from agentteams.vulnscan.manifests import UNSUPPORTED_REASON, unsupported_ecosystem
from agentteams.vulnscan.report import Finding, Report

#: Unscannable reason of every container manifest: the base image is judged, the layers are not.
CONTAINER_REASON = (f"{UNSUPPORTED_REASON}container images (layer contents not scanned; "
                    "base image support status checked)")
#: Largest container manifest read; a bigger one is unscannable.
MAX_BYTES = 1 << 20
#: Image references judged per manifest; the rest are listed as not judged.
MAX_REFS = 256
#: Official image -> :data:`runtimes.PRODUCTS` kind.
IMAGE_PRODUCTS: dict[str, str] = {
    "python": "python", "pypy": "python", "node": "node", "php": "php", "ruby": "ruby", "golang": "go",
    "eclipse-temurin": "java", "openjdk": "java", "amazoncorretto": "java-corretto", "nginx": "nginx",
    "postgres": "postgresql", "mysql": "mysql", "mariadb": "mariadb", "redis": "redis",
}
#: ``mcr.microsoft.com`` repositories -> kind.
MCR_PRODUCTS: dict[str, str] = {"dotnet/runtime": "dotnet", "dotnet/aspnet": "dotnet", "dotnet/sdk": "dotnet"}
#: Kinds whose image tags name a release line in more parts than endoflife.date does (.NET line ``8`` is
#: tagged ``8.0`` on MCR, which has no ``8`` tag): a tag of up to this many parts is a release-line tag.
TAG_LINE_PARTS: dict[str, int] = {"dotnet": 2}
#: Official OS images -> :data:`runtimes.OS_PRODUCTS` key.
OS_IMAGES: dict[str, str] = {"ubuntu": "ubuntu", "debian": "debian", "alpine": "alpine",
                             "amazonlinux": "amazonlinux"}
#: Bare Docker Hub names judged as official images (any other bare name may be a local build: never named).
KNOWN_OFFICIAL = frozenset(IMAGE_PRODUCTS) | frozenset(OS_IMAGES)
#: Registries whose image names may appear in a report and whose tag OS is judged.
PUBLIC_REGISTRIES = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io", "ghcr.io",
                               "mcr.microsoft.com", "quay.io", "public.ecr.aws", "gcr.io", "registry.k8s.io",
                               "registry.gitlab.com"})
_DOCKER_HUB = frozenset({"", "docker.io", "index.docker.io", "registry-1.docker.io"})
#: Tags that name no release line.
FLOATING_KEYWORDS = frozenset({"lts", "stable", "current", "mainline", "edge", "rolling", "devel"})
_UNPINNED = "unpinned tag: rebuilds change silently; release line not judged"
_ARG_LABEL = " (ARG default; `--build-arg` may override)"
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}")
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_ARG = re.compile(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(\S*))?\s*$", re.I)
_LEAD = re.compile(r"^v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class ImageRef:
    """One image reference of a container manifest.

    ``registry`` is ``""`` for Docker Hub's implicit registry; ``repository`` is the path after it
    (``library/python`` is shown as ``python``); ``tag`` and ``digest`` are ``""`` when absent. ``line`` is
    1-based; ``source`` is ``"FROM"`` or ``"image"``; ``arg_default`` marks a reference resolved from an ``ARG``
    default.
    """

    registry: str
    repository: str
    tag: str
    digest: str
    line: int
    source: str
    arg_default: bool = False

    @property
    def public(self) -> bool:
        """Whether the image may be named and judged: a :data:`PUBLIC_REGISTRIES` registry, a Docker Hub
        ``namespace/name``, or a bare official name vulnscan knows. A bare unknown name may be a local build."""
        if self.registry in PUBLIC_REGISTRIES:
            return True
        if self.registry:
            return False
        return ("/" in self.repository and not self.repository.startswith("library/")) or \
            self.official in KNOWN_OFFICIAL

    @property
    def official(self) -> str | None:
        """The Docker Hub official image name (``python``), or None."""
        if self.registry not in _DOCKER_HUB:
            return None
        name = self.repository[len("library/"):] if self.repository.startswith("library/") else self.repository
        return None if "/" in name else name

    def display(self) -> str:
        """Return the image name a report may show: ``python``, ``ghcr.io/x/y``; a private or local image is never named."""
        if not self.public:
            return "(private registry image)" if self.registry else "(local or unlisted image)"
        if self.official is not None:
            return self.official
        return f"{self.registry}/{self.repository}" if self.registry else self.repository


@dataclass
class _Scan:
    refs: list[ImageRef] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def is_container_manifest(name: str) -> bool:
    """Return whether a file name is a container manifest (Dockerfile, Containerfile or compose file).

    Args:
        name: File name.

    Returns:
        True when :func:`manifests.unsupported_ecosystem` calls it ``container images``.
    """
    return unsupported_ecosystem(name) == "container images"


def parse_ref(text: str, line: int, source: str, arg_default: bool = False) -> ImageRef | None:
    """Split an image reference into registry, repository, tag and digest.

    Args:
        text: The reference (``python:3.12-slim``, ``host:5000/x/y:1@sha256:...``).
        line: Its 1-based line.
        source: ``"FROM"`` or ``"image"``.
        arg_default: The reference came from an ``ARG`` default.

    Returns:
        The :class:`ImageRef`, or None for a reference outside the image-reference syntax.
    """
    text = text.strip()
    if not _REF.fullmatch(text) or text.count("@") > 1:
        return None
    name, _, digest = text.partition("@")
    if digest and not _DIGEST.fullmatch(digest):
        return None
    head, _, last = name.rpartition("/")
    tag = ""
    if ":" in last:
        last, _, tag = last.partition(":")
        if not _TAG.fullmatch(tag):
            return None
    path = f"{head}/{last}" if head else last
    if path != path.lower():   # registry and repository are lowercase; only a tag may not be
        return None
    first, sep, rest = path.partition("/")
    registry = ""
    if sep and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    if not path or not last or "//" in path or ":" in path:
        return None
    if registry in ("docker.io", "index.docker.io", "registry-1.docker.io") and "/" not in path:
        path = f"library/{path}"
    return ImageRef(registry, path, tag, digest, line, source, arg_default)


def _read(path: Path, scan: _Scan) -> list[str] | None:
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as exc:
        scan.notes.append(f"could not read: {type(exc).__name__}")
        return None
    if len(data) > MAX_BYTES:
        scan.notes.append(f"larger than {MAX_BYTES} bytes: base images not judged")
        return None
    if b"\0" in data:
        scan.notes.append("binary content: base images not judged")
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        scan.notes.append("not UTF-8 text: base images not judged")
        return None


def _add(scan: _Scan, text: str, line: int, source: str, arg_default: bool) -> None:
    if len(scan.refs) >= MAX_REFS:
        if not any(n.startswith("more than") for n in scan.notes):
            scan.notes.append(f"more than {MAX_REFS} image references: the rest not judged")
        return
    if "$" in text:
        scan.notes.append(f"line {line}: image reference uses an unresolved variable: not judged")
        return
    ref = parse_ref(text, line, source, arg_default)
    if ref is None:
        scan.notes.append(f"line {line}: unusual image reference: not judged")
    else:
        scan.refs.append(ref)


def _dockerfile(lines: list[str], scan: _Scan) -> None:
    args: dict[str, str] = {}
    aliases: set[str] = set()
    seen_from = False
    joined: list[tuple[int, str]] = []
    buf, start = "", 0
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if not buf:
            start = i
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        joined.append((start, buf + stripped))
        buf = ""
    if buf:
        joined.append((start, buf))
    for line, text in joined:
        word = text.split(None, 1)[0].upper() if text.split() else ""
        if word == "ARG" and not seen_from:
            m = _ARG.match(text)
            if m and m.group(2) is not None:
                args[m.group(1)] = m.group(2).strip("\"'")
            continue
        if word != "FROM":
            continue
        seen_from = True
        tokens = [t for t in text.split()[1:] if not t.startswith("--")]
        if not tokens:
            scan.notes.append(f"line {line}: FROM without an image: not judged")
            continue
        ref = tokens[0]
        earlier = set(aliases)
        if len(tokens) >= 3 and tokens[1].lower() == "as":
            aliases.add(tokens[2].lower())
        if ref.lower() in earlier or ref.lower() == "scratch":
            continue
        used = False

        def sub(m: re.Match[str]) -> str:
            nonlocal used
            name = m.group(1) or m.group(2)
            if name in args and args[name]:
                used = True
                return args[name]
            return m.group(0)

        resolved = _VAR.sub(sub, ref)
        if resolved.lower() in earlier or resolved.lower() == "scratch":
            continue
        _add(scan, resolved, line, "FROM", used)


def _compose(lines: list[str], scan: _Scan) -> None:
    in_services, svc_indent, child_indent = False, -1, -1
    service: list[tuple[int, str]] = []   # (line, image) of the current service
    build = False

    def flush() -> None:
        if not build:
            for line, image in service:
                _add(scan, image, line, "image", False)

    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        if indent == 0:
            if in_services:
                flush()
                service, build = [], False
            in_services = text.split("#", 1)[0].strip() == "services:"
            svc_indent = child_indent = -1
            continue
        if not in_services:
            continue
        if svc_indent < 0:
            svc_indent = indent
        if indent <= svc_indent:
            flush()
            service, build, child_indent = [], False, -1
            continue
        if child_indent < 0:
            child_indent = indent
        if indent != child_indent:
            continue
        key, colon, value = text.partition(":")
        if not colon:
            continue
        if key.strip() == "build":
            build = True
        elif key.strip() == "image":
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("\"'")
            if value:
                service.append((i, value))
    if in_services:
        flush()


def refs(path: Path) -> tuple[list[ImageRef], list[str]]:
    """Read the image references of one container manifest.

    Args:
        path: A Dockerfile, Containerfile or compose file.

    Returns:
        ``(references, notes)``: each note is why a reference or the whole file was not judged (an unresolved
        variable, an unusual reference, a file too large, binary or not UTF-8). This function does not raise.
    """
    scan = _Scan()
    lines = _read(path, scan)
    if lines is not None:
        if re.search(r"\.ya?ml$", path.name):
            _compose(lines, scan)
        else:
            _dockerfile(lines, scan)
    return scan.refs, scan.notes


@dataclass
class Judgement:
    """What :func:`judge` decided for one reference."""

    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)        # not judged, and why (an unscannable entry each)
    feed_errors: list[str] = field(default_factory=list)  # endoflife.date unreachable: the run is partial
    checked: int = 0


def _split_tag(tag: str) -> tuple[str, str]:
    """``3.11-slim-bookworm`` -> (``3.11``, ``-slim-bookworm``); ``v1.57.0-jammy`` -> (``1.57.0``, ``-jammy``)."""
    m = _LEAD.match(tag)
    return (m.group(1), tag[m.end():]) if m else ("", tag)


def tag_os(tag: str) -> tuple[str, str] | None:
    """Return the OS a tag names in a suffix or codename, as ``(OS_PRODUCTS key, version or codename)``.

    Args:
        tag: An image tag (``3.12-slim-bookworm``, ``v1.57.0-jammy``, ``1.22-alpine3.19``, ``17-al2023``).

    Returns:
        ``("debian", "bookworm")``, ``("ubuntu", "jammy")``, ``("alpine", "3.19")``, ``("amazonlinux", "2023")``,
        or None when the tag names no versioned OS (``slim``, a bare ``alpine``).
    """
    for token in tag.lower().split("-"):
        for os_id, names in runtimes.OS_CODENAMES.items():
            if token in names:
                return os_id, token
        m = re.fullmatch(r"alpine(\d+\.\d+)", token)
        if m:
            return "alpine", m.group(1)
        m = re.fullmatch(r"al(2|2023)", token)
        if m:
            return "amazonlinux", m.group(1)
    return None


def _finding(ref: ImageRef, rel: str, target: str, severity: int, detail: str, fix: str = "") -> Finding:
    return Finding(kind="image", target=target, name=ref.display(), version=ref.tag or "(no tag)",
                   severity=severity, fix=fix, locations=[f"{rel}:{ref.line}"],
                   detail=detail + (_ARG_LABEL if ref.arg_default else ""))


def _unchecked(v: runtimes.RuntimeFinding, ref: ImageRef, rel: str, out: Judgement) -> None:
    if v.detail.startswith("release data unavailable"):
        out.feed_errors.append(f"{rel}:{ref.line}: {v.kind} {v.version}: {v.detail}")
    else:
        out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: {v.detail}: not judged")


def _judge_os(os_id: str, version: str, ref: ImageRef, rel: str, target: str, out: Judgement,
              today: _dt.date | None) -> None:
    product = runtimes.OS_PRODUCTS[os_id]
    resolved = runtimes.OS_CODENAMES.get(os_id, {}).get(version, version)
    if product.cycle_fn is None and len(resolved.split(".")) < product.cycle:
        out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: floating {product.label} tag: "
                         "release line not determinable")
        return
    v = runtimes.evaluate_os(os_id, version, today=today)
    out.checked += 1
    if v.verdict == "unchecked":
        _unchecked(v, ref, rel, out)
    elif v.verdict == "eol":
        fix = f"{product.label} {runtimes.cycle_of(os_id, v.upgrade_to)}" if v.upgrade_to else ""
        out.findings.append(_finding(ref, rel, target, 3, f"base OS: {v.detail}", fix))


def _judge_product(kind: str, ref: ImageRef, rel: str, target: str, out: Judgement,
                   today: _dt.date | None) -> None:
    product = runtimes.PRODUCTS[kind]
    version, suffix = _split_tag(ref.tag)
    head = ref.tag.lower().split("-")[0]
    if not version:
        why = f"floating tag ({head})" if head in FLOATING_KEYWORDS else "tag without a version"
        out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: {why}: release line not determinable")
        return
    if product.cycle_fn is None and len(version.split(".")) < product.cycle:
        out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: floating major tag: "
                         "release line not determinable")
        return
    v = runtimes.evaluate(kind, version, manager="image", today=today)
    out.checked += 1
    parts = TAG_LINE_PARTS.get(kind, 0)
    def tag_line(ver: str) -> str:   # the release line as the registry tags it
        return ".".join(ver.split(".")[:parts]) if parts else runtimes.cycle_of(kind, ver)
    line_tag = version == runtimes.cycle_of(kind, version) or (parts > 0 and len(version.split(".")) <= parts)
    if v.verdict == "unchecked":
        _unchecked(v, ref, rel, out)
    elif v.verdict == "eol":
        newest = tag_line(v.upgrade_to) if v.upgrade_to else ""
        fix = f"{ref.display()}:{newest}{suffix}" if newest else ""
        out.findings.append(_finding(ref, rel, target, 3, f"{v.detail}; change FROM to a supported line", fix))
    elif not line_tag and v.verdict == "security-release-missing":   # a full-patch tag is never rebuilt
        out.findings.append(_finding(ref, rel, target, 3, f"pinned patch tag: {v.detail}",
                                     f"{ref.display()}:{v.upgrade_to}{suffix}"))
    elif not line_tag and v.verdict == "behind-patch":
        out.findings.append(_finding(ref, rel, target, 2, f"pinned patch tag behind {v.upgrade_to}",
                                     f"{ref.display()}:{v.upgrade_to}{suffix}"))
    # a release-line tag: only eol counts (a rebuild takes the line's newest patch)


def judge(ref: ImageRef, rel: str, target: str, today: _dt.date | None = None) -> Judgement:
    """Judge one image reference: release line, pinning and the OS in its tag.

    Only endoflife.date product slugs are sent anywhere; the image name never is.

    Args:
        ref: The reference.
        rel: The manifest's path for the report (scan-root relative).
        target: The repository, for findings.
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        A :class:`Judgement`: ``kind="image"`` findings, notes for what is not judged, and feed errors.
    """
    out = Judgement()
    if not ref.public:
        out.notes.append(f"line {ref.line}: {ref.display()}: not judged (its name is not sent anywhere)")
        if ref.tag in ("", "latest") and not ref.digest:
            out.findings.append(_finding(ref, rel, target, 1, _UNPINNED))
        return out
    official = ref.official
    kind = IMAGE_PRODUCTS.get(official or "") or (MCR_PRODUCTS.get(ref.repository)
                                                  if ref.registry == "mcr.microsoft.com" else None)
    os_image = OS_IMAGES.get(official or "")
    if official == "openjdk":
        major = _split_tag(ref.tag)[0].split(".")[0]
        out.findings.append(_finding(ref, rel, target, 1, "deprecated image; use eclipse-temurin",
                                     f"eclipse-temurin:{major}" if major else "eclipse-temurin"))
    if ref.tag in ("", "latest"):
        if ref.digest:
            out.notes.append(f"line {ref.line}: {ref.display()}: pinned by digest only: release line not determinable")
        else:
            out.findings.append(_finding(ref, rel, target, 1, _UNPINNED))
        return out
    if os_image:
        version, _ = _split_tag(ref.tag)
        named = tag_os(ref.tag)
        if not version and named and named[0] == os_image:
            version = named[1]
        if version:
            _judge_os(os_image, version, ref, rel, target, out, today)
        else:
            out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: tag without a version: not judged")
        return out
    if kind:
        _judge_product(kind, ref, rel, target, out, today)
    os_tag = tag_os(ref.tag)
    if os_tag:
        _judge_os(os_tag[0], os_tag[1], ref, rel, target, out, today)
    if not kind:
        judged = ""
        if os_tag:
            os_version = runtimes.OS_CODENAMES.get(os_tag[0], {}).get(os_tag[1], os_tag[1])
            judged = f"; base OS {runtimes.OS_PRODUCTS[os_tag[0]].label} {os_version} judged"
        out.notes.append(f"line {ref.line}: {ref.display()}:{ref.tag}: not an official image vulnscan knows: "
                         f"release line not judged{judged}")
    return out


def scan_manifest(path: Path, rel: str, target: str, report: Report, today: _dt.date | None = None) -> None:
    """Judge every base image of one container manifest into ``report``.

    Appends the manifest's :data:`CONTAINER_REASON` (with a digest count when any reference is pinned by digest)
    and one unscannable entry per reference not judged, the findings, and feed errors. A reference repeated on
    several lines is judged once and located at each. This function does not raise for bad input.

    Args:
        path: The container manifest.
        rel: Its path for the report.
        target: The repository, for findings.
        report: Report to extend.
        today: Date for EOL comparison (tests); defaults to today.
    """
    found, notes = refs(path)
    pinned = sum(1 for r in found if r.digest)
    reason = CONTAINER_REASON
    if pinned:
        reason = reason[:-1] + f"; {pinned} pinned by digest: contents frozen at the pinned digest)"
    report.unscannable.append((rel, reason))
    report.unscannable += [(rel, n) for n in notes]
    groups: dict[tuple[str, str, str, str, bool], list[ImageRef]] = {}
    for r in found:
        groups.setdefault((r.registry, r.repository, r.tag, r.digest, r.arg_default), []).append(r)
    for same in groups.values():
        j = judge(same[0], rel, target, today)
        report.checked += j.checked
        report.unscannable += [(rel, n) for n in j.notes]
        report.feed_errors += j.feed_errors
        for f in j.findings:
            f.locations = [f"{rel}:{r.line}" for r in same][:20]
            report.findings.append(f)


def plan(path: Path, rel: str, today: _dt.date | None = None) -> tuple[list[Finding], list[str]]:
    """Return what a fix plan lists for one container manifest: its image findings and feed errors.

    Args:
        path: The container manifest.
        rel: Its path for the plan.
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        ``(findings, feed errors)``; every finding is plan-only (nothing edits a Dockerfile or compose file).
    """
    rep = Report(target=rel)
    scan_manifest(path, rel, rel, rep, today)
    return rep.findings, rep.feed_errors
