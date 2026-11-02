"""runner_wasm.py — the runner's ``sandbox: wasm`` jails (WASM sandbox plan P12; D2 strong mode, D6 nesting).

Under ``write_policy: "orchestrator-only"`` an operator may declare ``confined_programs.<agent>.sandbox = "wasm"``.
That agent then has **no native program list**: each queued command names a guest, ``wasm:<lang> <path> [args]``, and
the runner (the sole executor) runs it as the pinned wasmtime inside a fresh Seatbelt/bwrap wrap, never natively.

* :func:`validate_entry` checks the jail shape for :func:`agentteams.confinement.load_confined`.
* :func:`prepare_command` turns a ``wasm:`` argv into a verified guest (``wasm_guests.argv_for``) before any ledger
  row: wasmtime and the interpreter are re-hashed against the pins, the source is read without following symlinks.
* :func:`launch` hands it to ``wasm_exec.materialize`` with ``os_confine="require"`` (or ``"off"`` under the logged
  ``allow_unconfined_runs`` opt-out, with a notice). The wrap is built from the same ``_sandbox_profiles`` builders
  and protected paths as the runner's own; the runner owns the ``Popen``. Executables run from the install dir,
  re-hashed just before exec (R3'), because the runner only serves teams whose session sandboxes write-deny
  ``.agentteams/``; any copy lands in ``.agentteams/wasm/exec/<run-id>/``, never TMPDIR.
* :func:`program_modes` / :func:`runner_exceptions` fill ``SandboxExceptions.wasm_programs`` / ``os_programs`` for
  the P15 gate; :func:`check_switch` runs that gate for ``wasm-to-os-switch``.

Rule 12 carve-outs (each an audit target; see ``P12-runner-wasm.plan.md``): the wasm branch skips
``_resolve_program``'s refusal of programs inside the project (only the pinned, re-hashed wasmtime under
``.agentteams/wasm/`` runs); a wasm jail has no operator exec list (the wrap's only exec rule is that one literal);
the agent's scratch, ``.agentteams/wasm/scratch/<agent>/``, is the one write-allow inside a protected path, granted only
to this path and exempt from the undeclared-writes diff only when an OS sandbox bounded the run (R11).

Stdlib only; integrity-pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteams import _sandbox_profiles as _profiles
from agentteams import wasm_exec as _wx
from agentteams import wasm_guests as _wg
from agentteams._sandbox_profiles import ConfinementError

#: The two runner sandboxes a ``confined_programs`` entry may declare (``"os"`` is the default).
SANDBOX_KINDS = ("os", "wasm")
#: The pseudo-programs a wasm agent's ``argv[0]`` may name, and the guest language each selects (derived from
#: ``wasm_guests.LANGUAGES``, the single source).
WASM_ARGV0 = {f"wasm:{lang}": lang for lang in _wg.LANGUAGES}
#: File extensions ``argv[1]`` must carry for each language.
EXTENSIONS = {"module": (".wasm",), "python": (".py",), "javascript": (".js", ".mjs")}
#: The per-agent scratch parent (R8; == ``sandbox_state.SCRATCH_REL``, test-locked).
SCRATCH_REL = ".agentteams/wasm/scratch"
#: The one agent-writable path inside a protected tree a wasm jail adds (R11): ``<SCRATCH_REL>/<agent>``.
SCRATCH_GUEST = "/scratch"

#: VCS internals and the control plane / queue: never preopened (beside the credential names).
VCS_PARTS = (".git", ".hg", ".svn", ".agentteams", ".agentteams-queue")
# Never preopened, whatever the root (== the agentteams_runner server's read denies, test-locked). The credential
# names are the one shared list in ``_sandbox_profiles`` (wave-3 @security S1), the same one wasm_config uses.
DENY_PARTS = VCS_PARTS + _profiles.CREDENTIAL_PARTS
DENY_SUFFIXES = _profiles.CREDENTIAL_SUFFIXES
DENY_NAMES = _profiles.CREDENTIAL_NAMES
DENY_PREFIXES = _profiles.CREDENTIAL_PREFIXES
DENY_INFIXES = _profiles.CREDENTIAL_INFIXES
DENY_ALLOWED = _profiles.CREDENTIAL_ALLOWED

_ENTRY_KEYS = frozenset({"sandbox", "wasm"})
_WASM_KEYS = frozenset({"languages", "read", "scratch", "limits"})
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$", re.ASCII)
_AGENT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class RunnerWasmError(ValueError):
    """A ``sandbox: wasm`` command is refused (the caller turns it into a ``ProposalError``)."""


# --- the entry shape ----------------------------------------------------------------------------------------------

def denied_name(segment: str) -> bool:
    """Whether a path segment names a secret or VCS/control-plane location no guest may see.

    Args:
        segment: One path component.

    Returns:
        True for a :data:`VCS_PARTS` name or a shared credential name (``_sandbox_profiles.credential_name``).

    Raises:
        Nothing.
    """
    return segment.lower() in VCS_PARTS or _profiles.credential_name(segment)


def _check_rel(agent: str, what: str, rel: object, protected_rel: tuple[str, ...],
               control_plane_of: Callable[[str], Any]) -> str:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or "\\" in rel:
        raise ConfinementError(f"confined_programs.{agent}.{what} entries must be project-relative paths")
    parts = rel.strip("/").split("/")
    if any(p in ("", ".", "..") or not _SEGMENT.match(p) for p in parts):
        raise ConfinementError(f"confined_programs.{agent}.{what} {rel!r} must be a literal path of "
                               "[A-Za-z0-9._-] segments (no '.', '..' or wildcard)")
    # Case-folded on every platform (wave-3 @security S4): on a case-insensitive filesystem (darwin's default)
    # ``.GITHUB`` is ``.github``; on a case-sensitive one this only over-refuses.
    if (parts[0].lower() in {p.lower() for p in protected_rel} or control_plane_of(rel)
            or any(denied_name(p) for p in parts)):
        raise ConfinementError(f"confined_programs.{agent}.{what} {rel!r} covers the ledger, .git, the control "
                               "plane or a secret; refused")
    return "/".join(parts)


def _limits(agent: str, raw: object) -> dict[str, int]:
    """The jail limits through ``wasm_config.limits_of`` (the one MiB -> bytes conversion; wave-3 H2)."""
    from agentteams.wasm_config import limits_of

    try:
        limits = limits_of(raw, f"confined_programs.{agent}.wasm.limits")
    except ValueError as exc:
        raise ConfinementError(str(exc)) from exc
    return {"fuel": limits.fuel, "max_memory_bytes": limits.max_memory_bytes, "timeout_s": limits.timeout_s}


def validate_entry(agent: str, spec: dict[str, Any], control_plane_of: Callable[[str], Any], *,
                   protected_rel: tuple[str, ...]) -> dict[str, Any]:
    """Validate one ``sandbox: wasm`` entry of ``confined_programs``.

    ``exec`` is refused (the only program is the pinned wasmtime, so an operator list would be a second, unreviewed
    allowlist), as are ``write`` (wasmtime gets no project write root; a guest writes only its scratch, and no
    copy-out exists yet) and the xcrun opt-in (no launcher is ever involved).

    Args:
        agent: The agent slug (it names the scratch directory, so it must be a plain slug).
        spec: The raw entry.
        control_plane_of: As for :func:`agentteams.confinement.load_confined`.
        protected_rel: ``confinement.PROTECTED_REL``.

    Returns:
        ``{"sandbox": "wasm", "exec": [], "write": [], "wasm": {"languages", "read", "scratch", "limits"}}`` with
        ``limits`` as ``{"fuel", "max_memory_bytes", "timeout_s"}``.

    Raises:
        ConfinementError: On any malformed or unsafe entry.
    """
    if "exec" in spec:
        raise ConfinementError(f"confined_programs.{agent}: a sandbox: wasm entry takes no exec list (the only "
                               "program is the pinned wasmtime); remove it")
    if "write" in spec:
        raise ConfinementError(f"confined_programs.{agent}: a sandbox: wasm entry takes no write roots (a guest "
                               "writes only its scratch); use wasm.scratch")
    if set(spec) - _ENTRY_KEYS:
        raise ConfinementError(f"confined_programs.{agent}: a sandbox: wasm entry takes only sandbox and wasm "
                               f"(not {', '.join(sorted(set(spec) - _ENTRY_KEYS))})")
    if not _AGENT.match(agent):
        raise ConfinementError(f"confined_programs.{agent}: a sandbox: wasm agent must be a plain slug")
    w = spec.get("wasm")
    if not isinstance(w, dict) or set(w) - _WASM_KEYS:
        raise ConfinementError(f"confined_programs.{agent}.wasm must be an object of {sorted(_WASM_KEYS)}")
    langs = w.get("languages")
    if not (isinstance(langs, list) and langs and len(set(map(str, langs))) == len(langs)
            and all(isinstance(x, str) and x in _wg.LANGUAGES for x in langs)):
        raise ConfinementError(f"confined_programs.{agent}.wasm.languages must be distinct names from "
                               f"{', '.join(_wg.LANGUAGES)} (at least one)")
    read = w.get("read") or []
    if not isinstance(read, list):
        raise ConfinementError(f"confined_programs.{agent}.wasm.read must be a list")
    rels = [_check_rel(agent, "wasm.read", r, protected_rel, control_plane_of) for r in read]
    if len(set(rels)) != len(rels):
        raise ConfinementError(f"confined_programs.{agent}.wasm.read lists a path twice")
    scratch = w.get("scratch", False)
    if not isinstance(scratch, bool):
        raise ConfinementError(f"confined_programs.{agent}.wasm.scratch must be true or false")
    return {"sandbox": "wasm", "exec": [], "write": [],
            "wasm": {"languages": list(langs), "read": rels, "scratch": scratch, "limits": _limits(agent, w.get("limits"))}}


def program_modes(raw: object, *, reserved: tuple[str, ...] = ()) -> tuple[frozenset[str], frozenset[str]]:
    """Which agents of a raw ``confined_programs`` object are on ``sandbox: wasm`` and which on ``os``.

    Read from the raw object (the gate must see a switch even in an entry ``load_confined`` would refuse).

    Args:
        raw: ``confined_programs`` (operator file or brief); anything else counts as empty.
        reserved: Operator-file keys that are not agents (``gate_exec``, ``gate_argv_sha256``).

    Returns:
        ``(wasm_programs, os_programs)``; only plain slugs are counted.

    Raises:
        Nothing.
    """
    wasm: set[str] = set()
    native: set[str] = set()
    for agent, spec in (raw.items() if isinstance(raw, dict) else ()):
        if agent in reserved or not isinstance(spec, dict) or not isinstance(agent, str) or not _AGENT.match(agent):
            continue
        (wasm if spec.get("sandbox") == "wasm" else native).add(agent)
    return frozenset(wasm), frozenset(native)


def effective_raw(root: Path, brief_confined: object) -> object:
    """The ``confined_programs`` the runner would load: the operator file when present, else the brief's block.

    Args:
        root: The project root.
        brief_confined: The brief's ``confined_programs`` (or None).

    Returns:
        The raw object.

    Raises:
        ConfinementError: The operator file fails its custody checks (fail closed).
    """
    from agentteams import confinement as C

    found = C.read_confined_file(root)
    return found[0] if found is not None else (brief_confined or {})


def runner_exceptions(raw: object, *, native_exec_keep: frozenset[str] = frozenset()) -> Any:
    """Build the P15 gate input ``SandboxExceptions`` from a raw ``confined_programs`` object.

    Args:
        raw: As :func:`program_modes`.
        native_exec_keep: P13's keep set (``wasm_enforcement.gate_keep_set``: each a ``native-exec-keep`` effect).

    Returns:
        A :class:`~agentteams.sandbox_state.SandboxExceptions`.

    Raises:
        Nothing.
    """
    from agentteams.proposal_policy import GATE_ARGV_KEY, GATE_EXEC_KEY
    from agentteams.sandbox_state import SandboxExceptions

    wasm, native = program_modes(raw, reserved=(GATE_EXEC_KEY, GATE_ARGV_KEY))
    return SandboxExceptions(native_exec_keep=native_exec_keep, wasm_programs=wasm, os_programs=native)


def switch_relaxations(previous: Any, new: Any) -> list[Any]:
    """The ``wasm-to-os-switch`` relaxations *new* makes over *previous* (P15 ``plan_relaxations``).

    Args:
        previous: The previous ``SandboxExceptions`` (or None: nothing was recorded).
        new: The new ``SandboxExceptions``.

    Returns:
        The switch relaxations only, new and continuing.

    Raises:
        Nothing.
    """
    from agentteams.cli import sandbox_relaxation_gate as gate
    from agentteams.cli.sandbox_effects import WASM_TO_OS_SWITCH
    from agentteams.sandbox_state import SandboxState

    neutral = SandboxState(os=True, os_reason="runner", wasm=False, wasm_reason="runner", wasm_languages=(),
                           wasm_grants={}, model_substrate=None)
    return [r for r in gate.plan_relaxations(neutral, neutral, framework_id="runner", profile_would_emit=False,
                                             exceptions=(previous, new)) if r.kind == WASM_TO_OS_SWITCH]


def check_switch(team_dir: Path | None, previous: Any, new: Any, *, consume: bool) -> list[dict[str, str]]:
    """Run the P15 gate for every agent *new* moves from ``sandbox: wasm`` back to ``os`` (D7, Rule 15).

    In a governed workspace each switch needs the operator's Ed25519-signed decision ``wasm-to-os-switch:<agent>``;
    elsewhere a ``SANDBOX RELAXATION`` notice is printed. With no team dir and a new switch, it refuses (the
    decisions log cannot be found, so the workspace may be governed: fail closed).

    Args:
        team_dir: The team directory (decisions log, registry, signing switch), or None when none was found.
        previous: The previous ``SandboxExceptions`` (or None).
        new: The new ``SandboxExceptions``.
        consume: Consume the decision and register the standing exception (render); False only checks.

    Returns:
        The gate's records (empty when nothing switches).

    Raises:
        RunnerWasmError: The gate refuses, or a switch has no team directory to be checked against.
    """
    from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError, authorize

    relaxations = switch_relaxations(previous, new)
    fresh = [r for r in relaxations if not r.continuing]
    if team_dir is None:
        if fresh:
            raise RunnerWasmError(f"{', '.join(r.action for r in fresh)}: no team directory with a build-log was "
                                  "found to check the operator's signed decision against; pass --output <team dir>")
        return []
    try:
        records = authorize(Path(team_dir), relaxations, consume=consume)
    except SandboxRelaxationError as exc:
        raise RunnerWasmError(str(exc)) from exc
    for rec in records:
        if rec.get("authorization") == "notice-only":
            print(f"  !  SANDBOX RELAXATION: {rec['action']}: the runner will run this agent's commands natively "
                  "(OS sandbox only), not inside wasmtime", file=sys.stderr)
    return records


def team_dirs(root: Path, output: str | Path | None = None) -> list[Path]:
    """The team directories whose P15 gate a runner-policy change must clear.

    Args:
        root: The project root.
        output: An explicit ``--output`` team dir (then only that one).

    Returns:
        ``[output]``, else every registered framework's agents dir under *root* that holds
        ``references/build-log.json`` (a multi-framework project has several; each is governed on its own).

    Raises:
        Nothing.
    """
    if output:
        return [Path(output).resolve()]
    from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for

    found: list[Path] = []
    for fid in FRAMEWORK_IDS:   # every id is registered, so agents_dir_for never raises here
        d = Path(agents_dir_for(fid, Path(root))).resolve()
        if (d / "references" / "build-log.json").is_file() and d not in found:
            found.append(d)
    return found


def gate_policy_change(root: Path, previous_raw: object, new_raw: object, *, output: str | Path | None = None,
                       consume: bool = False) -> list[dict[str, str]]:
    """Clear every ``wasm-to-os-switch`` a ``confined_programs`` change makes, in each team dir (P15 gate).

    Args:
        root: The project root.
        previous_raw: The ``confined_programs`` in force before (operator file or brief).
        new_raw: The candidate ``confined_programs``.
        output: An explicit team dir.
        consume: As :func:`check_switch` (``--install-confined`` checks only; the render consumes).

    Returns:
        The gate records of every team dir.

    Raises:
        RunnerWasmError: A switch is refused, or no team dir could be found for a switch.
    """
    previous, new = runner_exceptions(previous_raw), runner_exceptions(new_raw)
    dirs = team_dirs(root, output)
    if not dirs:
        return check_switch(None, previous, new, consume=consume)
    return [rec for d in dirs for rec in check_switch(d, previous, new, consume=consume)]


# --- run time -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WasmCommand:
    """A verified guest run, ready for :func:`launch` (nothing written yet).

    Attributes:
        language: The guest language (a ``wasm_guests.LANGUAGES`` entry).
        prepared: The ``wasm_exec.Prepared`` guest (module hash, grant, runtime mounts, ``/src`` files).
        guest_args: The guest's own arguments, after ``argv_for``.
        resolver: The provisioning resolver ``materialize`` re-hashes wasmtime and the interpreter with.
        scratch_rel: ``.agentteams/wasm/scratch/<agent>`` when the jail grants scratch, else None.
        meta: The ledger fields (language, path, module/source/grant/wasmtime sha256).
    """

    language: str
    prepared: Any
    guest_args: tuple[str, ...]
    resolver: Any
    scratch_rel: str | None
    meta: dict[str, Any]


def _guest_path(root: str, rel: object, reads: list[str], language: str) -> tuple[str, str]:
    """Check ``argv[1]``: a project-relative regular file under a ``wasm.read`` path; return ``(rel, real)``."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or "\\" in rel:
        raise RunnerWasmError("argv[1] must be a project-relative path to the guest file")
    parts = rel.split("/")
    if any(p in ("", ".", "..") or not _SEGMENT.match(p) or denied_name(p) for p in parts):
        raise RunnerWasmError(f"argv[1] {rel!r} must be a literal path of [A-Za-z0-9._-] segments, not a secret")
    if not rel.lower().endswith(EXTENSIONS[language]):
        raise RunnerWasmError(f"a {language} guest must be a {'/'.join(EXTENSIONS[language])} file")
    if not any(rel == r or rel.startswith(r + "/") for r in reads):
        raise RunnerWasmError(f"argv[1] {rel!r} is outside this agent's wasm.read paths")
    path = os.path.join(root, *parts)
    real = os.path.realpath(path)
    if real != path or not _wx.path_inside(real, root):
        raise RunnerWasmError(f"argv[1] {rel!r} is or passes through a symlink; refused")
    try:
        st = os.lstat(real)
    except OSError as exc:
        raise RunnerWasmError(f"argv[1] {rel!r} cannot be read ({exc.strerror})") from exc
    if not stat.S_ISREG(st.st_mode):
        raise RunnerWasmError(f"argv[1] {rel!r} must be a regular file")
    return rel, real


