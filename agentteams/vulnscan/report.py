"""Findings, reports, exit codes and day-over-day diffs.

Exit codes are shared by every subcommand:

| Code | Meaning |
|---|---|
| ``0`` | Nothing at or above the threshold |
| ``1`` | Findings at or above ``--fail-on`` |
| ``3`` | A feed failed, so part of the target is **unscanned** (never reported as clean) |
| ``4`` | A malicious package version or a compromise indicator is present |
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field

from agentteams.vulnscan import REPORT_SCHEMA_VERSION
from agentteams.vulnscan.osv import SEVERITY_NAME, SEVERITY_RANK

EXIT_CLEAN, EXIT_FINDINGS, EXIT_UNSCANNED, EXIT_COMPROMISE = 0, 1, 3, 4


@dataclass
class Finding:
    """One problem: a vulnerable package, a stale runtime, or a host-level check."""

    kind: str                       # "package" | "runtime" | "host" | "ioc"
    target: str                     # repo (relative to scan root) or host name
    name: str
    version: str = ""
    ecosystem: str = ""
    scope: str = "runtime"          # "runtime" | "dev"
    severity: int = 0
    fix: str = ""                   # minimal upgrade target ("" = none known)
    unfixed: int = 0                # advisories with no fixed version
    ids: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    malicious: bool = False
    distro: bool = False            # managed by the OS package manager
    detail: str = ""

    def key(self) -> str:
        """Return a stable identity used to diff one run against the previous one.

        A new advisory against an already-flagged version, or a new host-check message,
        changes the key, so it is reported as NEW.

        Returns:
            ``target|kind|ecosystem|name|version|advisory ids or detail``.
        """
        tail = ",".join(sorted(self.ids)) if self.ids else self.detail
        return "|".join((self.target, self.kind, self.ecosystem, self.name.lower(), self.version, tail))


@dataclass
class Report:
    """Result of one scan (repo tree, server inventory, or a daily aggregate)."""

    target: str
    findings: list[Finding] = field(default_factory=list)
    unscannable: list[tuple[str, str]] = field(default_factory=list)   # (path, reason)
    feed_errors: list[str] = field(default_factory=list)
    checked: int = 0
    generated_at: str = field(default_factory=lambda: _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat())

    def merge(self, other: Report) -> None:
        """Fold another report into this one (used by the daily aggregate).

        Args:
            other: Report to absorb.
        """
        self.findings += other.findings
        self.unscannable += other.unscannable
        self.feed_errors += other.feed_errors
        self.checked += other.checked

    def exit_code(self, fail_on: str = "high", include_dev: bool = False) -> int:
        """Return the shared exit code for this report.

        Args:
            fail_on: Lowest severity that fails: critical|high|moderate|low|any|none.
            include_dev: Count dev-scope package findings toward the threshold.

        Returns:
            One of the module's EXIT_* codes.
        """
        if any(f.malicious or f.kind == "ioc" for f in self.findings):
            return EXIT_COMPROMISE
        if self.feed_errors:
            return EXIT_UNSCANNED
        if fail_on == "none":
            return EXIT_CLEAN
        floor = 0 if fail_on == "any" else SEVERITY_RANK.get(fail_on.upper(), 3)
        for f in self.findings:
            if f.distro or (f.scope == "dev" and not include_dev):
                continue
            if f.severity >= floor:
                return EXIT_FINDINGS
        return EXIT_CLEAN

    def to_dict(self) -> dict:
        """Return a JSON-serialisable form.

        Returns:
            The report as plain data, tagged with the schema version.
        """
        d = asdict(self)
        d["schema_version"] = REPORT_SCHEMA_VERSION
        d["unscannable"] = [{"path": p, "reason": r} for p, r in self.unscannable]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Report:
        """Rebuild a report written by :meth:`to_dict`.

        Args:
            data: Decoded JSON.

        Returns:
            The report.
        """
        rep = cls(target=data.get("target", ""), checked=int(data.get("checked", 0)),
                  feed_errors=list(data.get("feed_errors", [])),
                  generated_at=data.get("generated_at", ""))
        rep.unscannable = [(u["path"], u["reason"]) for u in data.get("unscannable", [])]
        known = Finding.__dataclass_fields__
        rep.findings = [Finding(**{k: v for k, v in f.items() if k in known}) for f in data.get("findings", [])]
        return rep

    def markdown(self, new_keys: set[str] | None = None) -> str:
        """Render the report for people.

        Args:
            new_keys: Finding keys absent from the previous run; marked **NEW**.

        Returns:
            Markdown text.
        """
        out = [f"# Vulnerability report — {self.target}", "",
               f"Generated {self.generated_at}. {self.checked} items checked; "
               f"{len(self.findings)} findings.", ""]
        if any(f.malicious or f.kind == "ioc" for f in self.findings):
            out += ["> **COMPROMISE INDICATORS / MALICIOUS PACKAGES PRESENT.** Do not upgrade in place: "
                    "isolate the machine, rebuild it, and rotate every credential it could reach.", ""]
        if self.feed_errors:
            out += ["> **Partially unscanned** — a feed failed; absence of findings proves nothing for:", ""]
            out += [f"> - {e}" for e in self.feed_errors] + [""]
        groups: dict[str, list[Finding]] = {}
        for f in sorted(self.findings, key=lambda f: (f.target, -f.severity, f.kind, f.name.lower())):
            groups.setdefault(f.target, []).append(f)
        for target, items in groups.items():
            out += [f"## {target}", "", "| | Severity | Kind | Item | Installed | Upgrade to | Notes |",
                    "|---|---|---|---|---|---|---|"]
            for f in items:
                mark = "**NEW**" if new_keys is not None and f.key() in new_keys else ""
                sev = "**MALICIOUS**" if f.malicious else SEVERITY_NAME.get(f.severity, "?")
                item = f"{f.name} ({f.ecosystem})" if f.ecosystem else f.name
                notes = []
                if f.scope == "dev":
                    notes.append("dev-only")
                if f.distro:
                    notes.append("OS-managed: apply OS security updates")
                if f.unfixed:
                    notes.append(f"{f.unfixed} advisory(ies) without a fix")
                if f.ids:
                    notes.append(f"{len(f.ids)}: {', '.join(f.ids[:3])}{'…' if len(f.ids) > 3 else ''}")
                if f.detail:
                    notes.append(f.detail)
                if f.locations:
                    notes.append("at " + ", ".join(f"`{loc}`" for loc in f.locations[:2]))
                out.append(f"| {mark} | {sev} | {f.kind} | {item} | {f.version} | {f.fix or '—'} | {'; '.join(notes)} |")
            out.append("")
        if self.unscannable:
            out += ["## Not scannable (no exact versions)", ""]
            out += [f"- `{p}` — {r}" for p, r in self.unscannable] + [""]
        return "\n".join(out)


def new_finding_keys(current: Report, previous: Report | None) -> set[str]:
    """Return keys of findings present now but not in ``previous``.

    Args:
        current: This run.
        previous: The prior run, or None on the first run (everything is new).

    Returns:
        Set of :meth:`Finding.key` values.
    """
    before = {f.key() for f in previous.findings} if previous else set()
    return {f.key() for f in current.findings} - before
