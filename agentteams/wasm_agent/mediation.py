"""mediation.py — every model call the WASM agent host makes (P05 §4.4, S-2, S-3, S-8).

One :class:`Mediator` per run owns the host-side conversation and is the only caller of
``model_providers.complete``. Per call it:

1. **DLP** (S-2): the start (system prompt + task) and every new ``tool_result`` block pass
   ``vulnscan.gateway_dlp.Filter`` (block classes refuse the start / withhold the block; redaction classes are
   counted in a ``dlp`` record that never carries the matched text).
2. **Reserves** the worst case in the :class:`~agentteams.wasm_agent.budget.BudgetLedger`.
3. **Journals before the call**: a ``model-request`` record must be acked durable, or no call is made.
4. Calls the provider, commits the real usage (priced when a price table is set), and withholds a reply that echoes a
   known secret (``provider-echoed-secret``).
5. Strips the reserved provider keys from the reply (:class:`ReservedKeyVault`) before anything else sees it. They stay
   host-side, are re-attached only to the very turn they came from, and only after their HMAC re-verifies; a guest can
   never supply one (it never authors history in step mode, and any reserved key found in the host copy is refused).
   The MAC key lives in the host process (Stage 1 when wrapped), so it does not protect against a compromised host.

Model output is data (C-4): the host never parses reply text for instructions.

Stdlib only.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agentteams import model_providers as MP
from agentteams.vulnscan import gateway_dialects as GD
from agentteams.vulnscan import gateway_dlp as DLP
from agentteams.wasm_agent.budget import BudgetExceeded, BudgetLedger
from agentteams.wasm_agent.protocol import canonical
from agentteams.wasm_agent.transcript import JournalError, JournalSink, contains_secret, reserved_mac

RESERVED_KEYS = ("_provider_id", "_provider_sig")
#: The fixed user text the host appends when a guest asks to continue after ``max_tokens``.
CONTINUATION_TEXT = "Continue from where you stopped."
#: Provider error kinds that mean "this run cannot succeed" (others are transient: ``provider-unavailable``).
_FATAL_KINDS = frozenset({"config", "auth", "not_found", "bad_request", "too_large", "refused"})


class MediationError(Exception):
    """A model call could not be made or its reply cannot be used; ``code`` ends the run."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Build the error.

        Args:
            code: ``dlp:<class>``, ``provider-error:<kind>``, ``provider-unavailable``, ``provider-echoed-secret``,
                ``journal-unwritable``, ``reserved-key`` ...
            detail: Human-readable detail (never a key).

        Raises:
            Nothing.
        """
        self.code = code
        self.detail = detail[:300]
        super().__init__(f"{code}: {self.detail}" if detail else code)


# --- DLP -----------------------------------------------------------------------------------------------------------

class OutboundDlp:
    """The outbound DLP filter of one run (gateway_dlp.Filter with the run's known secrets)."""

    def __init__(self, secrets: Iterable[str], *, hostnames: Iterable[str] | None = None,
                 private_names: Iterable[str] = ()) -> None:
        """Build the filter.

        Args:
            secrets: Exact values to block: the provider key and the secrets files' values.
            hostnames: Host names to redact (default: this machine's, ``gateway_dlp.default_hostnames``).
            private_names: fnmatch patterns of private package names to redact.

        Raises:
            Nothing.
        """
        self.filter = DLP.Filter(secrets=secrets, hostnames=DLP.default_hostnames() if hostnames is None else hostnames,
                                 private_names=private_names, redact=True)

    def check_start(self, system: str, task: str) -> tuple[str, str, dict[str, int]]:
        """Scan the system prompt and the task: a block class refuses the run.

        Args:
            system: The system prompt.
            task: The task text.

        Returns:
            ``(system, task, redaction counts)`` with the lesser classes redacted.

        Raises:
            MediationError: ``dlp:<class>`` on a block class.
        """
        body = {"system": system, "messages": [{"role": "user", "content": [{"type": "text", "text": task}]}]}
        try:
            counts = self.filter.apply(GD.MESSAGES, body)
        except DLP.DlpBlocked as exc:
            raise MediationError(f"dlp:{exc.cls}", "the system prompt or task carries a blocked class") from None
        return body["system"], body["messages"][0]["content"][0]["text"], DLP.from_mapping(counts)

    def filter_blocks(self, blocks: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
        """Scan new user-turn blocks one by one: a blocked block's content is withheld, the call goes ahead.

        Args:
            blocks: ``tool_result`` / ``text`` blocks (copied, never mutated).

        Returns:
            ``(blocks, redaction counts, blocked classes)``.

        Raises:
            Nothing.
        """
        out, counts, blocked = [], {}, []
        for block in blocks:
            body = {"messages": [{"role": "user", "content": [copy.deepcopy(block)]}]}
            try:
                found = self.filter.apply(GD.MESSAGES, body)
                new = body["messages"][0]["content"][0]
            except DLP.DlpBlocked as exc:
                blocked.append(exc.cls)
                new = dict(block)
                key = "content" if block.get("type") == "tool_result" else "text"
                new[key] = f"[withheld by DLP: {exc.cls}]"
                if block.get("type") == "tool_result":
                    new["is_error"] = True
                found = {}
            for k, v in found.items():
                counts[k] = counts.get(k, 0) + v
            out.append(new)
        return out, DLP.from_mapping(counts), blocked


# --- reserved provider keys ------------------------------------------------------------------------------------------

def _sha(value: Any) -> str:
    return "" if value is None else hashlib.sha256(canonical(value)).hexdigest()


class ReservedKeyVault:
    """Holds the ``_provider_id`` / ``_provider_sig`` of every reply block host-side, bound by an HMAC."""

    def __init__(self, mac_key: bytes) -> None:
        """Bind the vault to the run's MAC key.

        Args:
            mac_key: At least 32 random bytes (Stage 0 generates them; never journaled, never sent to a guest).

        Raises:
            ValueError: A short key.
        """
        if not isinstance(mac_key, bytes) or len(mac_key) < 32:
            raise ValueError("the reserved-key MAC key must be at least 32 bytes")
        self._key = mac_key
        self._held: dict[tuple[int, int], tuple[dict[str, Any], dict[str, Any]]] = {}

    def strip(self, turn: int, content: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Remove the reserved keys from one assistant turn and keep them, with a MAC, host-side.

        Args:
            turn: The turn's index in the host's message list.
            content: The reply blocks as :func:`model_providers.complete` returned them.

        Returns:
            ``(stripped blocks, journal entries)``; an entry holds the turn, index, block type and id, the sha256 of
            each reserved value, and the MAC (never the values).

        Raises:
            Nothing.
        """
        stripped, entries = [], []
        for index, block in enumerate(content):
            plain = {k: copy.deepcopy(v) for k, v in block.items() if k not in RESERVED_KEYS}
            stripped.append(plain)
            reserved = {k: block[k] for k in RESERVED_KEYS if k in block}
            if not reserved:
                continue
            entry = {"turn": turn, "index": index, "type": str(block.get("type")), "id": str(block.get("id", "")),
                     "pid_sha256": _sha(reserved.get("_provider_id")) if "_provider_id" in reserved else "-",
                     "sig_sha256": _sha(reserved.get("_provider_sig")) if "_provider_sig" in reserved else "-"}
            entry["mac"] = reserved_mac(self._key, entry)
            self._held[(turn, index)] = (reserved, entry)
            entries.append(entry)
        return stripped, entries

    def restore(self, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """A deep copy of the host's messages with the held reserved keys re-attached to their own blocks.

        Args:
            messages: The host-side (stripped) conversation.

        Returns:
            The request messages.

        Raises:
            MediationError: ``reserved-key`` when a host-side block already carries a reserved key (it can only have
                come from outside the vault) or a held value no longer matches its MAC.
        """
        out = copy.deepcopy([dict(m) for m in messages])
        for turn, msg in enumerate(out):
            for index, block in enumerate(msg["content"]):
                if any(k in block for k in RESERVED_KEYS):
                    raise MediationError("reserved-key", f"turn {turn} block {index} carries a reserved key")
                held = self._held.get((turn, index))
                if held is None:
                    continue
                reserved, entry = held
                check = {**entry, "type": str(block.get("type")), "id": str(block.get("id", "")),
                         "pid_sha256": _sha(reserved.get("_provider_id")) if "_provider_id" in reserved else "-",
                         "sig_sha256": _sha(reserved.get("_provider_sig")) if "_provider_sig" in reserved else "-"}
                if msg["role"] != "assistant" or not hmac.compare_digest(entry["mac"], reserved_mac(self._key, check)):
                    raise MediationError("reserved-key", f"turn {turn} block {index}: reserved-key MAC mismatch")
                block.update(copy.deepcopy(reserved))
        return out


# --- the mediator ----------------------------------------------------------------------------------------------------

class Mediator:
    """The one conversation and model-call path of a run (see the module docstring)."""

    def __init__(self, *, provider: MP.Provider, model: str, system: str, max_tokens: int,
                 tools: Sequence[MP.ToolSpec], ledger: BudgetLedger, journal: JournalSink, dlp: OutboundDlp,
                 vault: ReservedKeyVault, secrets: Iterable[str], prices: Mapping[str, Mapping[str, float]] | None,
                 call_timeout_s: float = 300.0) -> None:
        """Bind everything one run's model calls need.

        Args:
            provider: The P06 provider (holds the key; never leaves this process).
            model: The operator-configured model id.
            system: The (DLP-checked) system prompt.
            max_tokens: Output ceiling per call.
            tools: Tool specs offered to the model.
            ledger: The run's budget ledger.
            journal: The run's journal sink.
            dlp: The run's outbound DLP.
            vault: The run's reserved-key vault.
            secrets: Known secrets a reply must not echo.
            prices: ``gateway_dialects.load_prices`` table, or None.
            call_timeout_s: Per-call socket timeout cap (also capped by the wall clock left).

        Raises:
            Nothing.
        """
        self.provider, self.model, self.system, self.max_tokens = provider, model, system, max_tokens
        self.tools = tuple(tools)
        self.ledger, self.journal, self.dlp, self.vault = ledger, journal, dlp, vault
        self.secrets = tuple(s for s in secrets if len(s) >= 8)
        self.prices = prices
        self.call_timeout_s = call_timeout_s
        self.messages: list[dict[str, Any]] = []
        self.calls = 0

    def start(self, task: str) -> None:
        """Open the conversation with the (DLP-checked) task as the first user turn.

        Args:
            task: The task text.

        Returns:
            None.

        Raises:
            Nothing.
        """
        self.messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]

    def add_user_turn(self, blocks: Sequence[dict[str, Any]]) -> None:
        """Append a DLP-filtered user turn (tool results or the fixed continuation).

        Args:
            blocks: The new blocks.

        Returns:
            None.

        Raises:
            MediationError: ``journal-unwritable`` when the ``dlp`` record cannot be journaled.
        """
        filtered, counts, blocked = self.dlp.filter_blocks(blocks)
        if counts or blocked:
            try:
                self.journal.append("dlp", {"scope": "user-turn", "redactions": counts, "blocked": blocked})
            except JournalError as exc:
                raise MediationError("journal-unwritable", str(exc)) from exc
        self.messages.append({"role": "user", "content": filtered})

    def _usd_estimate(self, input_bytes: int) -> float:
        # The gateway's own reservation pricing (the input at the dearer of input/cache-write; 0 when unpriced).
        checked = GD.Checked(b"", self.model, max_output=self.max_tokens,
                             input_estimate=-(-input_bytes // GD.BYTES_PER_TOKEN))
        return GD.reservation(self.prices or {}, checked)[1]

    def call(self) -> MP.ModelReply:
        """Make one model call on the current conversation and append the (stripped) reply.

        Returns:
            The reply with the reserved keys stripped from ``content``.

        Raises:
            BudgetExceeded: The reservation would exceed a cap (nothing was called).
            MediationError: Journal failure, provider failure, echoed secret, or a reserved-key violation.
        """
        request = self.vault.restore(self.messages)
        digest = hashlib.sha256(canonical({"system": self.system, "messages": self.messages,
                                           "tools": [t.name for t in self.tools]})).hexdigest()
        size = len(canonical(request)) + len(self.system.encode())
        reservation = self.ledger.reserve(size, self.max_tokens, self._usd_estimate(size))
        try:
            self.journal.append("model-request", {"call": self.calls, "model": self.model, "max_tokens": self.max_tokens,
                                                  "request_sha256": digest, "reservation": {
                                                      "tokens": reservation.tokens, "usd": reservation.usd}})
        except JournalError as exc:
            self.ledger.refund(reservation)
            raise MediationError("journal-unwritable", str(exc)) from exc
        timeout = max(1.0, min(self.call_timeout_s, self.ledger.remaining_s()))
        self.calls += 1
        try:
            reply = MP.complete(self.provider, self.model, request, system=self.system, tools=self.tools,
                                max_tokens=self.max_tokens, timeout=timeout)
        except MP.ModelProviderError as exc:
            if exc.attempts > 0:   # possibly billed: keep the worst case
                self.ledger.commit(reservation, reservation.tokens, None)
            else:
                self.ledger.refund(reservation)
            code = f"provider-error:{exc.kind}" if exc.kind in _FATAL_KINDS else "provider-unavailable"
            raise MediationError(code, exc.message) from None
        u = reply.usage
        tokens = u.input_tokens + u.output_tokens + u.cache_read + u.cache_write
        usd = GD.priced(self.prices, self.model, u) if self.prices else None
        self.ledger.commit(reservation, tokens, usd if usd is not None else u.cost)
        if contains_secret(canonical(list(reply.content)).decode("unicode_escape", "replace"), self.secrets):
            raise MediationError("provider-echoed-secret", "the reply contains a known secret; withheld")
        turn = len(self.messages)
        stripped, entries = self.vault.strip(turn, reply.content)
        try:
            self.journal.append("model-reply", {"call": self.calls - 1, "stop": reply.stop, "raw_stop": reply.raw_stop,
                                                "usage": {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                                          "cache_read": u.cache_read, "cache_write": u.cache_write,
                                                          "cost": u.cost, "usd": usd},
                                                "request_id": reply.request_id, "dropped_blocks": reply.dropped_blocks,
                                                "content": stripped, "reserved": entries})
        except JournalError as exc:
            raise MediationError("journal-unwritable", str(exc)) from exc
        if not stripped:   # an empty assistant turn would break alternation; keep the conversation well-formed
            stripped = [{"type": "text", "text": ""}]
        self.messages.append({"role": "assistant", "content": stripped})
        return MP.ModelReply(tuple(stripped), reply.stop, reply.raw_stop, u, reply.provider, reply.model,
                             reply.request_id, reply.dropped_blocks)


def usd_cap_refusal(max_usd: float | None, prices: Mapping[str, Any] | None, model: str) -> str | None:
    """S-8: a USD cap without a price for the model refuses the run (it would otherwise run unmetered).

    Args:
        max_usd: The budget's USD cap.
        prices: The price table, or None.
        model: The model id.

    Returns:
        A refusal reason, or None.

    Raises:
        Nothing.
    """
    if max_usd is not None and (not prices or model not in prices):
        return f"max_usd is set but model {model!r} has no price (fail closed; add it to the price table)"
    return None


__all__ = ["CONTINUATION_TEXT", "BudgetExceeded", "MediationError", "Mediator", "OutboundDlp", "ReservedKeyVault",
           "usd_cap_refusal"]