def _read_source(real: str) -> bytes:
    fd = os.open(real, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RunnerWasmError("the guest source must be a regular file")
        data = b""
        while len(data) <= _wg.MAX_SOURCE_BYTES:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return data
            data += chunk
    finally:
        os.close(fd)
    raise RunnerWasmError(f"the guest source exceeds {_wg.MAX_SOURCE_BYTES} bytes")


def build_grant(root: Path, agent: str, jail: Mapping[str, Any], *, timeout_s: int) -> Any:
    """The ``wasm_exec.Grant`` for one run: ``/project/<rel>`` (ro) per ``wasm.read``, ``/scratch`` (rw) if set.

    Args:
        root: The project root.
        agent: The agent slug.
        jail: The validated jail.
        timeout_s: The runner's timeout for this command; the guest's limit never exceeds it.

    Returns:
        The grant.

    Raises:
        RunnerWasmError: A read path is not an existing directory or holds credential-named entries (bounded scan),
            or the grant is refused.
    """
    from agentteams.cli.sandbox_effects import read_token, scratch_token
    from agentteams.sandbox_state import grant_from_tokens

    real_root = os.path.realpath(root)
    w = jail["wasm"]
    for rel in w["read"]:
        host = os.path.join(real_root, *rel.split("/"))
        if os.path.realpath(host) != host or not os.path.isdir(host):
            raise RunnerWasmError(f"wasm.read {rel!r} must be an existing real directory (no symlinks)")
    lim = w["limits"]
    try:
        tokens = [read_token(agent, rel) for rel in w["read"]] + ([scratch_token(agent)] if w["scratch"] else [])
        limits = _wx.Limits(fuel=lim["fuel"], max_memory_bytes=lim["max_memory_bytes"],
                            timeout_s=max(1, min(int(lim["timeout_s"]), int(timeout_s))))
        # The one token -> Mount mapping (wave-3 H2): symlink and credential checks included.
        return grant_from_tokens(agent, tokens, limits, project_root=Path(real_root))
    except (_wx.WasmExecError, ValueError) as exc:
        raise RunnerWasmError(f"wasm grant refused: {exc}") from exc


def grant_digest(root: Path, grant: Any) -> str:
    """sha256 of the grant's canonical JSON (mounts by project-relative host and guest name, and limits).

    Args:
        root: The project root (hosts are recorded relative to it).
        grant: The grant.

    Returns:
        The hex digest.

    Raises:
        Nothing.
    """
    real_root = os.path.realpath(root)
    doc = {"mounts": [[os.path.relpath(m.host, real_root), m.guest, m.mode] for m in grant.mounts],
           "limits": [grant.limits.fuel, grant.limits.max_memory_bytes, grant.limits.timeout_s]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _resolver(root: Path) -> Any:
    from agentteams import wasm_runtime

    return wasm_runtime.Resolver(Path(root))


def prepare_command(root: Path, agent: str, argv: list[str], jail: Mapping[str, Any], *, timeout_s: int,
                    resolver: Any | None = None) -> WasmCommand:
    """Validate a ``wasm:<lang> <path> [args]`` request and prepare its guest; nothing is written or spawned.

    wasmtime and the interpreter are re-hashed against the pins here, before any ledger row (fail closed).

    Args:
        root: The project root.
        agent: The agent.
        argv: The request's argv.
        jail: The agent's validated ``sandbox: wasm`` jail.
        timeout_s: The runner's timeout for this command.
        resolver: Provisioning (``wasm_runtime.Resolver`` by default; a test seam).

    Returns:
        A :class:`WasmCommand`.

    Raises:
        RunnerWasmError: On any refusal (unknown pseudo-program, language not granted, path, runtime, grant).
    """
    language = WASM_ARGV0.get(argv[0])
    if language is None:
        raise RunnerWasmError(f"{agent} is on sandbox: wasm; argv[0] must be one of {', '.join(WASM_ARGV0)}, "
                              f"not {argv[0]!r} (nothing runs natively)")
    if language not in jail["wasm"]["languages"]:
        raise RunnerWasmError(f"{agent}'s wasm jail does not grant {language}")
    if len(argv) < 2:
        raise RunnerWasmError(f"{argv[0]} needs the guest file as argv[1]")
    real_root = os.path.realpath(root)
    rel, real = _guest_path(real_root, argv[1], jail["wasm"]["read"], language)
    grant = build_grant(root, agent, jail, timeout_s=timeout_s)
    resolver = resolver if resolver is not None else _resolver(root)
    try:
        resolver.resolve("wasmtime")   # fail closed now, before the ledger row; materialize re-hashes it again
        wasmtime_sha = resolver.expected_sha256("wasmtime")
        if language == "module":
            prepared, guest_args = _wg.argv_for("module", None, real, args=argv[2:], grant=grant,
                                                project_root=real_root, resolver=resolver, executor=_wx)
            source_sha = prepared.module_sha256
        else:
            source = _read_source(real)
            prepared, guest_args = _wg.argv_for(language, source, None, args=argv[2:], grant=grant,
                                                project_root=real_root, resolver=resolver, executor=_wx)
            source_sha = hashlib.sha256(source).hexdigest()
    except (_wg.WasmGuestError, _wx.WasmExecError, OSError, ValueError, LookupError, RuntimeError) as exc:
        if isinstance(exc, RunnerWasmError):
            raise
        raise RunnerWasmError(f"wasm runtime refused: {exc}") from exc
    for notice in _wg.limit_notices(language, grant.limits):
        print(f"[runner] WASM SANDBOX: {notice}", file=sys.stderr)
    scratch = f"{SCRATCH_REL}/{agent}" if jail["wasm"]["scratch"] else None
    meta = {"language": language, "path": rel, "module_sha256": prepared.module_sha256, "source_sha256": source_sha,
            "grant_sha256": grant_digest(root, grant), "wasmtime_sha256": wasmtime_sha}
    return WasmCommand(language=language, prepared=prepared, guest_args=tuple(guest_args), resolver=resolver,
                       scratch_rel=scratch, meta=meta)


def launch(cmd: WasmCommand, root: Path, *, sandbox: str | None) -> Any:
    """Materialize the guest run: verified executables, the agent scratch, and a fresh OS wrap (D6).

    Args:
        cmd: From :func:`prepare_command`.
        root: The project root.
        sandbox: ``"seatbelt"`` / ``"bwrap"`` from the runner's probe, or None under the logged opt-out (the
            guest still runs inside wasmtime; a notice says no OS sandbox bounds it).

    Returns:
        A ``wasm_exec.Launch`` (argv already wrapped, empty environment); the caller must call ``cleanup()``.

    Raises:
        RunnerWasmError: On any refusal, including an OS wrap that cannot be applied (fail closed).
    """
    if sandbox is None:
        print("[runner] WASM only: no OS sandbox (allow_unconfined_runs); the guest runs inside wasmtime alone",
              file=sys.stderr)
    try:
        return _wx.materialize(cmd.prepared, list(cmd.guest_args), project_root=root,
                               os_confine="require" if sandbox else "off", resolver=cmd.resolver,
                               profiles=_profiles, control_plane_protected=True)
    except _wx.WasmExecError as exc:
        raise RunnerWasmError(f"wasm run refused: {exc}") from exc


def exec_literal(argv: list[str]) -> str:
    """The wasmtime path inside a (possibly wrapped) launch argv: the element before ``run -C``.

    Args:
        argv: ``Launch.argv``.

    Returns:
        The absolute wasmtime path.

    Raises:
        RunnerWasmError: When the argv has no wasmtime invocation.
    """
    for i in range(len(argv) - 2):
        if argv[i + 1] == "run" and argv[i + 2] == "-C" and os.path.isabs(argv[i]):
            return argv[i]
    raise RunnerWasmError("the wasm launch argv has no wasmtime invocation")


def check_runtimes(root: Path, confined: Mapping[str, Mapping[str, Any]], *, resolver: Any | None = None) -> None:
    """Runner start: every runtime a wasm jail needs must be installed and match its pins (fail closed at start).

    Args:
        root: The project root.
        confined: The policy's validated jails.
        resolver: Provisioning (``wasm_runtime.Resolver`` by default; a test seam).

    Returns:
        None.

    Raises:
        RunnerWasmError: Naming the agent and the component that is absent or does not match.
    """
    needed: dict[str, str] = {}
    for agent, jail in sorted(confined.items()):
        if jail.get("sandbox") == "wasm":
            needed.setdefault("wasmtime", agent)
            for lang in jail["wasm"]["languages"]:
                if lang != "module":
                    needed.setdefault(lang, agent)
    if not needed:
        return
    resolver = resolver if resolver is not None else _resolver(root)
    for component, agent in needed.items():
        try:
            resolver.resolve(component)
            if component != "wasmtime":
                resolver.runtime_mounts(component)
        except (OSError, ValueError, LookupError, RuntimeError) as exc:
            raise RunnerWasmError(f"confined_programs.{agent} is on sandbox: wasm but the {component} runtime is "
                                  f"not usable: {exc}") from exc


__all__ = [
    "EXTENSIONS",
    "SANDBOX_KINDS",
    "SCRATCH_REL",
    "VCS_PARTS",
    "WASM_ARGV0",
    "RunnerWasmError",
    "WasmCommand",
    "build_grant",
    "check_runtimes",
    "check_switch",
    "denied_name",
    "effective_raw",
    "exec_literal",
    "gate_policy_change",
    "grant_digest",
    "launch",
    "prepare_command",
    "program_modes",
    "runner_exceptions",
    "switch_relaxations",
    "team_dirs",
    "validate_entry",
]
