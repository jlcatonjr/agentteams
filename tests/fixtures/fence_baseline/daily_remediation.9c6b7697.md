<!-- AGENTTEAMS:BEGIN daily_remediation v=2 -->
## Daily Remediation workflow

1. **Read.** `get_summary`, then `list_findings` (`min_severity: "moderate"` first) and
   `get_fix_plans`. Check `feed_errors`: an unscanned target is unknown, not clean. Say so in your
   report.
2. **Triage, in order:**
   - malicious or IOC (stop conditions);
   - critical or KEV-listed **runtime** dependencies;
   - high;
   - moderate.

   Deadlines follow Rule S-VM: 24 h, 7 days and 30 days. Dev-only findings come after shipped
   ones. OS-managed (`distro`) findings belong to the host's security updates, not to a repo fix.
3. **Repositories.** For each repo whose fix plan contains a `fix` action:
   - request `stage-fix`, giving as target exactly the path in that plan's ``stage-fix target`` heading
     (the repository itself, never a folder that contains it);
   - when `request_status` shows `done`, request `open-pr` for the same repo.

   Rationale must name the advisory ids and severity. Skip every row whose action is not `fix`
   (`major-held`, `cooldown-held`, `no-clean-release`, `hash-held`, `workspace-held`,
   `npm-unavailable`; `malicious` is a stop condition), and list them for the operator with the reason.
4. **Servers.** Request `server-audit` for hosts with host findings or that are stale. Request
   `server-dryrun` for hosts with HIGH host findings, so the operator gets a ready patch plan.
5. **Follow up.** Poll `request_status` until each request is `done`, `failed` or `refused`. Do not
   re-request something that was refused for lack of a grant.
6. **Report** in the format below and submit it with `submit_report`. It is the record of this
   unattended run that the operator's after-action review collects. Submit once, at the end; a
   later submission replaces an earlier one.
<!-- AGENTTEAMS:END daily_remediation -->
