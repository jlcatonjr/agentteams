"""proposal_policy.py — the registered policy for the orchestrator-only-writes pilot.

Carved out of :mod:`agentteams.proposals` when P4b pushed that module to the CH-07 ceiling. This half only
reads and validates the brief: ``agent_policies``, ``proposal_gates``, ``protected_paths``,
``confined_programs`` and ``allow_unconfined_runs``. It writes nothing and runs nothing. ``proposals``
re-exports every name, so no import changed. Stdlib only; integrity-pinned.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from agentteams import confinement as _confinement
from agentteams.frameworks._write_roots import control_plane_of

#: Proposal content above this many bytes is refused; the orchestrator must review and write it itself.
DEFAULT_SIZE_CAP = 256 * 1024
#: The longest per-entry command timeout (P5a); ``proposals`` re-exports it.
MAX_COMMAND_TIMEOUT = 7200
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh"})
_GLOB_CHARS = "*?["
#: P5c: the operator file's reserved key for gates' exec paths. The underscore can't collide with an agent slug.
GATE_EXEC_KEY = "gate_exec"
#: The operator file's record of each ``gate_exec`` gate's argv digest, written by ``--install-confined``: a gate
#: whose brief argv changed since install no longer gets the file's exec (stale binding; P5c @security advisory).
GATE_ARGV_KEY = "gate_argv_sha256"
_RESERVED_KEYS = (GATE_EXEC_KEY, GATE_ARGV_KEY)


def gate_argv_digest(argv: list[str]) -> str:
    """The digest that binds an operator ``gate_exec`` entry to the gate's argv in the brief.

    Args:
        argv: The gate's ``argv`` from ``proposal_gates``.

    Returns:
        The sha256 of the argv as compact JSON.

    Raises:
        TypeError: When *argv* isn't JSON-serialisable.
    """
    return hashlib.sha256(json.dumps(list(argv), separators=(",", ":")).encode("utf-8")).hexdigest()


class ProposalError(Exception):
    """A proposal or request is refused (the message says why)."""


@dataclass
class Policy:
    """A team's registered orchestrator-only-writes policy."""

    agent_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)
    size_cap: int = DEFAULT_SIZE_CAP
    brief_rel: str | None = None
    #: P4b: per-agent sandbox config ``{agent: {"exec": [paths], "write": [rel roots]}}``.
    confined: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    #: P4b decision A: an explicit, ledger-logged opt-out for machines with no usable sandbox.
    allow_unconfined: bool = False
    #: P5b: the sha256 of the operator-owned confined_programs file the policy came from (None: from the brief).
    confined_file_sha: str | None = None


# --- policy -------------------------------------------------------------------------------------


_PROBE_STEMS = ("x", "x.lean", "x.py", "x.json", "x.txt", "x.md", "1", "CX-1", "a-b", "MathAgents")
_PROBE_FORMS = {"-{}": "an option", "--{}": "an option", "--plugin={}": "an option", "../{}": "a parent path",
                "a/../{}": "a parent path", "a/../../{}": "a parent path", "b/../{}": "a parent path",
                "z/../{}": "a parent path", "x/../{}": "a parent path", "foo/../{}": "a parent path",
                "0/../{}": "a parent path", "/{}": "an absolute path",
                "/etc/{}": "an absolute path", "~/{}": "a home path"}


def _pattern_too_broad(pattern: str) -> str | None:
    """Probe a pattern with option-, parent-, absolute- and home-shaped strings around common stems.

    A sampling lint, not a proof: it refuses the shapes that reopen the argv[0] hole (``.*``, ``[a-z].*``,
    ``.+\\.lean``), and the residual is documented.
    """
    for form, what in _PROBE_FORMS.items():
        for stem in _PROBE_STEMS:
            if re.fullmatch(pattern, form.format(stem)):
                return what
    return None


def _writes_glob_problem(glob: str, brief_rel: str | None) -> str | None:
    g = str(glob).replace("\\", "/")
    first = g.split("/", 1)[0]
    if not first or any(c in first for c in "*?[") or g.startswith(("/", "~", "..")):
        return "must start with a literal project directory"
    if control_plane_of(first, platform="darwin"):
        return "is inside the project control plane"
    if brief_rel and fnmatch.fnmatch(brief_rel.lower(), g.lower()):
        return "covers the brief"
    return None


