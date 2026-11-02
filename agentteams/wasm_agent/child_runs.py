"""child_runs.py — Stage 0's child-run service for WASM delegation (WASM P09, METAPLAN R4).

Stage 0 is the only writer under ``.agentteams/`` (R4), and only Stage 0 holds grants (the grant never crosses the
control pipe from Stage 1). So when a run delegates, Stage 0 — not the host loop — decides and builds the child:
:class:`ChildRuns` re-checks the delegation against its own :class:`~agentteams.wasm_agent.delegation.RunTree`
(target in the delegator's effective ``delegates``, no cycle, depth, fan-out and tree caps), resolves the target from
the lock-verified team, computes the attenuated spec (:func:`~agentteams.wasm_agent.delegation.effective_child_spec`,
never wider than the delegator's, D11), creates the child's flat run dir and transcript
(:class:`~agentteams.wasm_agent.transcript.FileJournal`) and a :class:`~agentteams.wasm_agent.broker.StepPreparer`
holding the child's effective grant, so every child step is a fresh guest on that grant.

In Stage 0's in-process mode the host loop calls :meth:`ChildRuns.open` / :meth:`ChildRuns.close` directly; with a
wrapped Stage 1, ``launch.serve_control`` serves ``child-open`` / ``child-close`` and routes a request's ``run`` to
:meth:`ChildRuns.journal_for` / :meth:`ChildRuns.preparer_for`. A child's scratch dir must be one Stage 0 created and
granted as a write root at launch (``reachable_scratch_dirs``); nothing here derives a path from model output.

Integrity-pinned. Stdlib only.
"""

from __future__ import annotations

import contextlib
import secrets as _secrets
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from agentteams.wasm_exec import WasmExecError
from agentteams.wasm_agent.broker import BrokerError, LocalBroker, StepPreparer
from agentteams.wasm_agent.delegation import ChildSlot, DelegationError, RunTree, effective_child_spec
from agentteams.wasm_agent.transcript import FileJournal, JournalError

#: Result keys Stage 0 keeps from a child's (Stage-1-reported) result when it writes ``result.json``.
_RESULT_KEYS = ("status", "reason", "output", "usage", "notices", "step_timings")


