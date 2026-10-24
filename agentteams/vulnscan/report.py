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

from agentteams.vulnscan import REPORT_SCHEMA_VERSION, osv
from agentteams.vulnscan.osv import SEVERITY_NAME, SEVERITY_RANK, UNSCORED_AS

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
    severity: int = 0               # effective rank 0..4 (an unscored advisory counts as the run's unscored_as)
    fix: str = ""                   # minimal upgrade target ("" = none known)
    unfixed: int = 0                # advisories with no fixed version
    ids: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    malicious: bool = False
    distro: bool = False            # managed by the OS package manager
    count_dev: bool | None = None   # dev scope: the project's include_dev (None = the run's setting)
    detail: str = ""
    rated: int = 0                  # highest rank the advisories publish (CVSS or label); 0 = none of them does
    unscored: int = 0               # advisories with neither a CVSS vector nor a severity label
    malformed: int | None = None    # advisories whose record had malformed parts (skipped); None: from osv.quirks
    fix_hidden: int | None = None   # advisories whose fix version has an unusual format (not shown, counted unfixed)

    def __post_init__(self) -> None:
        """Count a new package finding's malformed advisories and hidden fix versions (R8, :func:`osv.quirks`).

        Left unset (None), both come from what :func:`osv.minimal_fix` recorded for this package version in
        this process; a report read back (:meth:`Report.from_dict`) keeps its own counts (0 for an older one).
        """
        if self.malformed is None or self.fix_hidden is None:
            m, h = osv.quirks(self.ids, self.ecosystem, self.name, self.version) if self.kind == "package" else (0, 0)
            self.malformed = m if self.malformed is None else self.malformed
            self.fix_hidden = h if self.fix_hidden is None else self.fix_hidden

    def shows_unscored(self) -> bool:
        """Return whether the severity comes from ``unscored_as`` (no advisory publishes one as high).

        Returns:
            True for a non-malicious package finding whose unscored advisories set its severity.
        """
        return bool(self.unscored) and not self.malicious and self.rated < self.severity

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
    opted_out: list[str] = field(default_factory=list)   # repos the operator enrolled at level off (not scanned)
    excluded: list[str] = field(default_factory=list)    # "repo: path": manifests matching a brief's exclude_paths
    withheld: list[str] = field(default_factory=list)    # "repo: name (ecosystem)": private, not checked
    brief_off: list[str] = field(default_factory=list)   # repos whose brief says off, scanned by operator enrolment
    unscored_as: str = "high"                            # what an advisory without any severity counts as
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
        self.opted_out += other.opted_out
        self.excluded += other.excluded
        self.withheld += other.withheld
        self.brief_off += other.brief_off

    def apply_unscored(self, level: str) -> None:
        """Count every unscored advisory as ``level`` (the config's ``unscored_as``), never as harmless.

        A package finding's severity becomes the higher of what its advisories publish and ``level`` when
        any advisory is unscored; malicious findings stay critical.

        Args:
            level: ``critical``, ``high``, ``moderate`` or ``low``.

        Raises:
            ValueError: An unknown level.
        """
        if level not in UNSCORED_AS:
            raise ValueError(f"unscored_as must be one of {', '.join(UNSCORED_AS)} (got {level!r})")
        self.unscored_as = level
        for f in self.findings:
            if f.kind == "package" and f.unscored and not f.malicious:
                f.severity = max(f.rated, UNSCORED_AS[level])

    def unscored_findings(self) -> list[Finding]:
        """Return the findings whose severity comes from ``unscored_as`` (listed separately).

        Returns:
            Those findings, in report order.
        """
        return [f for f in self.findings if f.shows_unscored()]

    def policy_counts(self) -> dict[str, tuple[int, int]]:
        """Return, per repository, how many manifests its brief excluded and how many packages it withheld.

        Returns:
            ``{repo: (excluded manifests, withheld private packages)}``, sorted by repo.
        """
        counts: dict[str, list[int]] = {}
        for i, labels in enumerate((self.excluded, self.withheld)):
            for label in labels:
                counts.setdefault(label.partition(": ")[0], [0, 0])[i] += 1
        return {k: (v[0], v[1]) for k, v in sorted(counts.items())}

    def exit_code(self, fail_on: str = "high", include_dev: bool = False) -> int:
        """Return the shared exit code for this report.

        Args:
            fail_on: Lowest severity that fails: critical|high|moderate|low|any|none.
            include_dev: Count dev-scope package findings toward the threshold (a finding's
                ``count_dev``, set from its project's brief, overrides it).

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
            counts_dev = include_dev if f.count_dev is None else f.count_dev
            if f.distro or (f.scope == "dev" and not counts_dev):
                continue
            if f.severity >= floor:
                return EXIT_FINDINGS
        return EXIT_CLEAN

    def to_dict(self, *, private_names: bool = True) -> dict:
        """Return a JSON-serialisable form.

        Args:
            private_names: False replaces each withheld package's name with ``(private)`` (the count per
                repository stays): ``summary.json`` is read by the agent's MCP server, so it never names them.

        Returns:
            The report as plain data, tagged with the schema version.
        """
        d = asdict(self)
        if not private_names:
            d["withheld"] = [f"{label.partition(': ')[0]}: (private)" for label in self.withheld]
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
        level = data.get("unscored_as", "high")
        rep.unscored_as = level if level in UNSCORED_AS else "high"
        rep.unscannable = [(u["path"], u["reason"]) for u in data.get("unscannable", [])]
        rep.opted_out, rep.excluded, rep.withheld, rep.brief_off = (
            [str(i) for i in data.get(k, [])] for k in ("opted_out", "excluded", "withheld", "brief_off"))
        known = Finding.__dataclass_fields__
        rep.findings = [Finding(**{"malformed": 0, "fix_hidden": 0, **{k: v for k, v in f.items() if k in known}})
                        for f in data.get("findings", [])]   # an older report counted neither (R8)
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
        unscored = self.unscored_findings()
        if unscored:
            out += [f"> **{len(unscored)} finding(s) UNSCORED**: no advisory gives a CVSS vector or a severity label; "
                    f"each counts as {self.unscored_as.upper()} (`unscored_as`) — see *Unscored advisories*.", ""]
        malformed = sum(f.malformed or 0 for f in self.findings)
        if malformed:
            out += [f"> {malformed} advisory record(s) from OSV were malformed: their unreadable parts were skipped, "
                    "so a fix may be missing (see each finding's notes).", ""]
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
                sev = "**MALICIOUS**" if f.malicious else (
                    f"UNSCORED (as {SEVERITY_NAME.get(f.severity, '?')})" if f.shows_unscored()
                    else SEVERITY_NAME.get(f.severity, "?"))
                item = f"{f.name} ({f.ecosystem})" if f.ecosystem else f.name
                notes = []
                if f.scope == "dev":
                    notes.append("dev-only" + {True: " (counted: project brief)",
                                               False: " (not counted: project brief)"}.get(f.count_dev, ""))
                if f.distro:
                    notes.append("OS-managed: apply OS security updates")
                if f.unfixed - (f.fix_hidden or 0) > 0:
                    notes.append(f"{f.unfixed - (f.fix_hidden or 0)} advisory(ies) without a fix")
                if f.fix_hidden:
                    notes.append(f"{f.fix_hidden} advisory(ies): fix version not shown (unusual format)")
                if f.malformed:
                    notes.append(f"{f.malformed} advisory record(s) malformed (unreadable parts skipped)")
                if f.unscored and not f.malicious:
                    notes.append(f"{f.unscored} advisory(ies) unscored")
                if f.ids:
                    notes.append(f"{len(f.ids)}: {', '.join(f.ids[:3])}{'…' if len(f.ids) > 3 else ''}")
                if f.detail:
                    notes.append(f.detail)
                if f.locations:
                    notes.append("at " + ", ".join(f"`{loc}`" for loc in f.locations[:2]))
                out.append(f"| {mark} | {sev} | {f.kind} | {item} | {f.version} | {f.fix or '—'} | {'; '.join(notes)} |")
            out.append("")
        if unscored:
            out += ["## Unscored advisories (no CVSS vector or severity label)", "",
                    f"Counted as {self.unscored_as.upper()} toward the exit threshold (`unscored_as`); "
                    "read the advisory to rate it.", ""]
            out += [f"- `{f.target}`: {f.name} {f.version} — {', '.join(f.ids[:5])}" for f in unscored] + [""]
        if self.unscannable:
            out += ["## Not scannable (no exact versions)", ""]
            out += [f"- `{p}` — {r}" for p, r in self.unscannable] + [""]
        private = [(repo, n) for repo, (_e, n) in self.policy_counts().items() if n]
        for title, items in (("Opted out (operator enrolment: level off)", [f"`{i}`" for i in self.opted_out]),
                             ("Brief says off; scanned by operator enrolment", [f"`{i}`" for i in self.brief_off]),
                             ("Excluded by the project brief (exclude_paths)", [f"`{i}`" for i in self.excluded]),
                             # names never appear here: this text reaches the agent (get_summary)
                             ("Not checked (private, per the project brief)",
                              [f"`{repo}`: {n} package(s)" for repo, n in private])):
            if items:
                out += [f"## {title}", ""] + [f"- {i}" for i in items] + [""]
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
