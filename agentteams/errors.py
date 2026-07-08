"""errors.py — exception hierarchy for agentteams.

AgentTeamsError is the package base; artifact-emission errors also subclass
RuntimeError to preserve existing `except RuntimeError` call sites. Extracted
from build_team.py (CH-07/CH-24 — one typed home for wrap-and-reraise).
"""

from __future__ import annotations


class AgentTeamsError(Exception):
    """Base class for all agentteams-raised errors."""


class DeliveryReceiptError(AgentTeamsError, RuntimeError):
    """Raised when the delivery receipt cannot be produced or fails schema
    validation (RA2). Callers treat this as non-fatal: the build-log heal
    stands and the next ``--update`` re-emits the receipt."""
class EvalSuiteError(AgentTeamsError, RuntimeError):
    """Raised when the eval suite cannot be produced or fails schema
    validation (Cluster A Phase 2). Non-fatal to the caller, like
    DeliveryReceiptError — the build-log heal stands and the next ``--update``
    re-emits."""
class ModelRoutingError(AgentTeamsError, RuntimeError):
    """Raised when the model-routing contract fails schema validation (F6).
    Non-fatal to the caller, like EvalSuiteError — emitted only under
    --cost-routing; a malformed contract is not written and the next run
    re-emits."""
class MemoryIndexError(AgentTeamsError, RuntimeError):
    """Raised when the memory index fails schema validation (F8). Non-fatal
    at the call site: the existing work-summary documents are the source of
    truth — the navigator falls back to opening them and to filesystem search
    when the index is absent / stale / malformed."""
class CodeIndexError(AgentTeamsError, RuntimeError):
    """Raised when a code-index partition/manifest fails schema validation
    (F-CODEIDX). Non-fatal at the call site: the source files are the source of
    truth — a query falls back to opening the referenced file and to filesystem
    search when the code index is absent / stale / malformed. The code index is
    a gitignored local cache, never a committed/drift-tracked artifact."""