class ChildRuns:
    """Every child run of one tree (see the module docstring)."""

    def __init__(self, *, project_root: str, root_run_id: str, root_spec: Any, tree: RunTree,
                 resolve_agent: Callable[[str], Any], make_preparer: Callable[[Any], StepPreparer],
                 write_roots: Iterable[str], secrets: Iterable[str], mac_key: bytes) -> None:
        """Bind the tree to its root run.

        Args:
            project_root: The project root (real path).
            root_run_id: The root run (already journaled by Stage 0).
            root_spec: The root's effective ``AgentSpec``.
            tree: The tree's limits and bookkeeping.
            resolve_agent: ``slug -> AgentSpec`` from the lock-verified team (``Team.agent_spec``).
            make_preparer: ``grant -> StepPreparer`` with Stage 0's guest, resolver and confinement settings.
            write_roots: The scratch dirs Stage 0 created at launch (the only scratch a child may get).
            secrets: Known secrets (no child record may carry one).
            mac_key: The tree's reserved-key MAC key (its Stage 1 holds the vault).

        Raises:
            KeyError: A duplicate root.
        """
        self.root, self.tree = project_root, tree
        self.resolve_agent, self.make_preparer = resolve_agent, make_preparer
        self.write_roots = frozenset(write_roots)
        self.secrets, self.mac_key = tuple(secrets), mac_key
        self.journals: dict[str, FileJournal] = {}
        self.preparers: dict[str, StepPreparer] = {}
        tree.add(root_run_id, root_spec, None)

    def open(self, *, parent: str, agent: str, delegation_id: str) -> ChildSlot:
        """Validate one delegation and create the child run.

        Args:
            parent: The delegating run id.
            agent: The target agent.
            delegation_id: The parent's tool_use id (recorded by the parent; checked for shape only).

        Returns:
            The :class:`ChildSlot` (in-process journal and broker).

        Raises:
            DelegationError: Any refusal (the tree's checks, an unresolvable target, a scratch dir Stage 0 did not
                prepare, or an unwritable run dir).
        """
        from agentteams.wasm_agent.team import TeamError

        if not isinstance(agent, str) or not isinstance(parent, str) or not isinstance(delegation_id, str):
            raise DelegationError("refused", "malformed child-open request")
        self.tree.check(parent, agent)
        node = self.tree.nodes[parent]
        try:
            declared = self.resolve_agent(agent)
        except (TeamError, KeyError, ValueError) as exc:
            raise DelegationError("refused", f"cannot load agent {agent[:64]!r}: {exc}") from exc
        depth = node.depth + 1
        try:
            spec, dropped = effective_child_spec(node.spec, declared, project_root=self.root,
                                                 can_delegate=self.tree.can_delegate_at(depth))
        except (ValueError, TypeError) as exc:
            raise DelegationError("refused", f"cannot attenuate {agent[:64]!r}'s grant: {exc}") from exc
        for m in spec.grant.mounts:
            if m.guest == "/scratch" and m.host not in self.write_roots:
                raise DelegationError("refused", f"{agent[:64]!r}'s scratch dir was not prepared at launch")
        run_id = _secrets.token_hex(16)
        try:
            preparer = self.make_preparer(spec.grant)
            journal = FileJournal(self.root, run_id, secrets=self.secrets, mac_key=self.mac_key)
        except JournalError as exc:
            raise DelegationError("refused", f"cannot create the child run: {exc}") from exc
        except (BrokerError, WasmExecError) as exc:
            raise DelegationError("refused", f"cannot prepare the child's guests: {str(exc)[:200]}") from exc
        self.tree.add(run_id, spec, parent)
        self.journals[run_id], self.preparers[run_id] = journal, preparer
        return ChildSlot(run_id, spec, journal, LocalBroker(preparer), dropped, depth)

    def journal_for(self, run_id: str) -> FileJournal:
        """The transcript writer of a live child run.

        Args:
            run_id: The child.

        Returns:
            Its journal.

        Raises:
            JournalError: Not a live child of this tree.
        """
        journal = self.journals.get(run_id) if isinstance(run_id, str) else None
        if journal is None or not self.tree.nodes[run_id].open:
            raise JournalError("not a live child run of this tree")
        return journal

    def preparer_for(self, run_id: str) -> StepPreparer:
        """The guest preparer (holding the child's effective grant) of a live child run.

        Args:
            run_id: The child.

        Returns:
            Its preparer.

        Raises:
            JournalError: Not a live child of this tree.
        """
        self.journal_for(run_id)
        return self.preparers[run_id]

    def close(self, run_id: str, result: Mapping[str, Any]) -> None:
        """Finish one child run: close its transcript (``run-end`` if missing), write ``result.json``, release.

        Args:
            run_id: The child.
            result: Its result as the host loop reported it (data; only known keys kept).

        Returns:
            None.

        Raises:
            Nothing (an unknown or already closed child is ignored).
        """
        journal = self.journals.get(run_id) if isinstance(run_id, str) else None
        if journal is None or not self.tree.nodes[run_id].open:
            return
        self.tree.nodes[run_id].open = False
        self.preparers[run_id].release_all()
        journal.close()
        data = {k: result.get(k) for k in _RESULT_KEYS} if isinstance(result, Mapping) else {}
        data.update(run_id=run_id, transcript_head=journal.head, transcript_path=str(journal.path))
        with contextlib.suppress(JournalError, TypeError, ValueError):
            journal.write_result(data)

    def close_all(self) -> None:
        """Close every child still open (run end, or a Stage 1 that died mid-child).

        Returns:
            None.

        Raises:
            Nothing.
        """
        for run_id in list(self.journals):
            self.close(run_id, {"status": "failed", "reason": "host-exited"})


__all__ = ["ChildRuns"]
