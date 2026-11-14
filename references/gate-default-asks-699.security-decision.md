# Security decision: constitutional gate default asks (remediation row 699)

Audit closeout for the `@security` review of the pinned gate template change on branch
`fix/gate-default-asks-699`. It is a tracked copy of the decision rows in the local, gitignored
`references/security-decisions.log.csv` (plan slug `gate-default-asks-699`).

## Scope

`git-force-push-short` and `git-alias-config` move from the #456 policy-only list into the gate's
default `ask` table. Class ids are unchanged. The re-pin touches only
`agentteams/templates/universal/hooks/constitutional-gate.py` in `references/enforcement-integrity.json`.

## Rulings (2026-11-14)

1. **CONDITIONAL PASS** on the pinned diff.
2. **Uppercase `-F` narrowing of `strict`: accepted as deliberate.** git push has no `-F`. `-f`, `-uf`,
   `-vf`, `-fu` and `+refspec` are still caught. It is the only loosening. It is recorded here because
   Rule 15's weaken check sees only a policy that drops a denied class, not a pattern narrowing.
3. **`git config --get/--list/-l` read exemption scoped to its own command: tightening only**
   (`publication-channel` twice, `git-alias-config`). Before, `git config -l; git config alias.p …`
   asked nothing.
4. **Line-wide matching kept.** Per-command scoping would admit an escaped separator (`a\;b -f`). The
   extra prompts, and a "force push" reason given for another command's `-f`, are documented and accepted.
5. **IM review findings F1 and F2, and the `@security` follow-up: fixed.**
   - A `+refspec` or a flag behind shell quoting now matches: `"+"main`, `+'main'`, `$'+main'`, `\+main`,
     `"-f"`, `"-c"`, `'--config-env=…'`.
   - `git --config-env=` is matched wherever `git -c` is (`git-alias-config` and `publication-channel`).
   - The prefix alternatives (`$`, quote, backslash) begin with distinct characters, so they add no
     backtracking. The length-cap timing test covers the new heads.
6. **Re-pin: cleared by `@security`.** It is a trust-root write and needs the operator's approval. The
   operator ruled, as relayed by IM lane C on 2026-11-14, that it is approved at the next dev→main
   release PR, as for `cd3553eb`. This condition stays open until that PR has green CI.

## Not in scope (remediation log, 1.3)

`core.hooksPath`, `core.sshCommand` and `credential.helper` config can also defeat or redirect a push.
These are deferred as non-blocking.
