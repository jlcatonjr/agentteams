"""Known findings: the operator's signed, expiring decision to accept a finding (vfr-k K1, Rule 15).

A *known entry* records that the operator looked at a finding and decided to live with it for a while.
While the entry is valid the finding is graded ``known until <date>`` and leaves the exit code. It never
leaves the report: it stays in its table, the summary lists every entry under *Known findings* with its
reason, expiry, key id, decision reference and renewal count, and ``summary.json`` carries it to the agent.

**Supersedes operator notes for this purpose.** An operator note (:mod:`.notes`) is unsigned prompt text that
steers triage and never suppresses anything; that stays so. The 2026-11-01 zstd "known finding" was such a
note. A note's semantics (target, text, expiry) migrate into a proposal with ``known propose --from-note``:
the note text becomes the reason, its target must match the findings, and nothing is suppressed until the
operator signs the proposal.

**An entry is constraint-relaxing** (Rule 15): it relaxes the exit-code gate. So:

- it is **Ed25519-signed by the operator** through :func:`.grant.operator_sign`, with its own purpose tag
  (:data:`PURPOSE_TAG`), and verified at **every** load against the operator verify-key store
  (:func:`.grant.verify_key`). A symmetric (HMAC) signature is refused;
- its lifetime is at most :data:`MAX_DAYS` days;
- its class is **derived** from its effect by :func:`agentteams.cli.effect_classifier.classify_row`, and an
  effect the classifier finds non-eligible is refused;
- a ``@security`` HALT on :data:`ACTION_ID` (or on ``vulnscan-known:<anything>``, which matches it) voids **every**
  entry; a HALT on the bare entry id ``vk-…``, or on any id in its renewal lineage, voids that entry;
- a member may not be CRITICAL: vulnscan reads no CISA KEV catalog yet, so whether it is exploited is unknown;
- its decision reference must name a cleared row of the decision log (a ``CLEARED-WITH-CONDITIONS`` or
  ``CONDITIONAL`` row only once its ``conditions_verified`` is ``verified``), and a HALT, retraction or revocation
  recorded for that reference voids it;
- the body's sha256 must sit in the **git-tracked** index :data:`INDEX_REL` of its governance dir, read
  from the committed ``HEAD`` (an uncommitted edit counts for nothing);
- at most :data:`MAX_ACTIVE` entries are in force (a group counts as one). Over the cap the **newest** are
  refused and the existing ones stay valid, unlike :data:`.grant.MAX_ACTIVE`, which refuses all.

**Never known**, even when signed: a trust-root target (:func:`refusal_reason`), a ``MAL-*`` advisory, a
malicious package, a compromise indicator, a requirement-range, integrity or coverage finding, and a host
check other than the web-root exposure check (which carries content evidence) or the OS release check.

**Key.** A ``package`` entry keys on (target, kind, ecosystem, name, installed version, advisory ids). A
``group`` entry signs its **full member list**, each member keyed the same way; it is never a location
prefix, so a package that appears later under the same path is graded normally. A ``host`` entry for the
web-root exposure check keys on its detail and on :attr:`.report.Finding.evidence`: the audit's one digest over
each file's HMAC (a random per-host key kept on the host; rotating it voids the host's entries) and the nginx
``deny`` rules (the ``HOSTEVIDENCE`` line of ``L1-audit.sh``). No plain hash of a secret-looking file is printed.

**Void** (the finding is graded normally and the report gains a HIGH ``integrity`` finding saying why):
the installed version changed; an advisory id appeared that the entry did not sign; a fixed release is
available that the entry did not record (:attr:`Member.fix` records the fix state at signing); the
severity rose, or the finding is now on CISA KEV; the evidence changed; the entry expired; the decision
reference is missing, retracted or HALTed; the index row is missing or does not match; the signature,
purpose or bounds fail; the entry is over the cap.

**Renewal** is a new entry with a new id whose ``lineage`` lists every earlier id (signed); it is counted
(``renewal_count``) and shown, and a HALT on any ancestor voids it. The renewed entry is superseded.

Records live outside every project, in ``<config>/vulnscan-known/<entry_id>.json`` (mode 600).
:mod:`.known_report` loads and judges them and grades a report; :mod:`.known_cli` holds the commands.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths
from agentteams.vulnscan.report import OS_RELEASE, Finding

PURPOSE_TAG = "agentteams-vulnscan-known-v1"
#: The decision-log action every entry is checked under: a HALT on it voids all entries (C-2).
ACTION_ID = "vulnscan-known"
MAX_DAYS = 90
#: Severity rank (CRITICAL) a member may not have: without the CISA KEV catalog, exploitation is unknown (@security R2).
CRITICAL_RANK = 4
#: Aggregate cap (Rule 15(d)): entries in force at once; a group counts as one.
MAX_ACTIVE = 30
#: Members of one group entry (a group signs its full list, so it is bounded).
MAX_GROUP_MEMBERS = 40
MAX_IDS_PER_MEMBER = 64
MAX_LINEAGE = 24
REASON_MAX_CHARS = 500
#: How much of a reason the summary shows (the rest is cut, with an ellipsis).
SHOWN_REASON_CHARS = 200
TEXT_MAX_CHARS = 256
#: The git-tracked index, relative to the entry's governance dir.
INDEX_REL = "references/vulnscan-known-index.csv"
INDEX_HEADER = ("entry_id", "body_sha256", "renews", "renewal_count", "added")
DECISIONS_REL = "references/security-decisions.log.csv"
ENTRY_KINDS = ("package", "group", "host")
#: Finding kinds an entry may cover. ``range``, ``ioc`` and ``integrity`` never.
MEMBER_KINDS = ("package", "runtime", "image", "host")
#: Decision-log verdicts that clear a decision reference.
CLEARED_DECISIONS = ("CLEAR", "CLEARED", "CLEARED-WITH-CONDITIONS", "PASS", "CONDITIONAL PASS", "APPROVED")
#: A cleared verdict containing this word counts only when the row's ``conditions_verified`` is ``verified``.
CONDITIONAL_WORD = "CONDITION"
#: A decision row for the reference whose verdict contains one of these voids the entry.
RETRACTING_WORDS = ("HALT", "RETRACT", "REVOK", "WITHDRAW", "REJECT", "DENIED")
FIELDS = ("entry_id", "kind", "target", "members", "reason", "decision_ref", "issued", "expires", "key_id",
          "governance_dir", "renews", "lineage", "renewal_count")
MEMBER_FIELDS = ("kind", "ecosystem", "name", "version", "ids", "detail", "evidence", "fix", "severity", "kev")
#: The web-root exposure check of ``L1-audit.sh``: the one generic host check that can be known (it has evidence).
DOCROOT_DETAIL_PREFIX = "secret-looking files under web root "
#: Canonical path fragments (``governance_targets.canon_path`` form) of trust roots this module adds to the shared
#: vocabulary: the signing store, secrets, grants and ledger, this index and store, the vulnscan install.
TRUST_ROOT_MARKERS: tuple[str, ...] = (
    "agentteams-keys", "verify-key", "signing-key", "agentteams-secrets", "management-signing",
    "security-approver", "authorized-manager", "roster", "enforcement-integrity", "integrity-manifest",
    "claude-md", "constitution", "copilot-instructions", "vulnscan-grants", "vulnscan-publish-grants",
    "ledger-jsonl", "vulnscan-ledger", "vulnscan-known", "security-decision", "agentteams-vulnscan",
)
#: The package names of this tool itself: a finding on the install that judges findings is never known.
SELF_PACKAGES = frozenset({"agentteams"})

ENTRY_ID_RE = re.compile(r"^vk-[0-9a-f]{16}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_RE = re.compile(r"^sha256:[0-9a-f]{64};files=([1-9][0-9]{0,3});deny=([1-9][0-9]{0,3})$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class KnownError(ValueError):
    """A known entry could not be built, verified or applied."""


def store_dir() -> Path:
    """Return the operator-owned known-entry directory.

    Returns:
        ``<config>/vulnscan-known`` (not created).
    """
    return paths.config_dir() / "vulnscan-known"


def utc_now() -> datetime:
    """Return the current time in UTC (the clock every entry check reads).

    Returns:
        A timezone-aware datetime.
    """
    return datetime.now(UTC)


# ---------------------------------------------------------------- shape

def _text(value: Any, what: str, *, limit: int = TEXT_MAX_CHARS, empty: bool = False) -> str:
    if not isinstance(value, str) or CTRL_RE.search(value) or len(value) > limit or (not empty and not value.strip()):
        raise KnownError(f"{what} must be one line of at most {limit} characters")
    return value


def _check_member(m: Any) -> dict[str, Any]:
    if not isinstance(m, dict) or set(m) != set(MEMBER_FIELDS):
        raise KnownError(f"a member must have exactly the fields {list(MEMBER_FIELDS)}")
    if m["kind"] not in MEMBER_KINDS:
        raise KnownError(f"a {m['kind']!r} finding can never be known (kinds: {', '.join(MEMBER_KINDS)})")
    _text(m["ecosystem"], "ecosystem", empty=True)
    _text(m["name"], "name")
    _text(m["version"], "version", empty=True)
    _text(m["detail"], "detail", empty=True)
    _text(m["fix"], "fix", empty=True)
    if not isinstance(m["ids"], list) or len(m["ids"]) > MAX_IDS_PER_MEMBER \
            or any(not isinstance(i, str) for i in m["ids"]) or m["ids"] != sorted(set(m["ids"])):
        raise KnownError(f"ids must be a sorted list of at most {MAX_IDS_PER_MEMBER} distinct strings")
    for i in m["ids"]:
        _text(i, "advisory id", limit=128)
    if isinstance(m["severity"], bool) or not isinstance(m["severity"], int) or not 0 <= m["severity"] <= 4:
        raise KnownError("severity must be a rank 0..4")
    if not isinstance(m["kev"], bool):
        raise KnownError("kev must be true or false")
    if m["severity"] >= CRITICAL_RANK:
        raise KnownError("a CRITICAL finding cannot be known: vulnscan does not read the CISA KEV catalog yet, so "
                         "whether it is exploited is unknown")
    if not isinstance(m["evidence"], str):
        raise KnownError("evidence must be a string")
    if m["kind"] == "host":
        if m["name"] == OS_RELEASE:
            if not m["version"] or m["evidence"] or m["ids"]:
                raise KnownError("an os-release entry keys on its version (no evidence, no advisory ids)")
        elif m["name"] == "host" and m["detail"].startswith(DOCROOT_DETAIL_PREFIX):
            if not _EVIDENCE_RE.match(m["evidence"]) or m["version"] or m["ids"]:
                raise KnownError("a web-root host entry keys on its evidence: the audit's digest over the files' "
                                 "HMACs and the nginx deny rule (at least one of each); the audit must emit HOSTEVIDENCE")
        else:
            raise KnownError("only the web-root exposure check (with evidence) and the os-release check of a host "
                             "can be known")
    else:
        if not m["version"] or m["evidence"] or m["detail"]:
            raise KnownError(f"a {m['kind']} entry keys on its installed version (no detail, no evidence)")
    return m


def member_identity(m: dict[str, Any]) -> tuple[str, ...]:
    """Return what a member is *about* (its finding identity without version or advisories).

    Args:
        m: A member.

    Returns:
        ``(kind, ecosystem, lowercased name, detail)``.
    """
    return (m["kind"], m["ecosystem"], m["name"].lower(), m["detail"])


def finding_identity(f: Finding) -> tuple[str, ...]:
    """Return a finding's identity in :func:`member_identity` form.

    The detail is part of a host finding's identity (each host check is its own finding) and nothing else's.

    Args:
        f: A finding.

    Returns:
        ``(kind, ecosystem, lowercased name, detail or "")``.
    """
    detail = f.detail if f.kind == "host" and f.name == "host" else ""
    return (f.kind, f.ecosystem, f.name.lower(), detail)


def _check_shape(record: dict[str, Any]) -> None:
    if not isinstance(record, dict) or (set(record) != {*FIELDS, "signature"} and set(record) != set(FIELDS)):
        raise KnownError("unexpected or missing fields (an entry has no prefix, scope or effect_class field)")
    if not ENTRY_ID_RE.match(str(record["entry_id"])):
        raise KnownError("malformed entry_id")
    if record["kind"] not in ENTRY_KINDS:
        raise KnownError(f"kind must be one of {', '.join(ENTRY_KINDS)}")
    _text(record["target"], "target")
    members = record["members"]
    if not isinstance(members, list) or not members:
        raise KnownError("an entry needs at least one member")
    for m in members:
        _check_member(m)
    if record["kind"] == "group":
        if not 2 <= len(members) <= MAX_GROUP_MEMBERS:
            raise KnownError(f"a group entry lists 2..{MAX_GROUP_MEMBERS} members (each signed in full)")
        if any(m["kind"] == "host" for m in members):
            raise KnownError("a group entry lists package, runtime or image members only")
    elif len(members) != 1:
        raise KnownError(f"a {record['kind']} entry has exactly one member")
    elif (record["kind"] == "host") != (members[0]["kind"] == "host"):
        raise KnownError("a host entry has one host member; a package entry one non-host member")
    keys = [(*member_identity(m), m["version"]) for m in members]
    if len(set(keys)) != len(keys):
        raise KnownError("duplicate members")
    reason = record["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX_CHARS or "\x00" in reason:
        raise KnownError(f"reason must be 1..{REASON_MAX_CHARS} characters")
    ref = record["decision_ref"]
    if not isinstance(ref, dict) or set(ref) != {"plan_slug", "step"}:
        raise KnownError("decision_ref must be {plan_slug, step} naming a decision-log row")
    _text(ref["plan_slug"], "decision_ref.plan_slug", limit=200)
    _text(ref["step"], "decision_ref.step", limit=200)
    if not _KEY_ID_RE.match(str(record["key_id"])):
        raise KnownError(f"malformed key_id {record['key_id']!r}")
    _text(record["governance_dir"], "governance_dir", limit=1024)
    if not Path(record["governance_dir"]).is_absolute():
        raise KnownError("governance_dir must be absolute")
    lineage, count, renews = record["lineage"], record["renewal_count"], record["renews"]
    if not isinstance(lineage, list) or len(lineage) > MAX_LINEAGE \
            or any(not ENTRY_ID_RE.match(str(i)) for i in lineage):
        raise KnownError(f"lineage must list at most {MAX_LINEAGE} entry ids")
    if record["entry_id"] in lineage or len(set(lineage)) != len(lineage):
        raise KnownError("lineage repeats an id")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(lineage):
        raise KnownError("renewal_count must equal the length of the lineage")
    if renews != (lineage[-1] if lineage else ""):
        raise KnownError("renews must be the last id of the lineage ('' for a first entry)")
    try:
        issued = datetime.fromisoformat(str(record["issued"]))
        expires = datetime.fromisoformat(str(record["expires"]))
        if issued.tzinfo is None or expires.tzinfo is None:
            raise KnownError("issued and expires must carry a UTC offset")
        if not timedelta(0) < expires - issued <= timedelta(days=MAX_DAYS, minutes=1):
            raise KnownError(f"lifetime must be at most {MAX_DAYS} days")
    except (TypeError, ValueError) as exc:
        raise KnownError(f"bad issued/expires: {exc}") from exc


# ---------------------------------------------------------------- refusals (Rule 15(c)) and the derived class

def is_trust_root(text: str) -> bool:
    """Return whether ``text`` names a trust root, as written or, for a path, after resolving symlinks.

    Args:
        text: A target, package name, detail or location.

    Returns:
        True when the shared governance vocabulary or :data:`TRUST_ROOT_MARKERS` matches either form.
    """
    from agentteams.cli.governance_targets import canon_path, target_is_governance_root

    forms = [text]
    if "/" in text or text.startswith("~"):   # path-like: a symlink must not hide where it points
        forms.append(os.path.realpath(os.path.expanduser(text)))
    for form in forms:
        canon = canon_path(form)
        if canon and (target_is_governance_root(form) or any(m in canon for m in TRUST_ROOT_MARKERS)):
            return True
    return False


def refusal_reason(record: dict[str, Any]) -> str | None:
    """Return why an entry can never be honoured, even validly signed, or ``None``.

    Args:
        record: An entry (shape already checked).

    Returns:
        A reason, or None.
    """
    texts = [record["target"]]
    for m in record["members"]:
        if any(i.upper().startswith("MAL-") for i in m["ids"]):
            return f"{m['name']}: a MAL-* (malicious package) advisory is never a known finding"
        if m["name"].lower() in SELF_PACKAGES:
            return f"{m['name']}: a finding on the agentteams/vulnscan install itself is never known"
        texts += [m["name"], m["detail"]]
    hit = next((t for t in texts if t and is_trust_root(t)), None)
    if hit is not None:
        return f"{hit!r} names a trust root (signing store, roster, secrets, integrity manifest, constitution, " \
               "grants/ledger, the known index or store, the decisions log, the vulnscan install)"
    return None


def effect_row(record: dict[str, Any]) -> dict[str, str]:
    """Return the decision-log-shaped row the effect classifier derives an entry's class from.

    The effect is structured, not self-declared: the entry relaxes the exit-code gate on its target and
    members.

    Args:
        record: An entry.

    Returns:
        A string-valued row.
    """
    targets = [record["target"]] + [f"{m['ecosystem']}/{m['name']}" if m["ecosystem"] else m["name"]
                                    for m in record["members"]]
    return {"date": str(record["issued"])[:10], "action_reviewed": ACTION_ID,
            "scope": f"{ACTION_ID} {record['target']}", "effect_relaxes": "vulnscan-exit-code",
            "effect_targets": ";".join(t.replace(";", " ").replace(",", " ") for t in targets)}


def derived_class(record: dict[str, Any]) -> str:
    """Return the entry's derived effect class, refusing a non-eligible or non-relaxing derivation.

    Args:
        record: An entry.

    Returns:
        ``relaxing`` or ``indeterminate`` (both take the operator-signed path).

    Raises:
        KnownError: The classifier refuses the effect, or derives it benign (the model here would be wrong).
    """
    from agentteams.cli import effect_classifier as ec

    row = effect_row(record)
    why = ec.non_eligibility_reason(row, kind="decision")
    if why:
        raise KnownError(f"non-eligible effect: {why}")
    try:
        cls = ec.classify_row(row, kind="decision")
    except ec.EffectClassifierError as exc:
        raise KnownError(str(exc)) from exc
    if cls == ec.NON_RELAXING or not ec.requires_operator_signature(row, kind="decision"):
        raise KnownError("the effect classifier does not derive this entry as relaxing; refusing (fail closed)")
    return cls


# ---------------------------------------------------------------- build, sign, verify

def member_from_finding(f: Finding) -> dict[str, Any]:
    """Return the member that keys on ``f`` exactly, fix state and severity included.

    Args:
        f: A finding from a run's report.

    Returns:
        The member.

    Raises:
        KnownError: The finding can never be known.
    """
    if f.malicious or f.kind not in MEMBER_KINDS:
        raise KnownError(f"{f.name}: a {'malicious' if f.malicious else f.kind} finding can never be known")
    host_check = f.kind == "host" and f.name == "host"
    m = {"kind": f.kind, "ecosystem": f.ecosystem, "name": f.name, "version": f.version,
         "ids": sorted(set(f.ids)), "detail": f.detail if host_check else "", "evidence": f.evidence or "",
         "fix": f.fix or "", "severity": int(f.severity), "kev": bool(f.kev)}
    return _check_member(m)


def build(findings: list[Finding], *, reason: str, decision_ref: dict[str, str], days: int, key_id: str,
          governance_dir: str, renews: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Build an unsigned entry from findings of one target, enforcing every bound and refusal.

    One finding makes a ``package`` (or ``host``) entry; several make a ``group`` entry that signs them all.

    Args:
        findings: The findings to accept (same target).
        reason: Why (at most :data:`REASON_MAX_CHARS`).
        decision_ref: ``{"plan_slug", "step"}`` of the decision-log row that cleared it.
        days: Lifetime, 1..:data:`MAX_DAYS`.
        key_id: Operator verify-key id.
        governance_dir: Team dir holding the decision log and the index.
        renews: The signed entry this one renews (its lineage is extended), or None.
        now: Clock override (tests).

    Returns:
        The unsigned record.

    Raises:
        KnownError: Anything is out of bounds or refused.
    """
    now = (now or utc_now()).replace(microsecond=0)
    if not findings:
        raise KnownError("no findings to accept")
    targets = {f.target for f in findings}
    if len(targets) != 1:
        raise KnownError("an entry covers findings of one target")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise KnownError(f"days must be 1..{MAX_DAYS}")
    members = sorted((member_from_finding(f) for f in findings),
                     key=lambda m: (*member_identity(m), m["version"]))
    kind = "group" if len(members) > 1 else ("host" if members[0]["kind"] == "host" else "package")
    lineage: list[str] = []
    if renews is not None:
        lineage = list(renews["lineage"]) + [renews["entry_id"]]
    gov = str(Path(governance_dir).expanduser().resolve()) if governance_dir else ""
    record = {"entry_id": "vk-" + secrets.token_hex(8), "kind": kind, "target": targets.pop(), "members": members,
              "reason": (reason or "").strip(), "decision_ref": {"plan_slug": decision_ref.get("plan_slug", ""),
                                                                 "step": decision_ref.get("step", "")},
              "issued": now.isoformat(), "expires": (now + timedelta(days=days)).isoformat(), "key_id": key_id,
              "governance_dir": gov, "renews": lineage[-1] if lineage else "", "lineage": lineage,
              "renewal_count": len(lineage)}
    check_unsigned(record)
    if renews is not None and (renews["target"] != record["target"]
                               or {member_identity(m) for m in renews["members"]}
                               != {member_identity(m) for m in members}):
        raise KnownError("a renewal covers the same target and members as the entry it renews")
    return record


