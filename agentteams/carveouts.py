"""carveouts.py — operator-signed, workspace-scoped carve-outs from the constitution's Rules (Rule 15 machinery).

A *carve-out* names constitutional-rule clauses that do not refuse one bounded activity, in one workspace or a listed
set of workspaces, until a date. The constraint-relaxing-authorizations rule ships the guardrail machinery to every
generated team and never a specific exception: this module is that machinery. Each carve-out is project data an
operator writes and signs, in one file:

* **Registry** ``.agentteams/carveouts.json`` at the project root, one source for every provider surface. It is a
  trust root (``governance_targets`` stems ``carveouts-json`` / ``carve-outs-json``; a handoff trust-root token), so a decision
  row that targets it is non-eligible and a sandbox handoff that names it is refused.
* **Operator signature.** Every entry derives as constraint-relaxing, so the registry needs the operator's Ed25519
  signature (Rule 15(b)): ``--sign-carveouts`` signs the whole registry with the operator key, after the same
  preflight, integrity check, review digest and confirmation as ``--sign-decision``; the signature is verified against
  ``<decision_log>/references/authorized-verify-keys/<key_id>.pub.pem``. Any later edit breaks it. The registry must
  also be committed, byte for byte, so every surface shows what git history records.
* **Scope** is exact. A workspace id is ``repo:<owner>/<name>`` (the GitHub origin, case-folded) or ``team:<slug>``
  (the brief's ``project_name`` slug). No prefix, suffix or substring widening, as for management directives.
* **Bounds**, checked by machine: ``added`` is not in the future; ``expires`` is required and at most
  :data:`MAX_TTL_DAYS` after ``added``; an active entry past its expiry stops binding (it is no longer rendered) and
  is reported until it is marked expired; active entries are capped (default :data:`DEFAULT_CAP`, never above
  :data:`CAP_CEILING`) and count against the team's Rule 15 exception-registry cap; a declared ``effect_class`` must
  agree with the one ``effect_classifier`` derives; each ``lineage`` id must be in the pinned decision log with no
  unretracted HALT (C-2), ancestors included.
* **Hard limits** no entry can lift: no rendered string names the Constitutional Core (C-1..C-5) or a HALT, and no
  relaxed clause names those, the carve-out mechanism, lineage, the cap, sunset or expiry; it never grants a
  capability (C-3), is never destructive (C-5), and never targets a trust root (``non_eligibility_reason``).
* **Rendering**: :func:`project` writes a hash-stamped ``CARVEOUTS`` block into the unfenced
  ``## Project-Specific Rules`` region of every enabled entry file (Copilot, Claude, Codex/AGENTS.md, Goose), the
  same targets and placement as :mod:`agentteams.rules_projection`; it runs after every generate and ``--update``
  too. That region survives ``--update --merge`` and ``--overwrite``. A block edited by hand is refused, not
  overwritten (the copy hash detects edits, it does not authenticate: the signed registry does that).
* **Clearance binding**: ``clearance_binding.check_file_clearance`` answers "does a verified @security PASS exist for scope X bound
  to this file's sha256, with no HALT on X?" through the non-consuming ``security_gate.check_clearance``. An operator
  script calls ``agentteams --check-clearance X --clearance-file PATH`` instead of hand-rolling the gate.

Stdlib only (verifying a signature needs the ``agentteams[signing]`` extra; without it, rendering fails closed).
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: The registry, relative to the project root.
REGISTRY_REL = ".agentteams/carveouts.json"
#: The longest an entry may run, counted from ``added``.
MAX_TTL_DAYS = 90
#: Active entries allowed when the registry sets no cap.
DEFAULT_CAP = 1
#: The registry's cap can't be set above this.
CAP_CEILING = 3
#: The block kind (shared with ``rules_projection._BLOCK_RE``, which strips it from the operator's own rules).
BLOCK_KIND = "CARVEOUTS"
#: The outcome path a high-water write failure is reported under.
HIGH_WATER_NOTE = "high-water mark"
#: The gate-action family: a HALT on ``carveout:<id>`` stops that carve-out (``action_ids.GATE_ACTIONS`` lists it).
GATE_FAMILY = "carveout"
#: The signature's domain tag: the first signed field, so no decision, grant or directive payload can be replayed.
PURPOSE_TAG = "agentteams-carveouts-v1"

_STATUSES = ("active", "closed", "expired", "revoked")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,63}$")
_WORKSPACE_RE = re.compile(r"^(repo:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|team:[a-z0-9][a-z0-9-]*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
#: A relaxed clause: ``rule-<n>(<letter>)``, then ``: `` and what the clause says. A whole rule is never lifted.
_CLAUSE_RE = re.compile(r"^rule-(?P<num>\d{1,3})\((?P<letter>[a-z])\): \S.{6,}$")
#: The constraint-relaxing-authorizations rule's clauses a carve-out may lift: (b), the operator-signature clause,
#: since the carve-out is itself operator-signed. Not (c): every trust root (c) protects is already refused as a
#: target here, so lifting it would let nothing through (adversarial round 3); not lineage, the cap or sunset.
RELAXABLE_RULE15_CLAUSES = frozenset({"b"})
_RULE15_HEADING_RE = re.compile(r"^[ \t]*(\d{1,3})\.[ \t]+\*\*Constraint-relaxing authorizations", re.IGNORECASE | re.MULTILINE)
#: The only places ``decision_log`` may name: the generated team directories, whose verify-key store, approvers and
#: rosters the emitted sandboxes write-protect (``confine-run.sh`` CONTROL_PLANE_REL). A registry can't choose where
#: its own verify key lives (adversarial/@security review round 2).
TEAM_DIRS: tuple[str, ...] = (".claude/agents", ".github/agents", ".goose/recipes", ".codex/agents", ".grok/agents")
#: Never named by any rendered string (after folding): the Constitutional Core or a HALT.
_CORE_RE = re.compile(r"(\bc ?-? ?[1-5]\b|constitutional ?-?core|\bhalt)")
#: Never named by a relaxed clause, besides the above: this mechanism and Rule 15's own bounds.
_MECHANISM_RE = re.compile(r"(carve ?-?outs?|carveout|lineage|\bcap\b|aggregate|sunset|expir|precedence|constitution)")
#: Trust roots no carve-out may target or name (canonical stems, as ``governance_targets.canon_path`` folds paths):
#: the keys, integrity manifest, decisions log, approvers, rosters, directive ledger, constitution files, exception
#: registry and this registry (@security delta review, 2026-11-09, condition 3).
_ANCHOR_STEMS: tuple[str, ...] = (
    "verify-key", "signing-key", "enforcement-integrity", "security-decision", "security-approver", "agent-privilege",
    "signing-governed", "claude-md", "copilot-instructions", "agents-md", "goosehints", "instruction-authority",
    "waiver", "carveout", "carve-out", "exception-registry", "authorized-manager", "management-directive",
    "management-authority")
_ENTRY_KEYS = frozenset({
    "id", "status", "workspaces", "relaxes", "effect", "effect_class", "conditions", "text", "added", "expires",
    "closes_when", "plan", "ledger", "lineage", "clearance", "author", "note"})
_EFFECT_KEYS = frozenset({"targets", "relaxes", "cross_repo", "bulk", "destructive", "grants"})
_TOP_KEYS = frozenset({"version", "sequence", "cap", "decision_log", "carveouts", "note", "signature"})


class CarveoutError(ValueError):
    """The registry can't be read, isn't signed or committed, or is invalid.

    ``report`` carries the projection that ran before the refusal (the blocks it removed or could not), when any.
    """

    def __init__(self, message: str, report=None) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class Problem:
    """One reason the registry, an entry or a rendered copy is not acceptable."""

    entry: str  # the entry id, "registry", or a rendered file's path
    code: str  # stable
    detail: str

    def __str__(self) -> str:
        return f"{self.entry}: {self.code}: {self.detail}"


@dataclass
class Verification:
    """:func:`verify`'s result."""

    problems: list[Problem] = field(default_factory=list)
    active: list[dict] = field(default_factory=list)
    workspace_ids: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        """True when nothing is wrong."""
        return not self.problems


