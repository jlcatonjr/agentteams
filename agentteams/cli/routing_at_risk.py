"""Routed agents an ``--overwrite`` would orphan, and the guard that refuses to (issue #392 item 12).

``--overwrite`` replaces the on-disk orchestrator with the freshly rendered one. Any agent the old orchestrator
routes work to that this build does not keep loses its routing: a bespoke workflow step, routing row or handoff
naming ``@my-agent`` is gone after the run.

Report half (#526): every such slug is printed on stderr before any file is written, and goes into the
``--dry-run --json`` report. Refusal half (this module's :func:`refuse_routing_loss`): a real ``--overwrite`` run,
whether ``--update --overwrite``, plain ``--overwrite`` or the ``--overwrite`` that ``--migrate`` re-enters with,
refuses before the overwrite clearance is spent and before any backup or write, unless the at-risk set is empty
after adoption or ``--accept-routing-loss`` names exactly that set. ``--yes`` and the ``--migrate`` gate exemption
never acknowledge. An orchestrator that exists but cannot be read refuses (fail closed). ``@security`` design
verdict 2026-11-19, conditions 1-10.

A slug counts as routed when the orchestrator names it as a backticked ``@slug`` in its body (the form agentteams
renders and bespoke edits copy), as a front-matter ``agents:`` item or ``handoffs:`` target, or as a bare ``@slug``
in its body that resolves to an agent file on disk.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_SLUG = r"[a-z0-9][a-z0-9-]{0,63}"
#: A backticked agent mention: `@slug`, as agentteams renders routing rows and workflow steps.
_MENTION = re.compile(rf"`@({_SLUG})`")
#: A bare ``@slug`` in prose; it counts only when an agent file with that slug exists (condition 5).
_BARE = re.compile(rf"(?<![\w`/@])@({_SLUG})(?![\w/-])")
#: Front-matter routing targets: an ``agents:`` list item, or a ``handoffs:`` entry's ``agent:`` value.
_FM_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
_FM_ITEM = re.compile(rf"^\s+-\s+['\"]?({_SLUG})['\"]?\s*$")
_FM_AGENT = re.compile(rf"^\s+(?:-\s+)?agent:\s*['\"]?({_SLUG})['\"]?\s*$")
#: Chat participants and tools that templates mention in backticks but that are never team agents, unless an
#: agent file with that slug exists (condition 5: a bespoke ``github`` agent is still an agent).
_NOT_AGENTS = frozenset({"workspace", "vscode", "terminal", "github"})
_ACCEPT_RE = re.compile(rf"^{_SLUG}$")


def _split(text: str) -> tuple[str, str]:
    """``(front matter, body)``; the front matter is empty when the text has no leading ``---`` block."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[3:end], text[end + 4:]
    return "", text


def _front_matter_targets(front: str) -> set[str]:
    targets: set[str] = set()
    key = ""
    for line in front.splitlines():
        if m := _FM_KEY.match(line):
            key = m[1]
            continue
        if key == "agents" and (m := _FM_ITEM.match(line)):
            targets.add(m[1])
        elif key == "handoffs" and (m := _FM_AGENT.match(line)):
            targets.add(m[1])
    return targets


def mentioned_slugs(text: str, *, has_agent_file: Callable[[str], bool] | None = None) -> set[str]:
    """Slugs an orchestrator routes to: backticked ``@slug`` in its body, and front-matter ``agents:``/``handoffs:``.

    Args:
        text: The orchestrator file's text.
        has_agent_file: When given, a bare ``@slug`` in the body counts if it returns True for that slug, and a
            chat-participant name (``github``, ``workspace``, ...) counts if it names a real agent file.

    Returns:
        The named slugs.

    Raises:
        Nothing.
    """
    front, body = _split(text)
    named = set(_MENTION.findall(body)) | _front_matter_targets(front)
    if has_agent_file is not None:
        named |= {s for s in _BARE.findall(body) if has_agent_file(s)}
        return {s for s in named if s not in _NOT_AGENTS or has_agent_file(s)}
    return named - _NOT_AGENTS


class _Unreadable(Exception):
    """The orchestrator exists but can't be read as UTF-8 text."""


