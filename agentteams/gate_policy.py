"""gate_policy.py — render-side handling of the project gate verdict policy (#456).

The constitutional gate (``templates/universal/hooks/constitutional-gate.py``) owns the policy format: its
``parse_gate_policy`` validates the file, its ``GATE_POLICY_PRESETS`` define the presets and its ``POLICY_CLASSES``
are the class ids a policy may name. This module loads that template as a module (it is never run, so its
``sys.path`` scrub does not fire) and uses it to:

* read and validate the project's ``.agentteams/gate-policy.json`` at render (:func:`project_policy`; an invalid
  file refuses the render, fail closed);
* write the file's SHA-256 into the emitted gate (:func:`apply_gate_policy`, the ``_GATE_POLICY_SHA256``
  sentinel, the same emit-time mechanism as ``_FAIL_CLOSED_ON_ERROR``);
* tell a weaker policy from an equal or stricter one (:func:`weakens`), so the relaxation gate can require the
  operator's signed decision for a weakening (Rule 15; ``gate-policy-weaken``);
* report the active policy for ``--check-wiring`` (:func:`wiring_lines`).

Integrity-pinned: a silent edit here could record a digest for the wrong bytes or call a weakening a tightening.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

#: The policy file, relative to the project root (== the gate's ``GATE_POLICY_REL``; test-locked).
GATE_POLICY_REL = ".agentteams/gate-policy.json"
#: The emitted gate's digest sentinel line, as the template ships it.
_SENTINEL = '_GATE_POLICY_SHA256 = ""'
_SENTINEL_RE = re.compile(r'^_GATE_POLICY_SHA256 = "([0-9a-f]{64})?"\s*$', re.M)
_TEMPLATE = Path(__file__).resolve().parent / "templates" / "universal" / "hooks" / "constitutional-gate.py"


class GatePolicyRenderError(ValueError):
    """The project's gate policy file cannot be recorded (invalid, unreadable or not a regular file)."""


@dataclass(frozen=True)
class RecordedPolicy:
    """A validated policy as the render records it.

    Attributes:
        sha256: SHA-256 of the file's bytes, written into the emitted gate.
        deny: Every class the policy denies (preset classes included).
        fail_closed_tools: Whether a gate failure denies every non-read-only tool.
        preset: The named preset, or None.
    """

    sha256: str
    deny: frozenset[str]
    fail_closed_tools: bool
    preset: str | None = None


@lru_cache(maxsize=1)
def gate_module() -> ModuleType:
    """Return the gate template loaded as a module (not run as ``__main__``).

    Returns:
        The module.

    Raises:
        ImportError: The template cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location("_agentteams_constitutional_gate", _TEMPLATE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the constitutional gate template at {_TEMPLATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> bytes:
    """Read a regular file without following a final symlink."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(gate_module()._GATE_POLICY_MAX_BYTES + 1)
    finally:
        os.close(fd)


def policy_from_bytes(raw: bytes) -> RecordedPolicy:
    """Validate policy bytes with the gate's own parser.

    Args:
        raw: The file's bytes.

    Returns:
        The recorded policy.

    Raises:
        GatePolicyRenderError: The gate's parser refuses the bytes.
    """
    gate = gate_module()
    try:
        parsed = gate.parse_gate_policy(raw)
    except gate.GatePolicyError as exc:
        raise GatePolicyRenderError(f"{GATE_POLICY_REL} is invalid: {exc}") from exc
    return RecordedPolicy(hashlib.sha256(raw).hexdigest(), frozenset(parsed.deny), bool(parsed.fail_closed_tools),
                          parsed.preset)


def project_policy(project_root: Path | None) -> RecordedPolicy | None:
    """Return the project's validated policy, or None when it has no policy file.

    Args:
        project_root: The project root, or None (no project root known: no policy).

    Returns:
        The policy, or None.

    Raises:
        GatePolicyRenderError: The file exists but is a symlink, not a regular file, unreadable or invalid.
    """
    if project_root is None:
        return None
    path = Path(project_root) / GATE_POLICY_REL
    if not os.path.lexists(path):
        return None
    try:
        raw = _read(path)
    except OSError as exc:
        raise GatePolicyRenderError(f"{GATE_POLICY_REL} cannot be read as a regular file ({exc})") from exc
    return policy_from_bytes(raw)