def load_policy(brief: dict[str, Any], *, brief_rel: str | None = None,
                confined_file: tuple[dict[str, Any], str] | None = None) -> Policy:
    """Build a :class:`Policy` from a project description, validating its shape and linting patterns.

    Args:
        brief: The parsed brief (``agent_policies``, ``proposal_gates``, ``protected_paths``).
        brief_rel: The brief's project-relative path, which no proposal may change (``None`` when outside).
        confined_file: P5b: ``(confined_programs, sha256)`` from the operator-owned file
            (:func:`agentteams.confinement.read_confined_file`), used instead of the brief's block.

    Returns:
        The policy.

    Raises:
        ProposalError: A field has the wrong shape, a pattern does not compile or admits options or
            parent/absolute paths, a gate templates ``{file}``/``{path}`` inside a larger argument or runs a
            shell, a write scope covers the brief or is an unsafe pattern, or both the brief and the operator
            file define ``confined_programs``. P5c: an agent is named ``gate_exec``; the brief's
            ``confined_programs`` uses that reserved key (``gate_argv_sha256`` likewise); or the operator file's
            ``gate_exec`` is malformed, names a gate the brief doesn't define, sets a gate whose brief entry
            already sets ``exec``, lacks its ``gate_argv_sha256`` binding, or is bound to an argv the brief has
            since changed.
    """
    agents = brief.get("agent_policies") or {}
    gates = brief.get("proposal_gates") or {}
    protected = brief.get("protected_paths") or []
    if not isinstance(agents, dict) or not isinstance(gates, dict) or not isinstance(protected, list):
        raise ProposalError("agent_policies and proposal_gates must be objects, protected_paths a list")
    for name, gate in gates.items():
        argv = gate.get("argv") if isinstance(gate, dict) else None
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise ProposalError(f"proposal_gates.{name} needs a non-empty argv list of strings")
        if os.path.basename(argv[0]) in _SHELLS:
            raise ProposalError(f"proposal_gates.{name} runs a shell; give the gate's own program instead")
        for arg in argv:
            if ("{file}" in arg or "{path}" in arg) and arg not in ("{file}", "{path}"):
                raise ProposalError(f"proposal_gates.{name}: {{file}}/{{path}} must be whole arguments, not {arg!r}")
    for reserved in _RESERVED_KEYS:
        if reserved in agents:
            raise ProposalError(f"agent_policies.{reserved}: that name is reserved for the operator file")
    for name, pol in agents.items():
        if not isinstance(pol, dict):
            raise ProposalError(f"agent_policies.{name} must be an object")
        for entry in pol.get("commands") or []:
            prefix = entry.get("prefix") if isinstance(entry, dict) else None
            if not (isinstance(prefix, list) and prefix and all(isinstance(p, str) for p in prefix)):
                raise ProposalError(f"agent_policies.{name}.commands needs non-empty string prefixes")
            if os.path.basename(prefix[0]) in _SHELLS:
                raise ProposalError(f"agent_policies.{name}: a command may not start with a shell ({prefix[0]})")
            for pattern in entry.get("args") or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ProposalError(f"agent_policies.{name}: bad argument pattern {pattern!r}: {exc}") from exc
                broad = _pattern_too_broad(pattern)
                if broad:
                    raise ProposalError(f"agent_policies.{name}: pattern {pattern!r} admits {broad}; narrow it")
            for glob in entry.get("writes") or []:
                problem = _writes_glob_problem(glob, brief_rel)
                if problem:
                    raise ProposalError(f"agent_policies.{name}: writes glob {glob!r} {problem}")
            t = entry.get("timeout")
            if "timeout" in entry and (isinstance(t, bool) or not isinstance(t, int)
                                       or not 1 <= t <= MAX_COMMAND_TIMEOUT):
                raise ProposalError(f"agent_policies.{name}: timeout must be an integer number of seconds, "
                                    f"1-{MAX_COMMAND_TIMEOUT}")
            if "stdin_gates" in entry and not entry["stdin_gates"]:
                raise ProposalError(f"agent_policies.{name}: stdin_gates is empty; stdin content would run ungated")
            for gate in entry.get("stdin_gates") or []:
                if gate not in gates:
                    raise ProposalError(f"agent_policies.{name}: unknown stdin gate {gate!r}")
        scopes = pol.get("write_scopes") or []
        if not (isinstance(scopes, list) and all(isinstance(s, str) for s in scopes)):
            raise ProposalError(f"agent_policies.{name}.write_scopes must be a list of strings")
        for scope in scopes:
            problem = _scope_pattern_problem(scope)
            if problem:
                raise ProposalError(f"agent_policies.{name}: write scope {scope!r} {problem}")
        if brief_rel and _in_scope(brief_rel, scopes):
            raise ProposalError(f"agent_policies.{name}.write_scopes covers the brief ({brief_rel}); refused (C-3)")
    for reserved in _RESERVED_KEYS:
        if isinstance(brief.get("confined_programs"), dict) and reserved in brief["confined_programs"]:
            raise ProposalError(f"confined_programs.{reserved} is reserved for the operator file; set a gate's exec "
                                "in proposal_gates in the brief")
    if confined_file is not None and brief.get("confined_programs"):
        raise ProposalError("confined_programs is set in both the brief and the operator file "
                            f"({_confinement.CONFINED_DIR}); keep one so it is clear which applies")
    raw_confined = confined_file[0] if confined_file is not None else brief.get("confined_programs") or {}
    if confined_file is not None and isinstance(raw_confined, dict) and (
            GATE_EXEC_KEY in raw_confined or GATE_ARGV_KEY in raw_confined):
        raw_confined = dict(raw_confined)
        gates = _merge_gate_exec(gates, raw_confined.pop(GATE_EXEC_KEY, {}), raw_confined.pop(GATE_ARGV_KEY, {}))
    try:
        confined = _confinement.load_confined(raw_confined, gates, control_plane_of)
    except _confinement.ConfinementError as exc:
        raise ProposalError(str(exc)) from exc
    return Policy(agents, gates, [str(p) for p in protected], brief_rel=brief_rel, confined=confined,
                  allow_unconfined=brief.get("allow_unconfined_runs") is True,
                  confined_file_sha=confined_file[1] if confined_file is not None else None)