# ---------------------------------------------------------------------------------------------- reading


def registry_path(root: Path) -> Path:
    """The registry's path in the project at *root*.

    Args:
        root: The project root.

    Returns:
        ``<root>/.agentteams/carveouts.json``.
    """
    return root / REGISTRY_REL


def load_registry(root: Path) -> dict | None:
    """Read the registry, or ``None`` when the project has none.

    Args:
        root: The project root.

    Returns:
        The parsed registry.

    Raises:
        CarveoutError: The registry (or ``.agentteams``) is a symlink, can't be read, isn't JSON, or isn't an object
            with a ``carveouts`` list.
    """
    path = registry_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise CarveoutError(f"{REGISTRY_REL} is not a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CarveoutError(f"{REGISTRY_REL} can't be read: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("carveouts"), list):
        raise CarveoutError(f"{REGISTRY_REL} must be an object with a 'carveouts' list")
    return data


def registry_sha256(root: Path) -> str:
    """The sha256 of the registry's bytes.

    Args:
        root: The project root.

    Returns:
        The hex digest.

    Raises:
        OSError: The registry can't be read.
    """
    return hashlib.sha256(registry_path(root).read_bytes()).hexdigest()


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=text, check=False)


def registry_commit_state(root: Path) -> str | None:
    """Why the registry's bytes differ from the committed ones, or ``None`` when they are identical.

    Compares the file with ``git show HEAD:<path>`` byte for byte, so ``--assume-unchanged`` or
    ``--skip-worktree`` can't hide an edit.

    Args:
        root: The project root.

    Returns:
        ``None``, or a reason.
    """
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return "the project is not a git repository, so the registry can't be shown to be committed"
    committed = _git(root, "show", f"HEAD:{REGISTRY_REL}", text=False)
    if committed.returncode != 0:
        return f"{REGISTRY_REL} is not in the last commit; commit it first"
    try:
        current = registry_path(root).read_bytes()
    except OSError as exc:
        return f"{REGISTRY_REL} can't be read: {exc}"
    if current != committed.stdout:
        return f"{REGISTRY_REL} differs from the last commit; commit it first"
    return None


