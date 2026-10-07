"""proposal_policy.py — the registered policy for the orchestrator-only-writes pilot.

Carved out of :mod:`agentteams.proposals` when P4b pushed that module to the CH-07 ceiling. This half only
reads and validates the brief: ``agent_policies``, ``proposal_gates``, ``protected_paths``,
``confined_programs`` and ``allow_unconfined_runs``. It writes nothing and runs nothing. ``proposals``
re-exports every name, so no import changed. Stdlib only; integrity-pinned.
"""

from __future__ import annotations

import fnmatch
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


def load_policy(brief: dict[str, Any], *, brief_rel: str | None = None) -> Policy:
    """Build a :class:`Policy` from a project description, validating its shape and linting patterns.

    Args:
        brief: The parsed brief (``agent_policies``, ``proposal_gates``, ``protected_paths``).
        brief_rel: The brief's project-relative path, which no proposal may change (``None`` when outside).

    Returns:
        The policy.

    Raises:
        ProposalError: A field has the wrong shape, a pattern does not compile or admits options or
            parent/absolute paths, a gate templates ``{file}``/``{path}`` inside a larger argument or runs a
            shell, or a write scope covers the brief.
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
        if brief_rel and _in_scope(brief_rel, pol.get("write_scopes") or []):
            raise ProposalError(f"agent_policies.{name}.write_scopes covers the brief ({brief_rel}); refused (C-3)")
    try:
        confined = _confinement.load_confined(brief.get("confined_programs") or {}, gates, control_plane_of)
    except _confinement.ConfinementError as exc:
        raise ProposalError(str(exc)) from exc
    return Policy(agents, gates, [str(p) for p in protected], brief_rel=brief_rel, confined=confined,
                  allow_unconfined=brief.get("allow_unconfined_runs") is True)


def _in_scope(rel: str, scopes: list[str]) -> bool:
    """True when ``rel`` is a listed file or lies under a listed directory (a scope ending in ``/``)."""
    folded = rel.lower()
    for scope in scopes:
        raw = str(scope).replace("\\", "/")
        norm = posixpath.normpath(raw).lower()
        if norm.startswith(("..", "/")) or norm == ".":
            continue  # never widen to the root or outside it
        if (raw.endswith("/") and folded.startswith(norm + "/")) or folded == norm:
            return True
    return False