def _merge_gate_exec(gates: dict[str, Any], gate_exec: Any, argv_digests: Any) -> dict[str, Any]:
    """Merge the operator file's ``gate_exec`` (P5c) into a copy of the brief's gates as their ``exec``.

    Machine paths for a gate's interpreter then stay out of the committed brief, as P5b did for
    ``confined_programs``. Path shapes are checked by ``load_confined`` like a brief-set ``exec``. Each entry is
    bound to the gate's argv by ``gate_argv_sha256`` (written by ``--install-confined``): a gate whose argv
    changed since install is refused, so a reworked gate never silently keeps the old exec.
    """
    if not (isinstance(gate_exec, dict) and all(isinstance(v, list) and v and all(isinstance(p, str) for p in v)
                                                for v in gate_exec.values())):
        raise ProposalError(f"{GATE_EXEC_KEY} must map gate names to non-empty lists of paths")
    if not (isinstance(argv_digests, dict) and all(isinstance(v, str) for v in argv_digests.values())):
        raise ProposalError(f"{GATE_ARGV_KEY} must map gate names to sha256 strings")
    unknown = sorted(set(gate_exec) - set(gates))
    if unknown:
        raise ProposalError(f"{GATE_EXEC_KEY}.{unknown[0]}: no such gate in the brief's proposal_gates")
    if set(argv_digests) != set(gate_exec):
        raise ProposalError(f"{GATE_ARGV_KEY} must name exactly the gates in {GATE_EXEC_KEY}; reinstall the file "
                            "with --install-confined, which records them")
    merged = dict(gates)
    for name, paths in gate_exec.items():
        if gates[name].get("exec"):
            raise ProposalError(f"{GATE_EXEC_KEY}.{name}: the brief already sets this gate's exec; keep one so it "
                                "is clear which applies")
        if argv_digests[name] != gate_argv_digest(gates[name]["argv"]):
            raise ProposalError(f"{GATE_EXEC_KEY}.{name}: the gate's argv changed since the operator file was "
                                "installed; review its exec and reinstall with --install-confined")
        merged[name] = {**gates[name], "exec": list(paths)}
    return merged


def _scope_pattern_problem(scope: str) -> str | None:
    """Why a pattern scope (P5b) is unsafe, or None. Literal scopes are not checked here."""
    raw = str(scope).replace("\\", "/")
    if not any(c in raw for c in _GLOB_CHARS):
        return None
    if raw.startswith(("/", "~")):
        return "must be project-relative"
    segments = raw[:-1].split("/") if raw.endswith("/") else raw.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return "has an empty, '.' or '..' segment"
    if any("**" in s for s in segments):
        return "uses '**'; '*' matches one segment, so list each depth"
    if any(c in segments[0] for c in _GLOB_CHARS):
        return "must start with a literal project directory"
    if control_plane_of(segments[0], platform="darwin"):
        return "is inside the project control plane"
    return None


def _pattern_scope_matches(folded: str, raw: str) -> bool:
    """Segment-wise fnmatch: ``*`` never crosses ``/``. A trailing ``/`` matches anything below the pattern."""
    pattern = raw.lower().rstrip("/").split("/")
    parts = folded.split("/")
    if raw.endswith("/"):
        if len(parts) <= len(pattern):
            return False
        parts = parts[:len(pattern)]
    elif len(parts) != len(pattern):
        return False
    # As in a shell glob, a wildcard never matches a dot-segment (``.git``, ``.agentteams``): only a pattern
    # segment that itself starts with ``.`` can.
    return all(fnmatch.fnmatchcase(p, q) and (not p.startswith(".") or q.startswith("."))
               for p, q in zip(parts, pattern))


def _in_scope(rel: str, scopes: list[str]) -> bool:
    """True when ``rel`` is a listed file, lies under a listed directory (a scope ending in ``/``), or matches
    a pattern scope (P5b: ``reports/dossiers/*/strategy.md``; ``*``, ``?`` and ``[...]`` match within one
    segment). A scope that is unsafe never matches, whether or not :func:`load_policy` saw it."""
    folded = rel.lower()
    for scope in scopes:
        raw = str(scope).replace("\\", "/")
        if any(c in raw for c in _GLOB_CHARS):
            if _scope_pattern_problem(raw) is None and _pattern_scope_matches(folded, raw):
                return True
            continue
        norm = posixpath.normpath(raw).lower()
        if norm.startswith(("..", "/")) or norm == ".":
            continue  # never widen to the root or outside it
        if (raw.endswith("/") and folded.startswith(norm + "/")) or folded == norm:
            return True
    return False