def workspace_ids(root: Path) -> set[str]:
    """This project's workspace ids: ``repo:<owner>/<name>`` (lowercased) and ``team:<slug>``.

    Args:
        root: The project root.

    Returns:
        The ids that can be derived; empty when neither is known.
    """
    from agentteams._utils import _slugify
    from agentteams.branch_inventory import github_repo_of

    ids: set[str] = set()
    origin = _git(root, "remote", "get-url", "origin")
    if origin.returncode == 0:
        repo = github_repo_of(origin.stdout.strip())
        if repo:
            ids.add(f"repo:{repo.lower()}")
    name = ""
    try:
        from agentteams.memory_index_surfaces import resolve_brief

        brief = resolve_brief(root)
        if brief is not None:
            name = str(json.loads(brief.read_text(encoding="utf-8")).get("project_name", ""))
    except (OSError, ValueError):
        name = ""
    slug = _slugify(name).strip("-") if name else ""
    if slug:
        ids.add(f"team:{slug}")
    return ids


def _norm_workspace(ws: str) -> str:
    return ws.lower() if ws.startswith("repo:") else ws


def in_scope(entry: dict, ids: set[str]) -> bool:
    """Whether *entry* names one of *ids*, by exact match (``repo:`` ids ignoring case, as GitHub does).

    Args:
        entry: A registry entry.
        ids: This project's workspace ids (:func:`workspace_ids`).

    Returns:
        True when one of the entry's workspaces equals one of *ids*.
    """
    wanted = {_norm_workspace(i) for i in ids}
    return any(isinstance(w, str) and _norm_workspace(w.strip()) in wanted for w in entry.get("workspaces") or [])


# ---------------------------------------------------------------------------------------------- validation


def _fold(value: str) -> str:
    """NFKC, every dash-like character to '-', other separators to a space, lowercased: what the limits match on."""
    text = "".join(ch for ch in unicodedata.normalize("NFKC", value) if unicodedata.category(ch) != "Cf")
    text = re.sub(r"[‐-―−﹘﹣－]", "-", text)
    text = re.sub(r"[_\s]+", " ", text)
    return text.lower()


def _date(value: object) -> _dt.date | None:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        return None


def _str_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return [v.strip() for v in value]


