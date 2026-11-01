"""agentteams.wasm_agent — the WASM agent runtime host (ARCH-B, plan P05).

One agent runs as a sequence of step-mode WASM guest invocations (:mod:`.protocol`). The host alone holds the model
key, calls the model (:mod:`.mediation`), runs host tools (:mod:`.tools`), enforces budgets (:mod:`.budget`) and
revocation, applies DLP to every outbound prompt, and has Stage 0 write a hash-chained transcript
(:mod:`.transcript`). :mod:`.launch` is the unconfined Stage 0 that owns ``.agentteams/``; :mod:`.stage1` is the
OS-confined host process. :mod:`.delegation` (with Stage 0's :mod:`.child_runs`) runs sub-agents for the ``delegate``
tool (P09), each with a grant never wider than its delegator's.

Public entry points (P08, P09, P17): :func:`run_agent`, :class:`AgentSpec`, :class:`RunOptions`,
:class:`RunResult`, :class:`Budget`, :class:`BudgetLedger`, :class:`BudgetExceeded` (a cap would be or was
exceeded; ``cap`` names it), :class:`RunLink` (a parent run for P09 delegation, so revoking an ancestor stops the
child), :func:`verify_transcript` / :class:`TranscriptReport`, :func:`revoke_run`, :func:`stage0`,
:data:`PROTOCOL_VERSION`, :class:`ProtocolError`.

Stdlib only.
"""

from __future__ import annotations

from agentteams.wasm_agent.budget import Budget, BudgetExceeded, BudgetLedger
from agentteams.wasm_agent.host import AgentSpec, RunLink, RunOptions, RunResult, run_agent
from agentteams.wasm_agent.launch import stage0
from agentteams.wasm_agent.protocol import PROTOCOL_VERSION, ProtocolError
from agentteams.wasm_agent.transcript import TranscriptReport, revoke_run, verify_transcript

__all__ = ["PROTOCOL_VERSION", "AgentSpec", "Budget", "BudgetExceeded", "BudgetLedger", "ProtocolError", "RunLink",
           "RunOptions", "RunResult", "TranscriptReport", "revoke_run", "run_agent", "stage0", "verify_transcript"]