def check_unsigned(record: dict[str, Any]) -> str:
    """Check an unsigned (or signed) record's shape, refusals and derived class.

    Args:
        record: The record.

    Returns:
        The derived class.

    Raises:
        KnownError: Anything is wrong.
    """
    _check_shape(record)
    why = refusal_reason(record)
    if why:
        raise KnownError(f"refused: {why}")
    gov = Path(record["governance_dir"])
    if not (gov / DECISIONS_REL).is_file():
        raise KnownError(f"governance_dir must be a team directory containing {DECISIONS_REL}")
    return derived_class(record)


def signed_values(record: dict[str, Any]) -> list[str]:
    """Return the exact Ed25519 payload: purpose tag + canonical JSON of every bound field.

    Args:
        record: An entry.

    Returns:
        Field list for ``signed_ledger.ed25519_sign``/``ed25519_verify``.

    Raises:
        KeyError: A bound field is missing.
    """
    body = {name: record[name] for name in FIELDS}
    return [PURPOSE_TAG, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def body_sha256(record: dict[str, Any]) -> str:
    """Return the sha256 the index records for a signed entry (its bound fields and signature).

    Args:
        record: A signed entry.

    Returns:
        Lowercase hex.
    """
    body = {name: record[name] for name in (*FIELDS, "signature")}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def remint(record: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return ``record`` with ``issued`` set to now and ``expires`` moved by the same lifetime.

    A proposal is signed later than it was written; the operator signs the window that actually starts then.

    Args:
        record: An unsigned proposal.
        now: Clock override (tests).

    Returns:
        A new record.
    """
    now = (now or utc_now()).replace(microsecond=0)
    life = datetime.fromisoformat(record["expires"]) - datetime.fromisoformat(record["issued"])
    return {**record, "issued": now.isoformat(), "expires": (now + life).isoformat()}


def sign(record: dict[str, Any], *, confirm_sha256: str | None = None,
         allow_checkout: bool = False) -> dict[str, Any] | None:
    """Sign an entry through the operator flow (:func:`.grant.operator_sign`). Operator only, never an agent.

    Args:
        record: Unsigned entry (checked again here).
        confirm_sha256: Exact review digest, for non-terminal confirmation.
        allow_checkout: ``--allow-checkout-signing``.

    Returns:
        The signed record, or None after printing why nothing was signed.

    Raises:
        KnownError: The record is refused.
    """
    from agentteams.vulnscan import grant

    check_unsigned(record)
    return grant.operator_sign(
        record, digest_kind="vulnscan-known-entry", fields=FIELDS, values=signed_values,
        banner="About to sign a KNOWN-FINDING entry. Until it expires or voids, these findings leave the exit "
               "code\n(they stay in the report). Check every member, the reason and the decision reference:",
        confirm_sha256=confirm_sha256, allow_checkout=allow_checkout)


def verify(record: dict[str, Any], *, now: datetime | None = None) -> str:
    """Verify one entry's shape, refusals, derived class, lifetime and Ed25519 signature.

    Args:
        record: Entry as read from disk.
        now: Clock override (tests).

    Returns:
        The derived class.

    Raises:
        KnownError: Anything is wrong (fail closed). ``expired`` starts the message of an expired entry.
    """
    from agentteams.cli.signed_ledger import ed25519_verify
    from agentteams.vulnscan import grant

    now = now or utc_now()
    if not isinstance(record, dict) or "signature" not in record:
        raise KnownError("unsigned entry")
    cls = check_unsigned(record)
    issued = datetime.fromisoformat(record["issued"])
    expires = datetime.fromisoformat(record["expires"])
    if now >= expires:
        raise KnownError(f"expired {record['expires']}")
    if now < issued:
        raise KnownError(f"not valid yet (issued {record['issued']})")
    sig = str(record["signature"])
    if not re.fullmatch(r"[0-9a-f]{128}", sig):
        raise KnownError("not an Ed25519 signature (a symmetric/HMAC signature is never accepted)")
    try:
        ok = ed25519_verify(grant.verify_key(record["key_id"]), signed_values(record), sig)
    except (RuntimeError, ValueError) as exc:   # GrantError is a ValueError
        raise KnownError(f"cannot verify: {exc}") from exc
    if not ok:
        raise KnownError("signature does not verify (tampered, or signed for another purpose)")
    return cls


def install(record: dict[str, Any]) -> Path:
    """Write a signed entry into the store (mode 600, never over an existing file).

    Args:
        record: Signed entry.

    Returns:
        The file path.

    Raises:
        KnownError: Unsigned or malformed record.
        FileExistsError: An entry with this id exists.
    """
    if not ENTRY_ID_RE.match(str(record.get("entry_id", ""))) or not record.get("signature"):
        raise KnownError("refusing to install an unsigned or malformed entry")
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"{record['entry_id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    return path


def remove(entry_id: str) -> bool:
    """Delete an entry from the store (a narrowing; no signature needed).

    Args:
        entry_id: Entry to remove.

    Returns:
        Whether a file was removed.
    """
    if not ENTRY_ID_RE.match(entry_id):
        return False
    path = store_dir() / f"{entry_id}.json"
    if path.is_file() and not path.is_symlink():
        path.unlink()
        return True
    return False


def index_row(record: dict[str, Any], *, today: str | None = None) -> dict[str, str]:
    """Return the index row that puts a signed entry in force once committed.

    Args:
        record: Signed entry.
        today: Date override (tests).

    Returns:
        ``{column: value}`` for :data:`INDEX_HEADER`.
    """
    return {"entry_id": record["entry_id"], "body_sha256": body_sha256(record), "renews": record["renews"],
            "renewal_count": str(record["renewal_count"]), "added": today or utc_now().date().isoformat()}


def append_index_row(record: dict[str, Any]) -> Path:
    """Append the entry's row to its governance dir's index (the operator commits it afterwards).

    Args:
        record: Signed entry.

    Returns:
        The index path.

    Raises:
        KnownError: The index exists with another header, or is a symlink.
    """
    path = Path(record["governance_dir"]) / INDEX_REL
    if path.is_symlink():
        raise KnownError(f"{path} is a symlink")
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh), [])
        if tuple(header) != INDEX_HEADER:
            raise KnownError(f"{path} does not start with the header {','.join(INDEX_HEADER)}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(",".join(INDEX_HEADER) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=INDEX_HEADER, lineterminator="\n").writerow(index_row(record))
    return path


def read_entry(path: Path) -> dict[str, Any]:
    """Read one entry file safely (no symlink; a regular file you own, not group/world-writable).

    Args:
        path: ``<store>/<entry_id>.json``.

    Returns:
        The record, unverified.

    Raises:
        KnownError: Unsafe file, not an object, or the id does not match the file name.
        OSError: The file cannot be opened (a symlink included).
        ValueError: Not JSON.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # a FIFO opens at once and is refused below
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise KnownError("not a regular file owned by you and not group/world-writable")
        raw = fh.read(1 << 20)
    rec = json.loads(raw.decode("utf-8"))
    if not isinstance(rec, dict):
        raise KnownError("not a JSON object")
    if rec.get("entry_id") != path.stem:
        raise KnownError("entry_id does not match the file name")
    return rec