def _rel_path_ok(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    p = value.strip()
    return not (p.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:", p) or ".." in Path(p).parts)


def _rendered_strings(entry: dict) -> list[tuple[str, str]]:
    """Every string :func:`render` puts in a constitution copy, as ``(field, value)``."""
    out: list[tuple[str, str]] = []
    for key in ("id", "text", "closes_when", "plan", "ledger", "expires"):
        if isinstance(entry.get(key), str):
            out.append((key, entry[key]))
    for key in ("workspaces", "relaxes", "conditions", "lineage"):
        out += [(f"{key}[{i}]", v) for i, v in enumerate(entry.get(key) or []) if isinstance(v, str)]
    clearance = entry.get("clearance")
    if isinstance(clearance, dict):
        out += [(f"clearance.{k}", v) for k, v in clearance.items() if isinstance(v, str)]
    return out


def _effect_row(entry: dict) -> dict[str, str]:
    effect = entry.get("effect") if isinstance(entry.get("effect"), dict) else {}
    relaxes = [*(entry.get("relaxes") or []), *(effect.get("relaxes") or [])]
    return {
        "action_reviewed": f"{GATE_FAMILY}:{entry.get('id', '')}",
        "scope": f"{GATE_FAMILY}:{entry.get('id', '')}",
        "verdict": "PASS",
        "effect_relaxes": ";".join(str(r) for r in relaxes),
        "effect_targets": ";".join(str(t) for t in effect.get("targets") or []),
        "effect_grants": ";".join(str(g) for g in effect.get("grants") or []),
        "effect_cross_repo": "true" if effect.get("cross_repo") is True else "",
        "effect_bulk": "true" if effect.get("bulk") is True else "",
        "effect_destructive": "true" if effect.get("destructive") is True else "",
    }


def derived_effect_class(entry: dict) -> str:
    """The effect class ``effect_classifier`` derives for *entry*'s structured effect.

    Every valid entry relaxes a clause, so this is ``relaxing`` for every valid entry: it is shown, and checked against
    a declared ``effect_class``, but carries no further information (adversarial review, 2026-11-09).

    Args:
        entry: A registry entry.

    Returns:
        ``relaxing``, ``non-relaxing`` or ``indeterminate``.
    """
    from agentteams.cli.effect_classifier import derive_effect_class

    return derive_effect_class(_effect_row(entry), kind="decision")


def validate_entry(entry: object, *, today: _dt.date) -> list[Problem]:
    """Check one entry's shape, bounds and hard limits.

    Args:
        entry: The raw entry.
        today: The date expiry and ``added`` are checked against.

    Returns:
        Its problems (empty when it is acceptable). ``lapsed`` means it no longer binds.
    """
    from agentteams.cli.governance_targets import canon_path

    if not isinstance(entry, dict):
        return [Problem("registry", "bad-entry", "every carve-out must be an object")]
    eid = entry.get("id") if isinstance(entry.get("id"), str) else "?"
    out: list[Problem] = []

    def bad(code: str, detail: str) -> None:
        out.append(Problem(eid, code, detail))

    if not _ID_RE.match(eid):
        bad("bad-id", "id must be 3-64 characters of a-z, 0-9, '.' and '-', starting with a letter or digit")
    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        bad("unknown-field", f"unknown field(s) {', '.join(unknown)} (a typo would silently drop a limit)")
    status = entry.get("status")
    if status not in _STATUSES:
        bad("bad-status", f"status must be one of {', '.join(_STATUSES)}")

    workspaces = _str_list(entry.get("workspaces"))
    if not workspaces:
        bad("no-workspaces", "workspaces must be a non-empty list of 'repo:<owner>/<name>' or 'team:<slug>' ids")
    else:
        for ws in workspaces:
            if not _WORKSPACE_RE.match(ws):
                bad("bad-workspace", f"'{ws}' is not 'repo:<owner>/<name>' or 'team:<slug>' (exact ids only)")
        if len({_norm_workspace(w) for w in workspaces}) != len(workspaces):
            bad("duplicate-workspace", "a workspace is listed twice")

    relaxes = _str_list(entry.get("relaxes"))
    if not relaxes:
        bad("no-relaxes", "relaxes must name the clauses lifted, e.g. 'rule-15(b): operator signature required'")
    for clause in relaxes or []:
        if not _CLAUSE_RE.match(clause):
            bad("bad-clause", f"'{clause}' is not 'rule-<n>(<letter>): <what it says>' (a whole rule is never lifted)")
        if _MECHANISM_RE.search(_fold(clause)):
            bad("unrelaxable", f"'{clause}' names the carve-out mechanism or Rule 15's own bounds (lineage, cap, "
                               "sunset, expiry, precedence, the constitution); none of these can be carved out")
    effect = entry.get("effect", {})
    if not isinstance(effect, dict) or set(effect) - _EFFECT_KEYS:
        bad("bad-effect", f"effect must be an object with only {', '.join(sorted(_EFFECT_KEYS))}")
        effect = {}
    for key in ("targets", "relaxes", "grants"):
        if key in effect and _str_list(effect[key]) is None and effect[key] != []:
            bad("bad-effect", f"effect.{key} must be a list of strings")
    for key in ("cross_repo", "bulk", "destructive"):
        if key in effect and not isinstance(effect[key], bool):
            bad("bad-effect", f"effect.{key} must be true or false")
    for clause in _str_list(effect.get("relaxes")) or []:
        if _CORE_RE.search(_fold(clause)) or _MECHANISM_RE.search(_fold(clause)):
            bad("unrelaxable", f"effect.relaxes '{clause}' names the Core, a HALT or the carve-out mechanism")
    if _str_list(effect.get("grants")):
        bad("grants-capability", "a carve-out never grants a capability (C-3); widen a tools grant through @security")
    if effect.get("destructive") is True:
        bad("destructive", "a carve-out never clears a destructive action (C-5); that needs a recorded clearance")
    named = [(f"effect.targets[{i}]", v) for i, v in enumerate(_str_list(effect.get("targets")) or [])]
    for name, value in named + [(n, v) for n, v in _rendered_strings(entry) if n not in ("id",)]:
        anchor = next((s for s in _ANCHOR_STEMS if s in canon_path(_fold(value))), None)
        if anchor:
            bad("anchor-target", f"{name} names a trust root ({anchor}); a carve-out never targets or names the "
                                 "keys, integrity manifest, decisions log, approvers, rosters, directive ledger, "
                                 "constitution files or registries")

    conditions = _str_list(entry.get("conditions"))
    if not conditions:
        bad("no-conditions", "conditions must list what the carved-out activity must still satisfy")
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        bad("no-text", "text must hold the carve-out's wording as every constitution copy will show it")
    elif re.search(r"^[ \t]*#", text, re.MULTILINE):
        bad("markup", "text may not contain Markdown headings: it must not start a section of its own")
    for name, value in _rendered_strings(entry):
        if "<!--" in value or "-->" in value:
            bad("markup", f"{name} contains an HTML comment marker, which could break the rendered block")
        if name != "text" and ("\n" in value or "\r" in value):
            bad("markup", f"{name} must be a single line")
    effect_strings = [(f"effect.{k}[{i}]", v) for k in ("targets", "relaxes")
                      for i, v in enumerate(effect.get(k) or []) if isinstance(v, str)]
    for name, value in _rendered_strings(entry) + effect_strings:
        if name != "text" and not value.isascii():
            bad("non-ascii", f"{name} must be ASCII (lookalike and invisible characters could hide a limit)")
        if any(unicodedata.category(ch) == "Cf" for ch in value):
            bad("non-ascii", f"{name} holds an invisible format character (e.g. a zero-width space or soft hyphen)")
        if _CORE_RE.search(_fold(value)):
            bad("unrelaxable", f"{name} names the Constitutional Core (C-1..C-5) or a HALT; a carve-out never "
                               "touches either, and its wording may not suggest it does")

    added, expires = _date(entry.get("added")), _date(entry.get("expires"))
    if added is None:
        bad("bad-added", "added must be a YYYY-MM-DD date")
    elif added > today:
        bad("future-added", f"added {added.isoformat()} is in the future; the limit is counted from it")
    if expires is None:
        bad("no-expiry", "expires must be a YYYY-MM-DD date: every carve-out lapses")
    if added and expires:
        if expires < added:
            bad("bad-expiry", "expires is before added")
        elif (expires - added).days > MAX_TTL_DAYS:
            bad("expiry-too-far", f"expires is {(expires - added).days} days after added; the limit is {MAX_TTL_DAYS}")
    if status == "active" and expires and expires < today:
        bad("lapsed", f"expired {expires.isoformat()}: it no longer binds; set its status to 'expired'")
    if "closes_when" in entry and (not isinstance(entry["closes_when"], str) or not entry["closes_when"].strip()):
        bad("bad-closes-when", "closes_when, when present, must be a non-empty string")

    for key in ("plan", "ledger"):
        if key in entry and not _rel_path_ok(entry[key]):
            bad(f"bad-{key}", f"{key} must be a path relative to the project root, without '..'")
    lineage = entry.get("lineage", [])
    if lineage != [] and _str_list(lineage) is None:
        bad("bad-lineage", "lineage must be a list of decision-log action ids")
    clearance = entry.get("clearance")
    if clearance is not None:
        if (not isinstance(clearance, dict) or set(clearance) != {"scope", "file"}
                or not isinstance(clearance.get("scope"), str) or not _SCOPE_RE.match(clearance["scope"])
                or not _rel_path_ok(clearance.get("file"))):
            bad("bad-clearance", "clearance must be {'scope': <action id>, 'file': <relative path>}")

    if not out:
        derived, declared = derived_effect_class(entry), entry.get("effect_class")
        if declared is not None and declared != derived:
            bad("effect-class-mismatch", f"declared effect_class '{declared}' but it derives as '{derived}'")
    return out


def _cap(registry: dict) -> int | None:
    cap = registry.get("cap", {"max_active": DEFAULT_CAP})
    value = cap.get("max_active", DEFAULT_CAP) if isinstance(cap, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= CAP_CEILING:
        return None
    return value


def validate_registry(registry: dict, *, today: _dt.date) -> list[Problem]:
    """Check the whole registry: version, cap, decision log, signature shape, unique ids and every entry.

    Args:
        registry: The parsed registry (:func:`load_registry`).
        today: The date expiry is checked against.

    Returns:
        Its problems.
    """
    out: list[Problem] = []
    unknown = sorted(set(registry) - _TOP_KEYS)
    if unknown:
        out.append(Problem("registry", "unknown-field", f"unknown top-level field(s) {', '.join(unknown)}"))
    if registry.get("version") != 1:
        out.append(Problem("registry", "bad-version", "version must be 1"))
    cap = _cap(registry)
    if cap is None:
        out.append(Problem("registry", "bad-cap", f"cap.max_active must be an integer from 0 to {CAP_CEILING}"))
    if registry.get("decision_log") not in TEAM_DIRS:
        out.append(Problem("registry", "no-decision-log", "decision_log must be one of the generated team directories "
                                                          f"({', '.join(TEAM_DIRS)}), whose key store the sandbox "
                                                          "protects"))
    seq = registry.get("sequence")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        out.append(Problem("registry", "bad-sequence", "sequence must be a positive integer, raised on every re-sign"))
    seen: set[str] = set()
    for entry in registry["carveouts"]:
        out += validate_entry(entry, today=today)
        eid = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(eid, str):
            if eid in seen:
                out.append(Problem(eid, "duplicate-id", "two entries share this id"))
            seen.add(eid)
    active = [e for e in registry["carveouts"] if isinstance(e, dict) and e.get("status") == "active"]
    if cap is not None and len(active) > cap:
        out.append(Problem("registry", "over-cap", f"{len(active)} active carve-outs; the cap is {cap}"))
    return out


def active_entries(registry: dict, ids: set[str], *, today: _dt.date) -> list[dict]:
    """The entries that bind this workspace now: active, in scope, not past expiry, and valid.

    Args:
        registry: The parsed registry.
        ids: This project's workspace ids.
        today: The current date.

    Returns:
        The entries, in registry order.
    """
    return [e for e in registry["carveouts"]
            if isinstance(e, dict) and e.get("status") == "active" and in_scope(e, ids)
            and not validate_entry(e, today=today)]


# ---------------------------------------------------------------------------------------------- signature


def decision_dir(root: Path, registry: dict) -> Path:
    """The pinned team directory (``decision_log``, one of :data:`TEAM_DIRS`) for the decisions log, the verify keys
    and the exception registry.

    Args:
        root: The project root.
        registry: The parsed registry.

    Returns:
        ``<root>/<decision_log>`` (not resolved: :func:`team_dir_problem` refuses a symlinked path).
    """
    rel = registry.get("decision_log")
    return root.resolve() / (rel if rel in TEAM_DIRS else TEAM_DIRS[0])


def signed_fields(registry: dict, key_id: str) -> list[str]:
    """The fields the operator signs: the domain tag, the key id and the canonical registry minus its signature.

    Args:
        registry: The registry.
        key_id: The verify key's file stem.

    Returns:
        The ordered fields (``signed_ledger.canonical_payload`` joins them).
    """
    body = {k: v for k, v in registry.items() if k != "signature"}
    return [PURPOSE_TAG, key_id, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def signer(root: Path, registry: dict) -> str:
    """Who signed the registry, for the operator to recognise: the team dir, key id and the key's sha256 fingerprint.

    A sandboxed agent can create a team directory that did not exist when its sandbox started, plant its own verify
    key there and sign with it (adversarial round 4); agentteams can't tell that directory's age. Showing the signing
    key's fingerprint on every verify and render lets the operator see that the key is theirs.

    Args:
        root: The project root.
        registry: The parsed registry.

    Returns:
        A one-line description, or why the key can't be read.
    """
    sig = registry.get("signature") if isinstance(registry.get("signature"), dict) else {}
    key_id = str(sig.get("key_id", ""))
    team = registry.get("decision_log")
    path = decision_dir(root, registry) / "references" / "authorized-verify-keys" / f"{key_id}.pub.pem"
    try:
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()[:16] if _KEY_ID_RE.match(key_id) else "?"
    except OSError:
        fingerprint = "unreadable"
    shown_key = key_id if _KEY_ID_RE.match(key_id) else "(invalid key id)"
    shown_team = team if team in TEAM_DIRS else "(invalid decision_log)"  # never echo unvalidated registry text
    return f"signed with key {shown_key} in {shown_team} (public key sha256 {fingerprint}); make sure it is yours"


def signature_problem(root: Path, registry: dict) -> Problem | None:
    """Why the registry's operator signature does not verify, or ``None`` when it does.

    Args:
        root: The project root.
        registry: The parsed registry.

    Returns:
        A problem (``unsigned`` or ``bad-signature``), or ``None``.
    """
    from agentteams.cli.decision_log import _load_verify_key
    from agentteams.cli.signed_ledger import ed25519_verify

    sig = registry.get("signature")
    if not isinstance(sig, dict) or sig.get("scheme") != "ed25519" or not isinstance(sig.get("value"), str):
        return Problem("registry", "unsigned", "the registry carries no operator Ed25519 signature; the operator runs "
                                               "agentteams --sign-carveouts --key-id <id>")
    key_id = sig.get("key_id")
    if not isinstance(key_id, str) or not _KEY_ID_RE.match(key_id):
        return Problem("registry", "bad-signature", "signature.key_id is missing or malformed")
    try:
        public = _load_verify_key(decision_dir(root, registry), key_id)
        ok = ed25519_verify(public, signed_fields(registry, key_id), sig["value"])
    except RuntimeError as exc:
        return Problem("registry", "bad-signature", f"the signature can't be verified: {exc}")
    if not ok:
        return Problem("registry", "bad-signature", "the signature does not verify: the registry changed after the "
                                                    "operator signed it, or another key signed it")
    return None


# ---------------------------------------------------------------------------------------------- lineage, cap, clearance


def binding_entries(root: Path, *, today: _dt.date) -> tuple[dict | None, list[dict], list[Problem]]:
    """Load the registry and every check that decides what binds: ``(registry, entries, refusals)``.

    A lapsed entry is not a refusal (it simply stops binding); every other problem is, and then nothing binds.

    Args:
        root: The project root.
        today: The current date.

    Returns:
        The registry (``None`` when absent), the binding entries, and the problems that refuse rendering.
    """
    try:
        registry = load_registry(root)
    except CarveoutError as exc:
        return None, [], [Problem("registry", "unreadable", str(exc))]
    if registry is None:
        return None, [], []
    refusals: list[Problem] = []
    state = registry_commit_state(root)
    if state:
        refusals.append(Problem("registry", "not-committed", state))
    from agentteams import carveout_checks as ck

    refusals += [p for p in validate_registry(registry, today=today) if p.code != "lapsed"]
    if not refusals:
        refusals += [p for p in (ck.team_dir_problem(root, registry),) if p]
    if not refusals:
        refusals += [p for p in (signature_problem(root, registry), ck.rollback_problem(root, registry)) if p]
        refusals += ck.clause_problems(root, registry)
    if refusals:
        return registry, [], refusals
    every_active = [e for e in registry["carveouts"] if isinstance(e, dict) and e.get("status") == "active"
                    and not validate_entry(e, today=today)]
    entries = [e for e in every_active if in_scope(e, workspace_ids(root))]
    refusals += ck.check_own_halts(root, registry, every_active)
    refusals += ck.check_lineage(root, registry, entries)
    refusals += ck.check_clearances(root, registry, entries)
    refusals += ck.check_aggregate_cap(root, registry, every_active)
    return registry, ([] if refusals else entries), refusals


# ---------------------------------------------------------------------------------------------- rendering


def render(entries: list[dict]) -> str:
    """The block's content for *entries* (the carve-outs that bind this workspace).

    Args:
        entries: Valid, active, in-scope entries.

    Returns:
        Markdown, ending in a newline.
    """
    lines = [f"> **Carve-outs (operator-signed, `{REGISTRY_REL}`).** These are constraint-relaxing authorizations "
             "under the constitution's own constraint-relaxing-authorizations rule: each lifts only the clauses it "
             "lists, only for the workspaces it names, only under its conditions, and only until it expires. They "
             "bind as written. None touches the Constitutional Core (C-1..C-5) or a HALT, and every other rule still "
             "applies. Rendered by agentteams from the signed, committed registry; never edit this block (edit the "
             "registry, re-sign, commit, then run `agentteams --sync-carveouts`).", ""]
    for e in entries:
        lines += [f"#### Carve-out `{e['id']}` (expires {e['expires']})", "", e["text"].strip(), ""]
        lines.append("- Workspaces: " + ", ".join(f"`{w}`" for w in e["workspaces"]))
        lines.append("- Lifts: " + "; ".join(e["relaxes"]) + f" (effect class: {derived_effect_class(e)}, derived)")
        lines.append("- Conditions:")
        lines += [f"  - {c}" for c in e["conditions"]]
        if e.get("closes_when"):
            lines.append(f"- Closes early when: {e['closes_when']}")
        for key, label in (("plan", "Plan"), ("ledger", "Ledger")):
            if e.get(key):
                lines.append(f"- {label}: `{e[key]}`")
        if e.get("lineage"):
            lines.append("- Lineage: " + ", ".join(f"`{x}`" for x in e["lineage"]))
        if e.get("clearance"):
            lines.append(f"- Clearance: `{e['clearance']['scope']}` bound to the sha256 of `{e['clearance']['file']}`")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _remove_block(text: str) -> tuple[str, str, str]:
    from agentteams.rules_projection import _BLOCK_RE, _sha
    from agentteams.user_regions import reindent

    found = list(_BLOCK_RE[BLOCK_KIND].finditer(text))
    if not found:
        return text, "current", ""
    if len(found) > 1:
        return text, "refused", f"it has {len(found)} {BLOCK_KIND} blocks; keep one"
    m = found[0]
    if _sha(reindent(m.group("body"), m.group("indent"), "") or "") != m.group("csha"):
        return text, "refused", "the carve-out block was edited by hand; restore it, then sync"
    return text[:m.start()] + text[m.end():].lstrip("\n"), "removed", "no carve-out binds this workspace now"


def project(root: Path, *, frozen: object = None, write: bool = True, today: _dt.date | None = None):
    """Render the carve-outs that bind this workspace into every enabled entry file.

    Args:
        root: The project root.
        frozen: The brief's ``frozen_surfaces``.
        write: Write the changes; ``False`` reports what would change.
        today: The date expiry is checked against (default: today).

    Returns:
        A ``rules_projection.Report`` whose ``source`` is the registry path; with no registry, its outcomes only
        remove blocks left behind. An outcome's status is ``current``, ``updated``, ``added``, ``removed``,
        ``refused`` or ``skipped``.

    Raises:
        CarveoutError: The registry is refused (unreadable, uncommitted, invalid, unsigned, a lineage HALT, an
            uncleared clearance, over the aggregate cap); nothing is rendered then, and the blocks it rendered
            earlier are removed first.
        ValueError: *frozen* is not a list of known surface ids.
    """
    from agentteams.rules_projection import (
        _RULES_HEADING, CODEX_BUDGET_BYTES, Outcome, Report, _block, _place, frozen_targets, targets)

    today = today or _dt.date.today()
    registry, entries, refusals = binding_entries(root, today=today)
    # A refused registry binds nothing: its earlier blocks are removed (fail closed), then the refusal is raised.
    block = "" if refusals or not entries else _block(BLOCK_KIND, REGISTRY_REL, registry_sha256(root),
                                                      render(entries), None)
    report = Report(source=REGISTRY_REL)
    report.outcomes += [Outcome(rel, "skipped", "a frozen surface owns it") for rel in frozen_targets(root, "", frozen)]
    real_root = root.resolve()
    for rel, provider in targets(root, "", frozen):
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(real_root):
            report.outcomes.append(Outcome(rel, "skipped", "a symlink, or a path that leaves the project"))
            continue
        if not path.exists():
            continue
        raw = path.read_bytes()
        if b"\r\n" in raw:
            report.outcomes.append(Outcome(rel, "refused", "CRLF line endings; convert it to LF"))
            continue
        text = raw.decode("utf-8")
        if block:
            new, status, detail = _place(text, BLOCK_KIND, block, heading=_RULES_HEADING, add_region=True)
        else:
            new, status, detail = _remove_block(text)
        if provider == "codex" and status in ("updated", "added") and len(new.encode("utf-8")) > CODEX_BUDGET_BYTES:
            status, detail = "refused", (f"the block would take {rel} past Codex's {CODEX_BUDGET_BYTES // 1024} KiB "
                                         "budget, so Codex would not read it; shorten the file or the carve-outs")
        if write and status in ("updated", "added", "removed"):
            path.write_text(new, encoding="utf-8")
        report.outcomes.append(Outcome(rel, status, detail))
    if refusals:
        removed = [o.path for o in report.outcomes if o.status == "removed"]
        note = f"\n  (blocks {'removed from' if write else 'to remove from'}: {', '.join(removed)})" if removed else ""
        raise CarveoutError("the registry is refused:\n  " + "\n  ".join(str(p) for p in refusals) + note,
                            report=report)
    if write and registry is not None:
        from agentteams.carveout_checks import record_high_water

        failure = record_high_water(root, registry)
        if failure:
            report.outcomes.append(Outcome(HIGH_WATER_NOTE, "refused", failure))
    return report


def verify(root: Path, *, frozen: object = None, today: _dt.date | None = None) -> Verification:
    """Report everything wrong with the registry and its rendered copies. Writes nothing.

    Args:
        root: The project root.
        frozen: The brief's ``frozen_surfaces``.
        today: The date expiry is checked against (default: today).

    Returns:
        The verification: problems (lapsed entries included), the binding entries, and this workspace's ids.
    """
    today = today or _dt.date.today()
    result = Verification(workspace_ids=workspace_ids(root))
    registry, entries, refusals = binding_entries(root, today=today)
    result.problems += refusals
    result.active = entries
    if registry is not None and not refusals:
        result.problems += [p for p in validate_registry(registry, today=today) if p.code == "lapsed"]
    if refusals:  # a refused registry binds nothing: report every block still in place (hand-edited, or not removed)
        try:
            project(root, frozen=frozen, write=False, today=today)
        except CarveoutError as exc:
            for o in (exc.report.outcomes if exc.report else []):
                if o.status in ("removed", "refused"):
                    result.problems.append(Problem(o.path, "stale-block", "a carve-out block is still in place "
                                                   "though the registry is refused" + (f": {o.detail}" if o.detail else "")))
        except ValueError as exc:
            result.problems.append(Problem("registry", "unrenderable", str(exc)))
        return result
    try:
        report = project(root, frozen=frozen, write=False, today=today)
    except (CarveoutError, ValueError) as exc:
        result.problems.append(Problem("registry", "unrenderable", str(exc)))
        return result
    for o in report.outcomes:
        if o.status in ("updated", "added", "removed", "refused"):
            why = {"updated": "its block is out of date", "added": "it has no block",
                   "removed": "its block names carve-outs that no longer bind"}.get(o.status, o.detail)
            result.problems.append(Problem(o.path, "drift", f"{why}; run agentteams --sync-carveouts"))
    return result