def _read_orchestrator(path: Path) -> str | None:
    """The on-disk orchestrator's text, or ``None`` when there is none; raises :class:`_Unreadable` otherwise."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise _Unreadable(exc.__class__.__name__) from exc


def _at_risk(output_dir: Path, ext: str, emitted: set[str], old: str, rendered: str) -> list[dict[str, Any]]:
    def has_file(slug: str) -> bool:
        return (output_dir / f"{slug}{ext}").is_file()

    named = mentioned_slugs(old, has_agent_file=has_file) - mentioned_slugs(rendered, has_agent_file=has_file)
    return [{"slug": s, "has_file": has_file(s)} for s in sorted(named - emitted)]


def at_risk_routed_slugs(output_dir: Path, agent_ext: str, emitted: set[str], rendered: str) -> list[dict[str, Any]]:
    """Agents the on-disk orchestrator routes to that neither this build nor its new orchestrator keeps.

    Args:
        output_dir: The team's agents directory.
        agent_ext: The framework's agent-file extension (``.agent.md``, ``.md``, ...).
        emitted: Slugs this build emits (or adopts) as agents.
        rendered: The freshly rendered orchestrator; a slug it still names (an ``(if in team)`` row) keeps its routing.

    Returns:
        One ``{"slug", "has_file"}`` dict per at-risk slug, sorted by slug; empty when there is no orchestrator, or
        when it can't be read (warned about on stderr; :func:`assess_routing_loss` refuses that case).

    Raises:
        Nothing.
    """
    path = output_dir / f"orchestrator{agent_ext}"
    try:
        old = _read_orchestrator(path)
    except _Unreadable as exc:
        print(f"  !  could not read {path.name} to list the agents it routes to ({exc}); "
              "--overwrite refuses until it can", file=sys.stderr)
        return []
    return [] if old is None else _at_risk(output_dir, agent_ext, emitted, old, rendered)


@dataclass(frozen=True)
class RoutingLoss:
    """What an ``--overwrite`` would do to the orchestrator's routing, and whether the run must refuse.

    Attributes:
        at_risk: ``{"slug", "has_file"}`` per routed agent the run would drop, after adoption.
        unreadable: The orchestrator exists but can't be read, so the loss is unknown (refuses).
        accepted: The ``--accept-routing-loss`` set, or None when the flag is absent.
        orchestrator_sha256: The on-disk orchestrator's sha256, for the audit line.
    """

    at_risk: tuple[dict[str, Any], ...] = ()
    unreadable: bool = False
    accepted: frozenset[str] | None = None
    orchestrator_sha256: str | None = None

    @property
    def slugs(self) -> frozenset[str]:
        """The at-risk slugs."""
        return frozenset(item["slug"] for item in self.at_risk)

    @property
    def would_refuse(self) -> bool:
        """True when a real run refuses: unreadable, or a non-empty loss not acknowledged exactly."""
        if self.unreadable:
            return True
        if self.accepted is not None:
            return self.accepted != self.slugs   # exact: no superset, no subset, no wildcard (condition 7)
        return bool(self.at_risk)

    def clearing_options(self) -> list[str]:
        """How a refused run gets through, most precise first."""
        if self.unreadable:
            return ["make the orchestrator readable as UTF-8", "--update --merge (keeps the on-disk body)"]
        if not self.would_refuse:
            return []
        filed = sorted(i["slug"] for i in self.at_risk if i["has_file"])
        options = [f"--adopt-orphans (keeps {', '.join(filed)}, and adopts every other orphan agent file too)"
                   ] if filed else []
        options += ["add them to the brief", "--update --merge (keeps the on-disk body)"]
        if self.at_risk:
            options.append(f"--accept-routing-loss {','.join(sorted(self.slugs))} (drop this exact set on purpose)")
        return options

    def as_report(self) -> dict[str, Any]:
        """The ``routing_loss`` entry of the ``--dry-run --json`` report."""
        return {"would_refuse": self.would_refuse, "unreadable": self.unreadable,
                "accepted": sorted(self.accepted) if self.accepted is not None else None,
                "clear_with": self.clearing_options()}


def _accepted(args: Any) -> frozenset[str] | None:
    raw = getattr(args, "accept_routing_loss", None)
    if raw is None:
        return None
    return frozenset(s.strip() for s in str(raw).split(",") if s.strip())


def assess_routing_loss(output_dir: Path, manifest: dict, adapter: Any, rendered: list[tuple[str, str]],
                        args: Any) -> RoutingLoss:
    """Compute what this ``--overwrite`` run would drop from the orchestrator's routing; the one function both the
    real run's refusal and the dry-run report use (condition 8).

    It compares the on-disk orchestrator with what the run would write in its place: the rendered orchestrator with
    the user-editable regions ``--overwrite`` carries (unless ``--discard-user-regions``). A run that does not
    rewrite the orchestrator (not ``--overwrite``, no rendered orchestrator, or a frozen surface owns it) drops
    nothing. When the carried regions have problems, emit refuses to write the orchestrator, so nothing is lost.

    Args:
        output_dir: The team's agents directory.
        manifest: The build manifest; ``agent_slug_list`` and ``adopted_agents`` (slugs) are what it keeps.
        adapter: The framework adapter, for the agent-file extension.
        rendered: The ``(relative path, content)`` pairs the run would write.
        args: Parsed CLI arguments (``overwrite``, ``discard_user_regions``, ``accept_routing_loss``).

    Returns:
        A :class:`RoutingLoss`.

    Raises:
        Nothing.
    """
    accepted = _accepted(args)
    if not getattr(args, "overwrite", False):
        return RoutingLoss(accepted=accepted)
    from agentteams.cli.frozen_render import drop_frozen
    from agentteams.user_regions import carry_regions

    ext = adapter.get_file_extension("agent")
    entry = next(((p, c) for p, c in rendered if Path(p).name == f"orchestrator{ext}"), None)
    if entry is None or not drop_frozen(manifest, [entry], output_dir, quiet=True):
        return RoutingLoss(accepted=accepted)
    path = output_dir / f"orchestrator{ext}"
    try:
        old = _read_orchestrator(path)
    except _Unreadable:
        return RoutingLoss(unreadable=True, accepted=accepted)
    if old is None:
        return RoutingLoss(accepted=accepted)
    new = entry[1]
    if not getattr(args, "discard_user_regions", False):
        new, _carried, problems = carry_regions(old, new, toml=ext.endswith(".toml"))
        if problems:  # emit refuses to write this orchestrator and says why, so nothing is lost this run
            return RoutingLoss(accepted=accepted)
    emitted = set(manifest.get("agent_slug_list", []) or [])
    emitted |= {a for a in manifest.get("adopted_agents", []) or [] if isinstance(a, str)}
    return RoutingLoss(at_risk=tuple(_at_risk(output_dir, ext, emitted, old, new)), accepted=accepted,
                       orchestrator_sha256=hashlib.sha256(old.encode("utf-8")).hexdigest())


def report_at_risk_routing(output_dir: Path, manifest: dict, adapter: Any, update_rendered: list[tuple[str, str]],
                           args: Any) -> RoutingLoss:
    """Print, on stderr, the routed agents an ``--overwrite`` would orphan, and return the assessment.

    Args:
        output_dir: The team's agents directory.
        manifest: The build manifest.
        adapter: The framework adapter.
        update_rendered: The ``(relative path, content)`` pairs this update would write.
        args: Parsed CLI arguments.

    Returns:
        The :class:`RoutingLoss` (its ``at_risk`` is empty for a merge).

    Raises:
        Nothing.
    """
    loss = assess_routing_loss(output_dir, manifest, adapter, update_rendered, args)
    if loss.at_risk:
        print(f"  !  --overwrite replaces the orchestrator: {len(loss.at_risk)} agent(s) it routes to are not in "
              "this build and lose their routing:", file=sys.stderr)
        for item in loss.at_risk:
            where = "file kept on disk" if item["has_file"] else "no file"
            print(f"       @{item['slug']}  — {where}", file=sys.stderr)
    if loss.would_refuse and getattr(args, "dry_run", False):
        print("     A real run refuses. Clear it with: " + "; ".join(loss.clearing_options()), file=sys.stderr)
    return loss


def refuse_routing_loss(output_dir: Path, manifest: dict, adapter: Any, rendered: list[tuple[str, str]],
                        args: Any) -> int | None:
    """The guard: refuse a real ``--overwrite`` that would drop routing it was not told to drop.

    Called before the overwrite clearance is consumed and before any backup, migration or write, on every
    ``--overwrite`` path. ``--yes`` and the ``--migrate`` exemption play no part. A dry run never refuses.

    Args:
        output_dir: The team's agents directory.
        manifest: The build manifest.
        adapter: The framework adapter.
        rendered: The ``(relative path, content)`` pairs the run would write.
        args: Parsed CLI arguments.

    Returns:
        1 when the run must stop (the reason is on stderr), else None. An exactly acknowledged loss prints one
        audit line (the slugs and the orchestrator's sha256) and proceeds.

    Raises:
        Nothing.
    """
    if not getattr(args, "overwrite", False) or getattr(args, "dry_run", False):
        return None
    loss = assess_routing_loss(output_dir, manifest, adapter, rendered, args)
    if loss.would_refuse:
        if loss.unreadable:
            why = "the orchestrator exists but can't be read, so the routing it would lose is unknown"
        elif loss.accepted is not None:
            why = (f"--accept-routing-loss names {', '.join(sorted(loss.accepted)) or 'nothing'}, but the routing "
                   f"this run drops is exactly {', '.join(sorted(loss.slugs)) or 'nothing'}")
        else:
            why = f"it would drop the routing to {', '.join('@' + s for s in sorted(loss.slugs))}"
        print(f"[ROUTING-LOSS] blocked: --overwrite replaces the orchestrator and {why}. Nothing was written. "
              "Clear it with: " + "; ".join(loss.clearing_options()) + ".", file=sys.stderr)
        return 1
    if loss.at_risk:
        print(f"  ℹ  routing loss accepted: {', '.join(sorted(loss.slugs))} "
              f"(orchestrator sha256 {loss.orchestrator_sha256})", file=sys.stderr)
    return None


def valid_accept_list(raw: str) -> bool:
    """Whether an ``--accept-routing-loss`` value is a comma list of agent slugs (no wildcard).

    Args:
        raw: The flag value.

    Returns:
        True when every item is a slug.

    Raises:
        Nothing.
    """
    items = [s.strip() for s in raw.split(",")]
    return bool(items) and all(_ACCEPT_RE.match(s) for s in items)


__all__ = ["RoutingLoss", "assess_routing_loss", "at_risk_routed_slugs", "mentioned_slugs", "refuse_routing_loss",
           "report_at_risk_routing", "valid_accept_list"]