def manifest_policy(manifest: dict[str, Any]) -> RecordedPolicy | None:
    """:func:`project_policy` for the manifest's CLI project root (``PROJECT_ROOT_KEY``).

    Args:
        manifest: The team manifest.

    Returns:
        The policy, or None.

    Raises:
        GatePolicyRenderError: As :func:`project_policy`.
    """
    from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY

    root = manifest.get(PROJECT_ROOT_KEY)
    return project_policy(root if isinstance(root, Path) else None)


def apply_gate_policy(hook_text: str, policy: RecordedPolicy | None) -> str:
    """Write the policy's digest into the emitted gate; unchanged when there is no policy.

    Args:
        hook_text: The gate text (after the fail-closed flip).
        policy: The recorded policy, or None.

    Returns:
        The gate text.

    Raises:
        GatePolicyRenderError: A policy is given but the gate carries no sentinel (it would be silently unpinned).
    """
    if policy is None:
        return hook_text
    if hook_text.count(_SENTINEL) != 1:
        raise GatePolicyRenderError("the constitutional gate has no single _GATE_POLICY_SHA256 sentinel to record into")
    return hook_text.replace(_SENTINEL, f'_GATE_POLICY_SHA256 = "{policy.sha256}"', 1)


def recorded_digest(hook_text: str) -> str | None:
    """Return the digest an emitted gate records, ``""`` for none, or None when the sentinel line is absent/malformed.

    Args:
        hook_text: An installed gate's text.

    Returns:
        The digest, ``""``, or None.
    """
    found = _SENTINEL_RE.findall(hook_text)
    return found[0] if len(found) == 1 else None


def weakens(old: RecordedPolicy | None, new: RecordedPolicy | None) -> bool:
    """Whether ``new`` is weaker than ``old``: a class no longer denied, ``fail_closed_tools`` dropped, or removed.

    Args:
        old: The previously recorded policy, or None.
        new: This render's policy, or None.

    Returns:
        True for a weakening (a constraint relaxation, Rule 15).
    """
    if old is None:
        return False
    if new is None:
        return True
    return bool(old.deny - new.deny) or (old.fail_closed_tools and not new.fail_closed_tools)


def wiring_lines(project_root: Path, gate_path: Path, *, advisory: bool) -> tuple[bool, list[str]]:
    """Return the ``--check-wiring`` lines for the active gate policy.

    Args:
        project_root: The project root.
        gate_path: The installed gate.
        advisory: True where the pin is advisory (no OS write protection: Copilot, or the OS sandbox off).

    Returns:
        ``(ok, lines)``; not ok when a recorded policy is missing, altered or invalid (the gate denies every
        non-read-only tool then).
    """
    try:
        digest = recorded_digest(gate_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return True, [f"gate policy: no readable gate at {gate_path} ({type(exc).__name__}) — nothing to report."]
    path = project_root / GATE_POLICY_REL
    if digest is None:
        return False, [f"ERROR: gate policy: {gate_path} has no well-formed _GATE_POLICY_SHA256 line (altered?)"]
    if not digest:
        note = f" ({GATE_POLICY_REL} exists but is NOT recorded; re-render to record it)" if os.path.lexists(path) else ""
        return True, [f"gate policy: none recorded — default verdicts{note}."]
    try:
        raw = _read(path)
    except OSError as exc:
        return False, [f"ERROR: gate policy: recorded sha256 {digest[:12]} but {GATE_POLICY_REL} is unreadable or "
                       f"missing ({exc}); the gate denies every non-read-only tool."]
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        return False, [f"ERROR: gate policy: {GATE_POLICY_REL} sha256 {actual[:12]} does not match the recorded "
                       f"{digest[:12]}; the gate denies every non-read-only tool."]
    try:
        policy = policy_from_bytes(raw)
    except GatePolicyRenderError as exc:
        return False, [f"ERROR: gate policy: {exc}"]
    lines = [f"gate policy: sha256 {digest[:12]}, preset {policy.preset or 'none'}, "
             f"deny {sorted(policy.deny) or '[]'}, fail_closed_tools {'on' if policy.fail_closed_tools else 'off'}."]
    if advisory:
        lines.append("gate policy: ADVISORY here — no OS write protection covers the gate and the policy file "
                     "(Copilot, or the OS sandbox is off), so an agent that can write them can unpin the policy.")
    return True, lines


__all__ = [
    "GATE_POLICY_REL",
    "GatePolicyRenderError",
    "RecordedPolicy",
    "apply_gate_policy",
    "gate_module",
    "manifest_policy",
    "policy_from_bytes",
    "project_policy",
    "recorded_digest",
    "weakens",
    "wiring_lines",
]
